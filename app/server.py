"""Pramaan web server: check API, verdict cards, proof pages, card verification.

Run:  uvicorn app.server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import json
from html import escape as html_escape
import logging
import os
import re
import secrets
import shutil
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import DATA_DIR, STATIC_DIR, STORE_DIR, get_settings
from app.lanes.lane_c import load_playbooks
from app.lanes.source_match import load_index
from app.models import Verdict
from app.rails.registry import get_registry
from app.render.card import renderer, words_snippet
from app.service import GENUINE_DIR, Record, check_and_record
from app.guard import desk_role, enforce_rate, require_role, role_for_token
from app.store import db
from app.store.signing import key_id, public_key_pem, verify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("pramaan.server")
TEMPLATES = Path(__file__).parent / "render" / "templates"
env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
IST = timezone(timedelta(hours=5, minutes=30))


def _ist(iso: str | None) -> str:
    """Stored UTC time -> '2026-10-24 03:10 IST' for pages people read in India."""
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:16].replace("T", " ")
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return t.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")


env.filters["ist"] = _ist
MAX_UPLOAD = 25 * 1024 * 1024
_SAFE = re.compile(r"^[A-Za-z0-9_\-]{6,32}$")


def record_from_row(r: dict) -> Record:
    return Record(id=r["id"], serial=r["serial"], verdict=Verdict.model_validate(r["verdict"]), timings=r["proof"].get("timings", {}),
                  proof=r["proof"], media=r["media"], signature=r["signature"], payload=r["payload"])


async def _purge_loop() -> None:
    while True:
        try:
            n = await asyncio.to_thread(db.purge_expired)
            if n:
                log.info("purged media of %d expired checks", n)
            from app.media import live_ref
            n = (await asyncio.to_thread(db.purge_caches) + await asyncio.to_thread(db.purge_watchers)
                 + await asyncio.to_thread(live_ref.purge_expired))
            if n:
                log.info("purged %d expired cache entries, watchers and trust links", n)
        except Exception:
            log.exception("purge failed")
        await asyncio.sleep(900)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    try:
        await renderer.start()
    except Exception as e:   # cards fall back to text until Chromium is available; the rest of Pramaan still runs
        log.error("card renderer unavailable (run: python -m playwright install chromium): %s", str(e).splitlines()[0])
    tasks = [asyncio.create_task(_purge_loop())]
    from app.lanes import detectors
    if detectors.enabled():   # load the weak detectors in the background so the first video check is not slow
        async def _warm():
            from app.lanes import semantic
            for fn in (semantic.warm, detectors._video_model, detectors._audio_model):
                try:
                    await asyncio.to_thread(fn)
                except Exception as e:
                    log.warning("detector warm-up skipped: %s", e)
            log.info("detectors warm")
        tasks.append(asyncio.create_task(_warm()))
    if get_settings().telegram_bot_token and not os.getenv("PRAMAAN_DISABLE_BOT"):
        from app.channels.telegram import run_bot
        tasks.append(asyncio.create_task(run_bot()))
    yield
    for t in tasks:
        t.cancel()
    await renderer.close()


app = FastAPI(title="Pramaan", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
from app.routes_seal import router as seal_router   # noqa: E402  (needs `app` and `env` defined first)
from app.routes_protect import router as protect_router   # noqa: E402
app.include_router(seal_router)
app.include_router(protect_router)


async def json_body(request: Request) -> dict:
    """A JSON object from the request body, or {} (a malformed body is a 400, never a server error)."""
    if not request.headers.get("content-type", "").startswith("application/json"):
        return {}
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "The request body is not valid JSON.")
    if not isinstance(body, dict):
        raise HTTPException(400, "Send a JSON object.")
    return body


def _get(check_id: str) -> dict:
    if not _SAFE.match(check_id):
        raise HTTPException(404)
    r = db.get_check(check_id=check_id)
    if not r:
        raise HTTPException(404, "This check does not exist or has expired.")
    return r


# ------------------------------------------------------------------ check API
@app.post("/api/check")
async def api_check(request: Request, text: str = Form(""), lang: str = Form("en"), files: list[UploadFile] | None = File(None)):
    enforce_rate(request)
    return await _run_web_check(text, lang, files)


async def _run_web_check(text: str, lang: str, files: list[UploadFile] | None, channel: str = "web") -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="pramaan_up_"))
    try:
        paths = []
        for f in files or []:
            data = await f.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, "File too large (25 MB max).")
            if not data:
                continue
            suffix = Path(f.filename or "upload").suffix.lower()[:6] or ".bin"
            p = tmp / f"upload{len(paths)}{suffix}"
            p.write_bytes(data)
            paths.append(p)
        if not text.strip() and not paths:
            raise HTTPException(400, "Send a message, a file, or both.")
        rec = await check_and_record(text[:8000], paths, channel=channel, language_hint="hi" if lang == "hi" else None)
        return {"id": rec.id, "serial": rec.serial, "level": rec.verdict.level.value, "title_en": rec.verdict.title_en,
                "title_hi": rec.verdict.title_hi, "reasons": [r.model_dump() for r in rec.verdict.reasons],
                "proof_url": rec.proof_url, "card_url": f"/card/{rec.id}.png?lang={lang}", "verify_url": rec.verify_url}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.post("/share")
async def share_target(request: Request, title: str = Form(""), text: str = Form(""), url: str = Form(""), files: list[UploadFile] | None = File(None)):
    """Android 'Share -> Pramaan' (PWA share target): the same check as the bank app's Share button would run."""
    from fastapi.responses import RedirectResponse
    enforce_rate(request)
    joined = "\n".join(x for x in (title, text, url) if x)
    res = await _run_web_check(joined, "en", files)
    return RedirectResponse(f"/p/{res['id']}", status_code=303)


