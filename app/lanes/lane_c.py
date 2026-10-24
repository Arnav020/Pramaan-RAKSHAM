"""Lane C · WHAT: what is the message asking you to do, and how hard is it pushing?

ask risk HIGH needs a STRONG signal here: a request no genuine institution
makes (OTP, remote access, sideloaded app), a payment pushed with pressure
tactics, or a high-risk ask that matches a known scam playbook.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import yaml

from app.config import RAILS_DATA_DIR
from app.extract.intent_rules import strip_negated
from app.models import AskRisk, AskType, ContactType, Extraction, Lane, LaneResult, Pressure, Provenance, Signal, Strength

_HIGH_RISK_ASKS = {AskType.PAY, AskType.CREDENTIALS, AskType.INSTALL_APP, AskType.REMOTE_ACCESS}
_PUSHY = {Pressure.GUARANTEED_RETURNS, Pressure.THREAT, Pressure.TOO_GOOD}
PRESSURE_WORDS = {
    Pressure.GUARANTEED_RETURNS: ("guaranteed returns", "गारंटीड रिटर्न के वादे"),
    Pressure.THREAT: ("threats", "धमकियों"),
    Pressure.TOO_GOOD: ("an offer too good to be true", "बहुत ज़्यादा अच्छे ऑफ़र"),
    Pressure.URGENCY: ("urgency", "जल्दबाज़ी"),
    Pressure.SECRECY: ("secrecy", "गोपनीयता"),
    Pressure.AUTHORITY: ("official-sounding authority", "सरकारी रौब"),
}


@dataclass
class Playbook:
    id: str
    name_en: str
    name_hi: str
    summary_en: str
    summary_hi: str
    sources: list[dict]
    required: list[str]
    groups: dict[str, list[re.Pattern]]
    high_risk_alone: bool = False  # a full match is strong even without a payment ask


@lru_cache(maxsize=1)
def load_playbooks() -> tuple[str, list[Playbook]]:
    d = yaml.safe_load((RAILS_DATA_DIR / "playbooks.yaml").read_text(encoding="utf-8"))
    out = []
    for p in d["playbooks"]:
        groups = {g: [re.compile(x, re.IGNORECASE) for x in pats] for g, pats in p["groups"].items()}
        out.append(Playbook(p["id"], p["name_en"], p["name_hi"], p["summary_en"], p["summary_hi"], p["sources"], p["required"], groups,
                            bool(p.get("high_risk_alone", False))))
    return d["version"], out


def match_playbooks(text: str) -> list[tuple[Playbook, float, list[str]]]:
    """Return playbooks whose required groups are all present, scored by group coverage."""
    _, pbs = load_playbooks()
    text = strip_negated(text)
    hits = []
    hidden = _held_out()
    for pb in pbs:
        if pb.id in hidden:
            continue
        present = [g for g, pats in pb.groups.items() if any(p.search(text) for p in pats)]
        if not all(r in present for r in pb.required):
            continue
        score = len(present) / len(pb.groups)
        if score >= 0.5:
            hits.append((pb, round(score, 2), present))
    return sorted(hits, key=lambda h: -h[1])


PAGE_CHUNK = 420   # characters: about one paragraph of a web page


def page_intent(text: str) -> list[Signal]:
    """Lane C for a web page's visible text. A page is long and full of generic words ('daily profit credit',
    'payout'), so a playbook counts only where its keyword groups appear together in one paragraph, and the
    playbook supported by the most paragraphs wins (ties: the one whose required ideas recur most). Rules only: no semantic matcher (too slow on a page) and no AI."""
    from app.extract.rules_extractor import extract_with_rules
    ex = extract_with_rules(text)
    asks, pressure = set(ex.asks), set(ex.pressure)
    sigs: list[Signal] = []
    if asks & {AskType.PAY, AskType.INSTALL_APP} and pressure & _PUSHY:
        used = sorted(pressure & _PUSHY, key=lambda x: x.value)
        sigs.append(_sig("ASK_PAY_WITH_PRESSURE", Strength.STRONG, pressure=[p.value for p in used],
                         pressure_en=", ".join(PRESSURE_WORDS[p][0] for p in used), pressure_hi=", ".join(PRESSURE_WORDS[p][1] for p in used)))
    chunks, cur = [], ""
    for sentence in re.split(r"(?<=[.!?।])\s+|\n+", ex.text):
        if cur and len(cur) + len(sentence) > PAGE_CHUNK:
            chunks.append(cur)
            cur = ""
        cur = f"{cur} {sentence}".strip()
    if cur:
        chunks.append(cur)
    support: dict[str, list] = {}
    for ch in chunks:
        for pb, score, present in match_playbooks(ch):
            if score >= 0.75:
                s = support.setdefault(pb.id, [pb, 0, score, present])
                s[1] += 1
                if score > s[2]:
                    s[2], s[3] = score, present
    if support:
        version, _ = load_playbooks()
        page = strip_negated(ex.text)

        def theme(s) -> int:   # how often the playbook's required ideas recur across the whole page
            return sum(len(p.findall(page)) for g in s[0].required for p in s[0].groups[g])
        pb, n, score, present = max(support.values(), key=lambda s: (s[1], theme(s), s[2]))
        strong = bool(asks & _HIGH_RISK_ASKS) or pb.high_risk_alone
        prov = Provenance(dataset=f"Scam playbooks v{version}", source_url=pb.sources[0]["url"], note=pb.sources[0]["title"])
        sigs.append(_sig("PLAYBOOK_MATCH", Strength.STRONG if strong else Strength.WEAK, prov, playbook=pb.id, name_en=pb.name_en,
                         name_hi=pb.name_hi, score=score, indicators=present, paragraphs=n, method="rules"))
    return sigs


def _sem_strong() -> float:
    from app.lanes.semantic import STRONG
    return STRONG


def _held_out() -> set[str]:
    """Playbooks hidden for the 'scam types never seen' study (eval/robustness.py). Empty in normal use."""
    import os
    return {x for x in os.getenv("PRAMAAN_HOLDOUT_PLAYBOOKS", "").split(",") if x}


def _semantic(text: str) -> list[dict]:
    """Semantic playbook matches, or [] if the model is off or unavailable (the rules still run)."""
    try:
        from app.lanes import semantic
        return semantic.match(text) if semantic.enabled() else []
    except Exception as e:
        import logging
        logging.getLogger("pramaan.semantic").warning("semantic match skipped: %s", type(e).__name__)
        return []


def _sig(code: str, strength: Strength, prov: Provenance | None = None, **params) -> Signal:
    return Signal(code=code, lane=Lane.C, strength=strength, params=params, provenance=prov)


def run_lane_c(ex: Extraction) -> LaneResult:
    asks, pressure = set(ex.asks), set(ex.pressure)
    sigs: list[Signal] = []

    if AskType.CREDENTIALS in asks:
        sigs.append(_sig("ASK_CREDENTIALS", Strength.STRONG))
    if AskType.REMOTE_ACCESS in asks:
        sigs.append(_sig("ASK_REMOTE_ACCESS", Strength.STRONG))
    if any(cp.type == ContactType.APP for cp in ex.contact_points):
        sigs.append(_sig("ASK_SIDELOAD_APP", Strength.STRONG, apps=[cp.value for cp in ex.contact_points if cp.type == ContactType.APP]))
    if asks & {AskType.PAY, AskType.INSTALL_APP} and pressure & _PUSHY:
        used = sorted(pressure & _PUSHY, key=lambda x: x.value)
        sigs.append(_sig("ASK_PAY_WITH_PRESSURE", Strength.STRONG, pressure=[p.value for p in used],
                         pressure_en=", ".join(PRESSURE_WORDS[p][0] for p in used), pressure_hi=", ".join(PRESSURE_WORDS[p][1] for p in used)))

    version, pbs = load_playbooks()
    matches = match_playbooks(ex.text)
    top = matches[0] if matches else None
    sem = _semantic(ex.text)
    # "Call our officer on 98xxxxxxxx": inside a known scam script, a request to call a personal mobile number is as
    # risky as a payment request (it is how the 'officer' then takes the money). Genuine notices give official numbers.
    call_mobile = AskType.CALL_BACK in asks and any(
        cp.type == ContactType.PHONE and len(cp.value) == 10 and cp.value[0] in "6789" for cp in ex.contact_points)
    if top:
        pb, score, present = top
        prov = Provenance(dataset=f"Scam playbooks v{version}", source_url=pb.sources[0]["url"], note=pb.sources[0]["title"])
        strong = score >= 0.75 and (bool(asks & _HIGH_RISK_ASKS) or pb.high_risk_alone or call_mobile)
        same = next((m for m in sem if m["playbook"] == pb.id), None)
        sigs.append(_sig("PLAYBOOK_MATCH", Strength.STRONG if strong else Strength.WEAK, prov,
                         playbook=pb.id, name_en=pb.name_en, name_hi=pb.name_hi, score=score, indicators=present,
                         method="rules+semantic" if same else "rules", similarity=same["similarity"] if same else None))
    elif sem and (asks - {AskType.NONE} or pressure or sem[0]["similarity"] >= _sem_strong()):
        # The phrase rules found no pattern, but the meaning is close to a known scam (a rewording).
        # A resemblance alone is not enough: the message must also ask for something or apply pressure,
        # unless the match is very close. (A genuine interview about investing is not a scam.)
        from app.lanes.semantic import STRONG as SEM_STRONG
        best = sem[0]
        pb = next(p for p in pbs if p.id == best["playbook"])
        prov = Provenance(dataset=f"Scam playbooks v{version} · semantic match", source_url=pb.sources[0]["url"], note=pb.sources[0]["title"])
        strong = best["similarity"] >= SEM_STRONG and (bool(asks & _HIGH_RISK_ASKS) or pb.high_risk_alone)
        sigs.append(_sig("PLAYBOOK_MATCH", Strength.STRONG if strong else Strength.WEAK, prov,
                         playbook=pb.id, name_en=pb.name_en, name_hi=pb.name_hi, score=None, indicators=[],
                         method="semantic", similarity=best["similarity"], sentence=best["sentence"]))

    for p in sorted(pressure, key=lambda x: x.value):
        sigs.append(_sig(f"PRESSURE_{p.value.upper()}", Strength.WEAK))
    if AskType.PRIVATE_GROUP in asks:
        sigs.append(_sig("ASK_PRIVATE_GROUP", Strength.WEAK))
    if ex.hidden_instructions:
        sigs.append(_sig("HIDDEN_INSTRUCTIONS", Strength.WEAK))

    if any(s.strength == Strength.STRONG for s in sigs):
        risk = AskRisk.HIGH
    elif asks - {AskType.NONE} or pressure:
        risk = AskRisk.LOW
    else:
        risk = AskRisk.NONE
    return LaneResult(
        lane=Lane.C, status=risk.value, signals=sigs,
        detail={"asks": sorted(a.value for a in asks), "pressure": sorted(p.value for p in pressure),
                "playbooks": [{"id": m[0].id, "score": m[1]} for m in matches[:3]]
                + [{"id": m["playbook"], "score": None, "similarity": m["similarity"]} for m in sem[:2] if not matches],
                "semantic": sem[:3]},
    )
