"""Orchestrates one check.

  media prep (frames, audio)  ->  speech-to-text
  extraction over (caption + transcript)
  lanes A, B, C in parallel  ->  fusion

A failing stage never crashes a check: the lane is marked unavailable and the
verdict falls back towards amber ("can't verify").
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.extract.extractor import extract
from app.fusion.engine import fuse
from app.lanes.lane_a import run_lane_a
from app.lanes.lane_b import MediaEvidence, run_lane_b
from app.lanes.lane_c import run_lane_c
from app.media.docs import qr_lines, read_image, read_pdf
from app.media.prep import MediaError, extract_audio, extract_frames, probe
from app.media.stt import STTUnavailable, transcribe
from app.models import ClaimType, Lane, LaneResult, Signal, Strength, Verdict
from app.store.campaigns import ist_date

log = logging.getLogger("pramaan.pipeline")


@dataclass
class CheckInput:
    text: str = ""
    media_paths: list[Path] = field(default_factory=list)
    use_llm: bool = True
    language_hint: str | None = None   # e.g. "hi" if the user prefers Hindi


@dataclass
class CheckResult:
    verdict: Verdict
    timings: dict[str, float]
    media: list[MediaEvidence]
    sites: list[tuple] = field(default_factory=list)   # (SiteFacts, homepage screenshot bytes or None)


SITE_BUDGET_S = 18
MAX_SITES = 2


async def _look_at_sites(ex) -> list[tuple]:
    """Visit (safely) the homepage of up to two websites named in the message, with a screenshot."""
    from app.lanes import site_check
    if not site_check.enabled():
        return []
    urls = [cp.value for cp in ex.contact_points if cp.type.value == "url"][:MAX_SITES]
    out = []
    for u in urls:
        facts = await asyncio.to_thread(site_check.inspect, u)
        shot = None
        if not facts.skipped and facts.exists and facts.status == "ok" and facts.final_url:
            from app.render.card import renderer
            shot, html = await renderer.visit_site(facts.final_url)    # one visit: the picture, and the page as rendered
            if site_check.needs_render(facts):
                await asyncio.to_thread(site_check.absorb_rendered, facts, html)
        out.append((facts, shot))
    return out


def _own_hosts() -> set[str]:
    import os
    from urllib.parse import urlparse
    from app.config import get_settings
    hosts = {urlparse(get_settings().public_base_url).hostname or ""}
    hosts |= {h.strip().lower() for h in os.getenv("PRAMAAN_OWN_HOSTS", "").split(",")}
    return {h for h in hosts if h}


def _drop_own_seal_links(ex) -> None:
    """A sealed notice prints Pramaan's own verify link. That link is the seal (checked by _apply_seals), not a website
    the sender wants you to visit: visiting it would read the notice's own safety advice as a scam page."""
    import re
    from urllib.parse import urlparse
    own = _own_hosts()
    def is_own(v: str) -> bool:
        u = urlparse(v if "://" in v else "https://" + v)
        return (u.hostname or "").lower() in own and re.match(r"/s/[A-HJ-NP-Z2-9]{8}(?![A-Za-z0-9])", u.path or "", re.IGNORECASE) is not None
    ex.contact_points = [cp for cp in ex.contact_points if not (cp.type.value == "url" and is_own(cp.value))]