@app.get("/card/{check_id}.png")
async def card_png(check_id: str, lang: str = "en"):
    r = _get(check_id)
    lang = "hi" if lang == "hi" else "en"
    cache = STORE_DIR / check_id / f"card_{lang}.png"
    if not cache.exists():
        png = await renderer.render(record_from_row(r), lang)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(png)
    return FileResponse(cache, media_type="image/png")


# ---------------------------------------------------------------- proof page
def _rail_rows(v: Verdict) -> list[dict]:
    from app.explain.templates import reason_for
    rows = []
    for s in v.lanes["A"].signals:
        try:
            text = reason_for(s).text_en
        except Exception:
            text = None
        rows.append({"code": s.code, "strength": s.strength.value, "params": s.params, "text": text,
                     "source": s.provenance.model_dump() if s.provenance else None})
    return rows


@app.get("/p/{check_id}/golden-hour.txt")
async def golden_hour_packet(check_id: str):
    from fastapi.responses import PlainTextResponse
    from app.explain.complaint import full_packet
    rec = record_from_row(_get(check_id))
    return PlainTextResponse(full_packet(rec), headers={"Content-Disposition": f'attachment; filename="pramaan-golden-hour-{rec.serial}.txt"'})


@app.get("/p/{check_id}", response_class=HTMLResponse)
async def proof_page(check_id: str, lang: str = "en"):
    r = _get(check_id)
    rec = record_from_row(r)
    v = rec.verdict
    proof = rec.proof
    src = proof.get("source")
    _, pbs = load_playbooks()
    # the playbook the verdict names comes first, then the lane's own ranking (never file order)
    named = [s.params.get("playbook") for s in v.lanes["C"].signals if s.code == "PLAYBOOK_MATCH"]
    order = list(dict.fromkeys([*named, *(x["id"] for x in v.lanes["C"].detail.get("playbooks", []))]))
    by_id = {p.id: p for p in pbs}
    playbooks = [{"id": p.id, "name_en": p.name_en, "name_hi": p.name_hi, "summary_en": p.summary_en, "summary_hi": p.summary_hi, "sources": p.sources}
                 for p in (by_id[i] for i in order if i in by_id)]
    ctx = {
        "rec": rec, "v": v, "lang": lang, "issued": v.created_at.astimezone(IST).strftime("%d %b %Y, %H:%M IST"),
        "src": src, "diff": proof.get("diff"), "doc": proof.get("doc"), "words_snippet": words_snippet(proof.get("diff")),
        "query_media": f"/media/q/{rec.id}/{rec.media[0]}" if rec.media else None,
        "ref_media": f"/media/ref/{src['ref_id']}" if src else None,
        "rails": _rail_rows(v), "playbooks": playbooks,
        "intent": v.lanes["C"].detail, "trace": json.dumps({k: l.model_dump(mode="json") for k, l in v.lanes.items()}, ensure_ascii=False, indent=1)[:20000],
        "proof_json": json.dumps({"source": src, "diff": proof.get("diff"), "query_transcript": proof.get("query_transcript"),
                                   "ref_transcript": proof.get("ref_transcript"),
                                   "qflags": proof.get("qflags"), "rflags": proof.get("rflags")}, ensure_ascii=False),
        "verify_url": rec.verify_url, "key_id": key_id(),
        "extraction": v.extraction, "expired": bool(proof.get("expired")), "sites": proof.get("sites") or [],
        # Honest demo engineering: say whether this answer was computed live or partly from the cache
        "corrected": db.upheld_appeal(rec.id),
        "freshness": {"cached": "(cached)" in (v.extraction.extractor or ""), "seconds": (proof.get("timings") or {}).get("total"),
                      "at": v.created_at.astimezone(IST).strftime("%H:%M")},
    }
    return env.get_template("proof.html").render(**ctx)


@app.get("/media/q/{check_id}/{name}")
async def query_media(check_id: str, name: str):
    _get(check_id)
    if not re.match(r"^(?:m\d+\.[a-z0-9]{1,5}|doc_page\.jpg|thumb_(?:ref|query)\.jpg|site_\d\.jpg)$", name):
        raise HTTPException(404)
    p = STORE_DIR / check_id / name
    if not p.exists():
        raise HTTPException(410, "Media deleted after 24 hours.")
    return FileResponse(p)


@app.get("/media/ref/{ref_id}")
async def ref_media(ref_id: str):
    ref = next((x for x in load_index() if x.id == ref_id), None)
    if not ref or not (GENUINE_DIR / ref.file).exists():
        raise HTTPException(404)
    return FileResponse(GENUINE_DIR / ref.file, media_type="video/mp4")


# ------------------------------------------------------------- verification
@app.get("/v/{serial}", response_class=HTMLResponse)
async def verify_page(serial: str, s: str = ""):
    if not re.match(r"^PR-\d{6}-\d{4}$", serial):
        raise HTTPException(404)
    r = db.get_check(serial=serial)
    genuine = bool(r and s and s == r["signature"] and verify(r["payload"], s))
    rec = record_from_row(r) if r else None
    return env.get_template("verify.html").render(serial=serial, genuine=genuine, found=bool(r), rec=rec,
                                                  corrected=db.upheld_appeal(rec.id) if rec else None,
                                                  issued=rec.verdict.created_at.astimezone(IST).strftime("%d %b %Y, %H:%M IST") if rec else "",
                                                  key_id=key_id())


@app.get("/.well-known/pramaan-verdict-key.pem", response_class=PlainTextResponse)
async def pubkey():
    return public_key_pem()


# ------------------------------------------------------- report and Desk
def _desk_cookie(resp: Response, token: str, request: Request) -> Response:
    resp.set_cookie("desk", token, httponly=True, samesite="strict", secure=request.url.scheme == "https"
                    or request.headers.get("x-forwarded-proto") == "https", max_age=12 * 3600, path="/")
    return resp


@app.get("/desk/login", response_class=HTMLResponse)
async def desk_login_page(error: str | None = None):
    return env.get_template("desk_login.html").render(error=error)


@app.post("/desk/login")
async def desk_login(request: Request, token: str = Form("")):
    from fastapi.responses import RedirectResponse
    enforce_rate(request, "login")
    if role_for_token(token.strip()) is None:
        return HTMLResponse(env.get_template("desk_login.html").render(error="That token is not valid."), status_code=403)
    return _desk_cookie(RedirectResponse("/desk", status_code=303), token.strip(), request)


@app.get("/desk/logout")
async def desk_logout():
    from fastapi.responses import RedirectResponse
    resp = RedirectResponse("/desk/login", status_code=303)
    resp.delete_cookie("desk", path="/")
    return resp


@app.post("/api/report/{check_id}")
async def api_report(check_id: str, request: Request):
    enforce_rate(request, "report")
    _get(check_id)
    from app.service import report_check
    body = await json_body(request)
    return await asyncio.to_thread(report_check, check_id, "web", str(body.get("note", ""))[:500])