def _apply_seals(sources: list[tuple[str, bool]], lanes: dict) -> None:
    """Pramaan Seals found in the item (QR on a notice, verify line in an SMS): who signed it, and is the text unchanged?
    Each seal is compared with the text it was found in, so a caption like 'is this real?' does not count as an edit.
    sources: (text, came_from_ocr). OCR noise can look like an edit, so a photo only counts as 'changed' when it clearly is."""
    from app.store import seal
    a, b = lanes["A"], lanes["B"]
    seen = set()
    for src, from_ocr in sources:
        codes = seal.codes_in(src)
        for code in codes:
            if code in seen:
                continue
            rec = seal.load(code)
            if rec is None and from_ocr:
                # a misread code next to the real one (the QR gives it exactly), or one character off an issued code
                fix = seal.near_code(code, [c for c in codes if c != code and seal.load(c)]) or seal.near_code(code, seal.issued_codes())
                if fix:
                    code, rec = fix, seal.load(fix)
                    if code in seen:
                        continue
            seen.add(code)
            if rec is None:
                a.signals.append(Signal(code="SEAL_NOT_ISSUED", lane=Lane.A, strength=Strength.STRONG, params={"code": code}))
                continue
            pl = rec["payload"]
            who = seal.issuers().get(pl["iss"], {})
            common = {"code": code, "issuer": who.get("name", pl["iss"]), "issuer_hi": who.get("name_hi", who.get("name", pl["iss"])),
                      "issued": pl["issued"][:10], "title": pl["title"]}
            check = seal.verify_record(rec)
            if not check["valid"]:
                kind = "SEAL_REVOKED" if check["why"] == "revoked" else "SEAL_FORGED"
                a.signals.append(Signal(code=kind, lane=Lane.A, strength=Strength.STRONG, params=common | {"why": check["why"]}))
                continue
            cmp = seal.compare(rec["text"], src, tolerant=from_ocr)
            if cmp["same"]:
                a.signals.append(Signal(code="SEALED", lane=Lane.A, strength=Strength.POSITIVE, params=common | {"coverage": cmp["coverage"]}))
            else:
                changed = " · ".join(cmp["added_critical"]) or "some of the wording"
                strength = Strength.WEAK if from_ocr and cmp["coverage"] >= 0.6 else Strength.STRONG
                b.signals.append(Signal(code="SEAL_CONTENT_CHANGED", lane=Lane.B, strength=strength,
                                        params=common | {"changed": changed, "coverage": cmp["coverage"], "added": cmp["added_critical"],
                                                         "missing": cmp["missing_critical"], "from_ocr": from_ocr}))
    if not seen:
        return
    a.detail["seals"] = sorted(seen)
    if any(s.strength == Strength.STRONG for s in a.signals):
        a.status = "CONTRADICTED"
    elif any(s.code == "SEALED" for s in a.signals):
        a.status = "VERIFIED"
    if any(s.code == "SEAL_CONTENT_CHANGED" and s.strength == Strength.STRONG for s in b.signals):
        b.status = "SOURCE_MISMATCH"
    elif any(s.code == "SEAL_CONTENT_CHANGED" for s in b.signals) and b.status in ("NONE", "NOT_APPLICABLE"):
        b.status = "INDICATORS"


def _apply_official_original(lanes: dict) -> None:
    """A video whose footage AND words match a clip from an institution's registered official channel."""
    from app.lanes.source_match import load_index
    a, b = lanes["A"], lanes["B"]
    same = next((s for s in b.signals if s.code == "SOURCE_MATCH_SAME_WORDS"), None)
    if not same or any(s.strength == Strength.STRONG for s in a.signals + b.signals):
        return
    ref = next((r for r in load_index() if r.id == same.params.get("ref_id")), None)
    if ref is None or not getattr(ref, "official", None):
        return
    a.signals.append(Signal(code="OFFICIAL_ORIGINAL", lane=Lane.A, strength=Strength.POSITIVE,
                            params={"source_title": ref.title_en, "source_title_hi": ref.title_hi, "owner": ref.official}))
    a.status = "VERIFIED"


def _apply_sites(sites: list[tuple], ex, lanes: dict) -> None:
    from app.lanes.site_check import intent_signals, public_facts, signals_for
    a, c = lanes["A"], lanes["C"]
    if not a.available:
        return
    for facts, _ in sites:
        sigs = signals_for(facts, ex)
        a.signals.extend(sigs)
        if any(s.strength == Strength.STRONG for s in sigs):
            a.status = "CONTRADICTED"
        if c.available:   # what the website itself asks visitors to do is Lane C evidence, like the message's own ask
            asks = intent_signals(facts)
            c.signals.extend(asks)
            if any(s.strength == Strength.STRONG for s in asks):
                c.status = "HIGH"
            elif asks and c.status == "NONE":
                c.status = "LOW"
    a.detail["sites"] = [public_facts(f) for f, _ in sites]


def _prepare_media(path: Path, language_hint: str | None, timings: dict, workdir: Path) -> MediaEvidence:
    t = time.perf_counter()
    pr = probe(path)
    ev = MediaEvidence(kind=pr.kind, duration=pr.duration)
    if pr.kind in ("pdf", "image"):
        ev.doc = read_pdf(path) if pr.kind == "pdf" else read_image(path)
        timings["doc"] = timings.get("doc", 0) + time.perf_counter() - t
        return ev
    if pr.kind == "video":
        ev.frames = extract_frames(path)
    timings["frames"] = timings.get("frames", 0) + time.perf_counter() - t
    if pr.kind in ("video", "audio") and (pr.has_audio or pr.kind == "audio"):
        t = time.perf_counter()
        try:
            audio = extract_audio(path, workdir / f"{path.stem}_{abs(hash(str(path))) % 10**8}.mp3")
            ev.audio_path = audio
            ev.transcript = transcribe(audio, language=language_hint)

            def retranscribe(lang: str, _audio=audio) -> object:
                try:
                    return transcribe(_audio, language=lang)
                except STTUnavailable:
                    return None
            ev.retranscribe = retranscribe
        except (MediaError, STTUnavailable) as e:
            log.warning("no transcript for %s: %s", path.name, e)
        timings["stt"] = timings.get("stt", 0) + time.perf_counter() - t
    return ev