@app.post("/api/appeal/{check_id}")
async def api_appeal(check_id: str, request: Request):
    """'This looks wrong': anyone holding a card or proof link can ask a bank analyst to review the verdict."""
    enforce_rate(request, "appeal")
    _get(check_id)
    body = await json_body(request)
    aid = await asyncio.to_thread(db.add_appeal, check_id, str(body.get("note", "")))
    return {"appeal": aid, "status": "open"}


@app.post("/desk/seal/{code}/revoke")
async def desk_revoke_seal(code: str, request: Request):
    role = require_role(request, "lead")
    from app.store import seal, vault
    if not await asyncio.to_thread(seal.revoke, code, f"{role} (demo)"):
        raise HTTPException(409, "Unknown or already revoked.")
    vault.append_event("seal_revoked", {"code": code.upper()})
    return {"code": code.upper(), "revoked": True}


@app.post("/desk/appeal/{appeal_id}/{decision}")
async def desk_appeal(appeal_id: int, decision: str, request: Request):
    role = require_role(request, "analyst")
    if decision not in ("upheld", "dismissed"):
        raise HTTPException(404)
    body = await json_body(request)
    a = await asyncio.to_thread(db.decide_appeal, appeal_id, decision, f"{role} (demo)", str(body.get("note", "")))
    if a is None:
        raise HTTPException(409, "Already decided.")
    from app.store import vault
    vault.append_event(f"appeal_{decision}", {"appeal": appeal_id, "minutes": a["minutes"]})
    told = await tell_appeal_outcome(a)
    return {"appeal": appeal_id, "status": decision, "minutes": a["minutes"], "person_told": told}


APPEAL_TXT = {
    "upheld": ("✅ <b>Your appeal was upheld</b> ({serial}).\n\nA bank analyst reviewed the check and agrees it was wrong. "
               "The proof page now says so, and the case goes into the rule review.{note}",
               "✅ <b>आपकी अपील मान ली गई</b> ({serial})।\n\nबैंक विश्लेषक ने जाँच देखी और माना कि नतीजा ग़लत था। सबूत वाले पेज पर अब यही लिखा है।{note}"),
    "dismissed": ("⚖️ <b>Your appeal was reviewed</b> ({serial}).\n\nA bank analyst checked it and the verdict stands. "
                  "If you are sure the message is genuine, confirm it with the sender's official number before you act.{note}",
                  "⚖️ <b>आपकी अपील देखी गई</b> ({serial})।\n\nबैंक विश्लेषक ने जाँच की और नतीजा वही रहा। कुछ करने से पहले भेजने वाले के आधिकारिक नंबर पर पुष्टि करें।{note}"),
}


async def tell_appeal_outcome(a: dict) -> bool:
    """Send the analyst's answer to the person who appealed, if they asked to hear it."""
    if a.get("notify_channel") != "telegram" or not a.get("notify_chat"):
        return False
    from app.channels import telegram
    if telegram.BOT is None:
        return False
    row = db.get_check(check_id=a["check_id"])
    lang = db.get_lang(db.pseudonym("telegram", a["notify_chat"])) or "en"
    note = f"\n\n<i>{html_escape(a['note_out'])}</i>" if a.get("note_out") else ""
    msg = APPEAL_TXT[a["status"]][1 if lang == "hi" else 0].format(serial=row["serial"] if row else "", note=note)
    await telegram.BOT.send_text(a["notify_chat"], msg)
    return True


ALERT_TXT = {
    "en": ("⚠️ <b>Update on something you checked</b> ({serial}, {when})\n\nBank fraud analysts have now <b>confirmed it as part of a scam campaign</b>: "
           "{title}.\n\nDo not pay or share any code. If you already paid, call <b>1930</b> now. Warn anyone who sent it to you."),
    "hi": ("⚠️ <b>आपकी जाँची गई चीज़ पर अपडेट</b> ({serial}, {when})\n\nबैंक के धोखाधड़ी विश्लेषकों ने अब <b>इसे एक धोखाधड़ी अभियान का हिस्सा माना है</b>: "
           "{title}।\n\nभुगतान न करें, कोई कोड न बताएँ। अगर पैसे भेज चुके हैं तो तुरंत <b>1930</b> पर कॉल करें।"),
}


async def send_retro_alerts(campaign: dict) -> int:
    from app.channels import telegram
    from app.store import campaigns
    sent = 0
    for w in campaigns.watchers_to_alert(campaign["id"]):
        lang = db.get_lang(db.pseudonym(w["channel"], w["chat_id"])) or "en"
        when = w["checked_at"][:10]
        msg = ALERT_TXT["hi" if lang == "hi" else "en"].format(serial=w["serial"], when=when, title=campaign["title"])
        if w["channel"] == "telegram" and telegram.BOT is not None:
            await telegram.BOT.send_text(w["chat_id"], msg)
        db.mark_alerted(w["check_id"], w["channel"], w["chat_id"])
        sent += 1
    if sent:
        from app.store import vault
        vault.append_event("retro_alert", {"campaign": campaign["id"], "sent": sent})
    return sent


@app.get("/desk", response_class=HTMLResponse)
async def desk(request: Request, t: str | None = None):
    from fastapi.responses import RedirectResponse
    if t:   # old links with ?t=: set the cookie once, then move to a clean URL so the token never stays in history or logs
        if role_for_token(t.strip()) is None:
            raise HTTPException(403, "Pramaan Desk is for bank analysts. Sign in at /desk/login.")
        return _desk_cookie(RedirectResponse("/desk", status_code=303), t.strip(), request)
    role = desk_role(request)
    if role is None:
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/desk/login", status_code=303)
        raise HTTPException(403, "Pramaan Desk is for bank analysts. Sign in at /desk/login.")
    from app.store import campaigns, vault
    camps = await asyncio.to_thread(campaigns.list_campaigns)
    # Open campaigns first, then by how many people they reached and how often they were reported
    camps.sort(key=lambda c: (c["status"] != "open", -(c["people_seen"] or 0) * max(1, len(c["reports"])), c["first_seen"]))
    with db.conn() as c:
        alerts = c.execute("SELECT COUNT(*) FROM watchers WHERE alerted = 1").fetchone()[0]
    stats = {"open": sum(c["status"] == "open" for c in camps), "reports": sum(len(c["reports"]) for c in camps),
             "people_protected": sum(c["people_seen"] for c in camps if c["status"] == "confirmed"), "alerts": alerts}
    appeals = await asyncio.to_thread(db.list_appeals)
    compromise = await asyncio.to_thread(db.needs_analyst)
    from app.store import seal
    seals = await asyncio.to_thread(seal.list_seals)
    stats["appeals_open"] = sum(a["status"] == "open" for a in appeals)
    return HTMLResponse(env.get_template("desk.html").render(campaigns=camps, stats=stats, chain=vault.verify_chain(), role=role,
                                                             appeals=appeals, compromise=compromise, seals=seals,
                                                             analyst="Lead analyst (demo)" if role == "lead" else "FRM analyst (demo)"))


@app.post("/desk/{campaign_id}/{decision}")
async def desk_decide(campaign_id: str, decision: str, request: Request):
    require_role(request, "analyst")
    if decision not in ("confirmed", "rejected") or not re.match(r"^C-[0-9A-F]{6}$", campaign_id):
        raise HTTPException(404)
    from app.store import campaigns, vault
    body = await json_body(request)
    camp = campaigns.decide(campaign_id, decision, "FRM analyst (demo)", str(body.get("note", "")))
    if camp is None:
        raise HTTPException(404, "No such campaign.")
    vault.append_event(f"campaign_{decision}", {"campaign": campaign_id, "indicators": camp["indicators"]})
    sent = await send_retro_alerts(camp) if decision == "confirmed" else 0
    return {"campaign": campaign_id, "status": decision, "alerts_sent": sent}


@app.get("/desk/{campaign_id}/packet.json")
async def desk_packet(campaign_id: str, request: Request):
    require_role(request, "lead")   # sending anything outside the bank is a lead decision
    from app.store import campaigns
    from app.store.signing import sign
    try:
        pkt = campaigns.export_packet(campaign_id)
    except KeyError:
        raise HTTPException(404, "No such campaign.")
    pkt["signature"] = sign(pkt)
    return JSONResponse(pkt, headers={"Content-Disposition": f'attachment; filename="pramaan-{campaign_id}.json"'})


@app.get("/desk/chain")
async def desk_chain(request: Request):
    require_role(request, "analyst")
    from app.store import vault
    return vault.verify_chain()