def _apply_campaign_memory(ex, lanes: dict) -> None:
    """Indicators of campaigns already CONFIRMED by bank analysts become strong signals
    (contact points -> Lane A, re-used footage / borrowed circulars -> Lane B)."""
    try:
        from app.store import campaigns
        provisional = {"lanes": {k: l.model_dump(mode="json") for k, l in lanes.items()}, "extraction": ex.model_dump(mode="json")}
        hits = campaigns.memory_hits(campaigns.indicators_from_verdict(provisional))
    except Exception:
        log.exception("campaign memory unavailable")
        return
    seen = set()
    for h in hits:
        lane = "B" if h["indicator"].startswith(("footage:", "circular:")) else "A"
        if (lane, h["id"]) in seen or not lanes[lane].available:
            continue
        seen.add((lane, h["id"]))
        lanes[lane].signals.append(Signal(code="CAMPAIGN_MEMORY_HIT", lane=Lane(lane), strength=Strength.STRONG,
                                          params={"campaign": h["id"], "title": h["title"], "indicator": h["indicator"].split(":", 1)[1],
                                                  "confirmed_on": ist_date(h["decided_at"])}))
        if lane == "A":
            lanes["A"].status = "CONTRADICTED"
        elif lanes["B"].status in ("NONE", "NOT_APPLICABLE", "INDICATORS"):
            lanes["B"].status = "SOURCE_MISMATCH"


async def run_check(inp: CheckInput) -> CheckResult:
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    media: list[MediaEvidence] = []
    workdir = Path(tempfile.mkdtemp(prefix="pramaan_check_"))
    if inp.media_paths:
        results = await asyncio.gather(*[asyncio.to_thread(_prepare_media, p, inp.language_hint, timings, workdir) for p in inp.media_paths],
                                       return_exceptions=True)
        for p, r in zip(inp.media_paths, results):
            if isinstance(r, Exception):
                log.warning("media prep failed for %s: %s", p, r)
                media.append(MediaEvidence(kind="unreadable"))
            else:
                media.append(r)
        timings["media_prep"] = time.perf_counter() - t0

    spoken = " ".join(m.transcript.text for m in media if m.transcript and m.transcript.text)
    written = "\n".join("\n".join(x for x in (m.doc.text, qr_lines(m.doc)) if x) for m in media if m.doc)
    full_text = "\n".join(x for x in (inp.text.strip(), spoken, written) if x)
    t = time.perf_counter()
    ex = await asyncio.to_thread(extract, full_text, inp.use_llm)
    timings["extract"] = time.perf_counter() - t
    _drop_own_seal_links(ex)

    async def timed(name: str, fn, *args) -> LaneResult:
        t = time.perf_counter()
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception:
            log.exception("lane %s failed", name)
            return LaneResult(lane=Lane(name), status="UNAVAILABLE", available=False)
        finally:
            timings[f"lane_{name}"] = time.perf_counter() - t

    t_site = time.perf_counter()
    site_task = asyncio.create_task(asyncio.wait_for(_look_at_sites(ex), SITE_BUDGET_S))   # runs alongside the lanes
    a, b, c = await asyncio.gather(timed("A", run_lane_a, ex), timed("B", run_lane_b, media, ex.text, any(c.type in (ClaimType.BANK, ClaimType.REGULATOR, ClaimType.NBFC) for c in ex.claims)), timed("C", run_lane_c, ex))
    shutil.rmtree(workdir, ignore_errors=True)   # raw audio is never kept after the check
    lanes = {"A": a, "B": b, "C": c}
    try:
        sites = await site_task
    except Exception as e:   # slow or unreachable sites never hold up or break a check
        log.warning("site check skipped: %s", type(e).__name__)
        sites = []
    timings["sites"] = time.perf_counter() - t_site
    _apply_sites(sites, ex, lanes)
    seal_sources = [("\n".join(x for x in (m.doc.text, qr_lines(m.doc)) if x), bool(m.doc.ocr_used)) for m in media if m.doc]
    seal_sources += [(inp.text, False), (spoken, True)]          # speech-to-text is as noisy as OCR
    _apply_seals([(s, o) for s, o in seal_sources if s and s.strip()], lanes)
    _apply_official_original(lanes)
    _apply_campaign_memory(ex, lanes)
    t = time.perf_counter()
    verdict = fuse(ex, lanes)
    timings["fuse"] = time.perf_counter() - t
    timings["total"] = time.perf_counter() - t0
    return CheckResult(verdict=verdict, timings=timings, media=media, sites=sites)


def check_text(text: str, use_llm: bool = True) -> Verdict:
    return asyncio.run(run_check(CheckInput(text=text, use_llm=use_llm))).verdict