@app.get("/lab", response_class=HTMLResponse)
async def lab():
    res = Path(__file__).resolve().parent.parent / "eval" / "results"
    load = lambda name: json.loads((res / name).read_text(encoding="utf-8")) if (res / name).exists() else None
    r, rob, base = load("latest.json"), load("robustness.json"), load("baseline-public-before-phase3.json")
    public_change = None
    if r and base:
        def counts(items):
            pub = [i for i in items if i.get("split") == "public" and i.get("label") == "scam"]
            return len(pub), sum("PLAYBOOK_MATCH" in (i.get("reasons") or []) for i in pub), sum((i.get("lane_status") or {}).get("C") == "HIGH" for i in pub)
        n0, named0, high0 = counts(base["items"])
        n1, named1, high1 = counts(r["items"])
        if n0 and n0 == n1:
            public_change = {"n": n1, "named_before": named0, "named_now": named1, "high_before": high0, "high_now": high1}
    ok_base, ok_now = load("open_kit-baseline-frozen.json"), load("open_kit.json")
    open_kit = {"base": ok_base, "now": ok_now if ok_now and ok_base and ok_now["generated_at"] != ok_base["generated_at"] else None} if ok_base else None
    return env.get_template("lab.html").render(r=r, rob=rob, public_change=public_change, appeals=await asyncio.to_thread(db.appeal_counts), open_kit=open_kit,
                                               ke_json=json.dumps(r["key_experiment"] if r else None, ensure_ascii=False))


# What a check caught, in words, for the red-team page (never the message itself).
CAUGHT = {
    "HIDDEN_INSTRUCTIONS": "hidden AI instructions", "DOMAIN_PUNYCODE": "look-alike letters in a link", "DOMAIN_ZONE_SPOOF": "fake .bank.in address",
    "DOMAIN_LOOKALIKE": "look-alike bank site", "SOURCE_MISMATCH": "same footage, different words", "DOC_SOURCE_MISMATCH": "borrowed RBI circular number",
    "DOC_REFERENCE_IMPOSSIBLE": "impossible circular number", "DOC_ACTIVE_CONTENT": "hidden code in a PDF", "QR_PAYEE_NOT_CLAIMED_ENTITY": "QR pays someone else",
    "UPI_NOT_VALID_FOR_SEBI_CLAIM": "non-@valid UPI for a SEBI claim", "UPI_FOR_REGULATOR": "regulator asking for UPI money", "UPI_PERSONAL_FOR_BANK": "personal UPI for a bank",
    "PHONE_MOBILE_FOR_INSTITUTION": "mobile number posing as a bank", "ALERT_LIST_HIT": "on RBI's Alert List", "ASK_CREDENTIALS": "OTP / PIN request",
    "ASK_REMOTE_ACCESS": "screen-sharing app", "ASK_INSTALL_APP": "app install", "PLAYBOOK_MATCH": "known scam playbook", "CAMPAIGN_MEMORY_HIT": "confirmed scam campaign",
    "PRESSURE_SECRECY": "secrecy pressure", "PRESSURE_THREAT": "threats",
}
DISGUISES = {"DOMAIN_PUNYCODE", "DOMAIN_ZONE_SPOOF", "DOMAIN_LOOKALIKE"}
LIVE_HOURS = 6


def _codes(verdict: dict) -> set[str]:
    codes = {r["code"] for r in verdict.get("reasons", [])}
    for lane in verdict.get("lanes", {}).values():
        codes |= {s["code"] for s in lane.get("signals", [])}
    return codes


def _disguised_contact(verdict: dict) -> bool:
    """A UPI ID written with spaces, spelled out ('at the rate') or split up still got found."""
    for c in verdict.get("extraction", {}).get("contact_points", []):
        if c["type"] == "upi" and re.sub(r"\s+", "", (c.get("raw") or "").lower()) != c["value"]:
            return True
    return False


@app.get("/redteam/live.json")
async def redteam_live():
    since = (datetime.now(timezone.utc) - timedelta(hours=LIVE_HOURS)).isoformat()
    with db.conn() as c:
        rows = c.execute("SELECT serial, created_at, level, verdict FROM checks WHERE created_at >= ? ORDER BY created_at DESC LIMIT 400", (since,)).fetchall()
    counts = {"total": len(rows), "red": 0, "hidden": 0, "disguise": 0, "fooled": 0}
    recent = []
    for r in rows:
        v = json.loads(r["verdict"])
        codes = _codes(v)
        hidden = bool(v.get("extraction", {}).get("hidden_instructions")) or "HIDDEN_INSTRUCTIONS" in codes
        disguise = bool(codes & DISGUISES) or _disguised_contact(v)
        counts["red"] += r["level"] == "RED"
        counts["hidden"] += hidden
        counts["disguise"] += disguise
        counts["fooled"] += (hidden or disguise) and r["level"] == "GREEN"
        if len(recent) < 12:
            caught = [CAUGHT[k] for k in CAUGHT if k in codes]
            if disguise and not codes & DISGUISES:
                caught.insert(0, "disguised UPI ID")
            when = datetime.fromisoformat(r["created_at"]).astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%H:%M")
            recent.append({"serial": r["serial"], "time": when, "level": r["level"], "caught": caught[:5]})
    return JSONResponse({"counts": counts, "recent": recent}, headers={"Cache-Control": "no-store"})


@app.get("/redteam", response_class=HTMLResponse)
async def redteam():
    from app.render.card import qr_data_uri
    p = Path(__file__).resolve().parent.parent / "eval" / "results" / "redteam.json"
    r = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    groups = []
    for a in (r or {}).get("attacks", []):
        a["found"] = [CAUGHT[k] for k in CAUGHT if k in set(a.get("codes") or [])][:4]
        if not groups or groups[-1]["name"] != a["group"]:
            groups.append({"name": a["group"], "rows": [], "passed": 0, "misses": 0})
        groups[-1]["rows"].append(a)
        groups[-1]["passed"] += a["status"] == "PASS"
        groups[-1]["misses"] += a["status"] == "KNOWN_MISS"
    bot = f"https://t.me/{telegram_username()}"
    return env.get_template("redteam.html").render(r=r, groups=groups, live_hours=LIVE_HOURS, bot_qr=qr_data_uri(bot))


def telegram_username() -> str:
    from app.channels import telegram
    return telegram.BOT.username if telegram.BOT else "PramaanVerifyDemoBot"


@app.get("/health")
async def health():
    reg = get_registry()
    return {"ok": True, "reference_clips": len(load_index()), "banks": len(reg.banks), "alert_list": len(reg.alert_list), "key": key_id()}


def _home_stats() -> dict | None:
    """The landing page's numbers, read from the latest results files (never typed in)."""
    res = Path(__file__).resolve().parent.parent / "eval" / "results"
    try:
        r = json.loads((res / "latest.json").read_text(encoding="utf-8"))
        m = r["metrics"].get("test") or r["metrics"]["all"]
        ke = r["key_experiment"]["genuine_false_alarm"]
        out = {"false_red": m["false_red_on_genuine"]["k"], "genuine": m["false_red_on_genuine"]["n"],
               "caught": m["scam_recall_amber_or_red"]["k"], "scams": m["scam_recall_amber_or_red"]["n"],
               "ke_det": ke["detector_alone"]["k"], "ke_n": ke["detector_alone"]["n"], "ke_pr": ke["pramaan_red"]["k"], "attacks": None}
        tiers = m.get("tiers")
        if tiers:
            out |= {"gen_not_clear": tiers["genuine_not_clear"]["k"], "gen_warned": tiers["genuine_warned"]["k"]}
        rt = res / "redteam.json"
        if rt.exists():
            t = json.loads(rt.read_text(encoding="utf-8"))
            out |= {"attacks": t["total"], "defended": t["passed"], "misses": (t.get("known_misses") or {}).get("still_missed")}
        ok = res / "open_kit-baseline-frozen.json"
        if ok.exists():
            o = json.loads(ok.read_text(encoding="utf-8"))["per_label"]
            out |= {"open_n": sum(x["n"] for x in o.values()), "open_ham_warned": o["ham"]["warned"]["k"], "open_ham": o["ham"]["n"],
                    "open_smish_flagged": o["smishing"]["not_clear"]["k"], "open_smish": o["smishing"]["n"]}
        return out
    except (OSError, KeyError, ValueError):
        return None


def _demo_links() -> dict:
    p = DATA_DIR / "demo_links.json"          # written by tools/prepare_demo.py
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except ValueError:
        return {}


@app.get("/", response_class=HTMLResponse)
async def home():
    from app.render.card import qr_data_uri
    bot = telegram_username()
    return env.get_template("home.html").render(stats=_home_stats(), demo=_demo_links(), bot=bot, bot_qr=qr_data_uri(f"https://t.me/{bot}"))
