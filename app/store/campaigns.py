"""Campaigns: "review once, protect many".

A campaign is a group of checks that share a HARD indicator:
  upi:<id>  phone:<n>  domain:<registrable domain>  footage:<genuine ref id> (re-voiced)
  circular:<RBI ref> (borrowed or impossible number)
Reports (explicit opt-in) open or join campaigns. An analyst confirms or
rejects a campaign once. Confirmation:
  - adds its indicators to CAMPAIGN MEMORY (future matches become a strong signal),
  - sends a Retro-Alert to every opted-in watcher of a matching check.
Nothing external happens automatically; packets are files for the bank team.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

from app.extract.contacts import registrable_domain
from app.store.db import conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',   -- open | confirmed | rejected
  created_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT, note TEXT, playbook TEXT
);
CREATE TABLE IF NOT EXISTS campaign_indicators (campaign_id TEXT NOT NULL, indicator TEXT NOT NULL, PRIMARY KEY (campaign_id, indicator));
CREATE INDEX IF NOT EXISTS ci_ind ON campaign_indicators(indicator);
CREATE TABLE IF NOT EXISTS reports (
  check_id TEXT PRIMARY KEY, serial TEXT NOT NULL, campaign_id TEXT NOT NULL, reported_at TEXT NOT NULL,
  chain_seq INTEGER, manifest_sha TEXT
);
CREATE TABLE IF NOT EXISTS check_indicators (check_id TEXT NOT NULL, indicator TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (check_id, indicator));
CREATE INDEX IF NOT EXISTS chk_ind ON check_indicators(indicator);
"""

HARD_PREFIXES = ("upi:", "phone:", "domain:", "footage:", "circular:")
_IGNORE_DOMAINS = {"cybercrime.gov.in", "sancharsaathi.gov.in", "sebi.gov.in", "rbi.org.in"}
# Shared platforms are never a campaign's indicator: confirming "t.me" or "youtube.com" as a scam would put a strong
# signal on every genuine message that links to them (campaign poisoning). The scammer's own handle on them is what
# identifies a campaign, and that is kept by its UPI ID, number or own domain.
_SHARED_PLATFORMS = {"t.me", "telegram.me", "telegram.org", "wa.me", "whatsapp.com", "youtube.com", "youtu.be", "google.com",
                     "goo.gl", "forms.gle", "bit.ly", "tinyurl.com", "instagram.com", "facebook.com", "fb.me", "x.com", "twitter.com",
                     "linkedin.com", "play.google.com", "apple.com", "drive.google.com", "docs.google.com", "github.com", "medium.com",
                     "gov.in", "nic.in", "npci.org.in", "upi.npci.org.in"}


def _never_an_indicator(domain: str) -> bool:
    """Official institutions (from the registry) and shared platforms cannot be filed as scam indicators."""
    from app.extract.contacts import FREE_HOSTS
    from app.rails.registry import get_registry
    return (domain in _IGNORE_DOMAINS or domain in _SHARED_PLATFORMS or domain in FREE_HOSTS
            or get_registry().entity_for_domain(domain) is not None or domain.endswith((".gov.in", ".nic.in")))


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ist_date(iso: str | None) -> str:
    """A stored UTC time as the date people in India saw it (a 03:10 IST confirm is not 'yesterday')."""
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10]
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone(timedelta(hours=5, minutes=30))).date().isoformat()


def indicators_from_verdict(v: dict) -> list[str]:
    """Hard indicators of a stored verdict (dict form). Official/positive findings are excluded."""
    out: set[str] = set()
    lanes = v.get("lanes", {})
    positive_values = set()
    for s in lanes.get("A", {}).get("signals", []):
        if s["strength"] == "POSITIVE":
            positive_values |= {str(x).lower() for x in s.get("params", {}).values()}
    for cp in v.get("extraction", {}).get("contact_points", []):
        t, val = cp["type"], cp["value"].lower()
        if t == "upi" and val not in positive_values and "@valid" not in val:
            out.add(f"upi:{val}")
        elif t == "phone" and len(val) == 10 and val[0] in "6789":
            out.add(f"phone:{val}")
        elif t == "url":
            dom = registrable_domain(val)[0]
            if dom not in positive_values and not _never_an_indicator(dom):
                out.add(f"domain:{dom}")
    b = lanes.get("B", {})
    for s in b.get("signals", []):
        if s["code"] == "SOURCE_MISMATCH" and s.get("params", {}).get("ref_id"):
            out.add(f"footage:{s['params']['ref_id']}")
        if s["code"] in ("DOC_SOURCE_MISMATCH", "DOC_REFERENCE_IMPOSSIBLE") and s.get("params", {}).get("ref"):
            out.add(f"circular:{s['params']['ref']}")
    return sorted(out)


def record_check(check_id: str, verdict: dict) -> list[str]:
    """Index every check's hard indicators (needed to count reach and to find Retro-Alert watchers)."""
    inds = indicators_from_verdict(verdict)
    init()
    with conn() as c:
        c.executemany("INSERT OR IGNORE INTO check_indicators(check_id, indicator, created_at) VALUES(?, ?, ?)", [(check_id, i, now()) for i in inds])
    return inds


def _title(verdict: dict, inds: list[str]) -> str:
    pb = next((s["params"].get("name_en") for s in verdict.get("lanes", {}).get("C", {}).get("signals", []) if s["code"] == "PLAYBOOK_MATCH"), None)
    lead = next((i.split(":", 1)[1] for p in ("footage:", "upi:", "circular:", "domain:", "phone:") for i in inds if i.startswith(p)), None)
    return f"{pb or 'Suspected scam'}" + (f" · {lead}" if lead else "")


def file_report(check: dict, chain_seq: int | None, manifest_sha: str | None) -> dict:
    """Attach a reported check to an existing campaign (shared hard indicator) or open a new one."""
    v = check["verdict"]
    inds = record_check(check["id"], v)
    with conn() as c:
        row = None
        if inds:
            q = ",".join("?" * len(inds))
            row = c.execute(f"SELECT c.id FROM campaigns c JOIN campaign_indicators i ON i.campaign_id = c.id "
                            f"WHERE i.indicator IN ({q}) AND c.status != 'rejected' ORDER BY c.created_at LIMIT 1", inds).fetchone()
        if row:
            cid = row["id"]
        else:
            cid = "C-" + secrets.token_hex(3).upper()
            pb = next((s["params"].get("playbook") for s in v.get("lanes", {}).get("C", {}).get("signals", []) if s["code"] == "PLAYBOOK_MATCH"), None)
            c.execute("INSERT INTO campaigns(id, title, created_at, playbook) VALUES(?, ?, ?, ?)", (cid, _title(v, inds), now(), pb))
        c.executemany("INSERT OR IGNORE INTO campaign_indicators(campaign_id, indicator) VALUES(?, ?)", [(cid, i) for i in inds])
        c.execute("INSERT OR REPLACE INTO reports(check_id, serial, campaign_id, reported_at, chain_seq, manifest_sha) VALUES(?, ?, ?, ?, ?, ?)",
                  (check["id"], check["serial"], cid, now(), chain_seq, manifest_sha))
    return {"campaign_id": cid, "indicators": inds}


def list_campaigns() -> list[dict]:
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM campaigns ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'confirmed' THEN 1 ELSE 2 END, created_at DESC")]
        for r in rows:
            r["indicators"] = [x["indicator"] for x in c.execute("SELECT indicator FROM campaign_indicators WHERE campaign_id = ?", (r["id"],))]
            r["reports"] = [dict(x) for x in c.execute("SELECT r.*, k.level, k.created_at AS checked_at FROM reports r JOIN checks k ON k.id = r.check_id "
                                                       "WHERE r.campaign_id = ? ORDER BY r.reported_at", (r["id"],))]
            hard = [i for i in r["indicators"] if i.startswith(HARD_PREFIXES)]
            if hard:
                q = ",".join("?" * len(hard))
                seen = c.execute(f"SELECT DISTINCT ci.check_id, k.chat_ref, k.created_at FROM check_indicators ci JOIN checks k ON k.id = ci.check_id "
                                 f"WHERE ci.indicator IN ({q})", hard).fetchall()
                r["checks_seen"] = len(seen)
                r["people_seen"] = len({s["chat_ref"] or s["check_id"] for s in seen})
                r["first_seen"] = min((s["created_at"] for s in seen), default=r["created_at"])
                r["watchers"] = c.execute(f"SELECT COUNT(*) FROM watchers w WHERE w.check_id IN (SELECT check_id FROM check_indicators WHERE indicator IN ({q}))", hard).fetchone()[0]
            else:
                r["checks_seen"] = r["people_seen"] = r["watchers"] = 0
                r["first_seen"] = r["created_at"]
    return rows


def decide(campaign_id: str, decision: str, analyst: str, note: str = "") -> dict | None:
    """None if there is no such campaign."""
    assert decision in ("confirmed", "rejected")
    with conn() as c:
        if c.execute("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,)).fetchone() is None:
            return None
        c.execute("UPDATE campaigns SET status = ?, decided_at = ?, decided_by = ?, note = ? WHERE id = ?", (decision, now(), analyst, note[:500], campaign_id))
    return next(x for x in list_campaigns() if x["id"] == campaign_id)


def memory_hits(indicators: list[str]) -> list[dict]:
    """Confirmed campaigns that share any of these hard indicators (campaign memory)."""
    if not indicators:
        return []
    init()
    q = ",".join("?" * len(indicators))
    with conn() as c:
        rows = c.execute(f"SELECT c.id, c.title, c.decided_at, i.indicator FROM campaigns c JOIN campaign_indicators i ON i.campaign_id = c.id "
                         f"WHERE c.status = 'confirmed' AND i.indicator IN ({q})", indicators).fetchall()
    return [dict(r) for r in rows]


def watchers_to_alert(campaign_id: str) -> list[dict]:
    with conn() as c:
        inds = [x["indicator"] for x in c.execute("SELECT indicator FROM campaign_indicators WHERE campaign_id = ?", (campaign_id,))]
        if not inds:
            return []
        q = ",".join("?" * len(inds))
        rows = c.execute(f"SELECT DISTINCT w.*, k.serial, k.created_at AS checked_at FROM watchers w JOIN checks k ON k.id = w.check_id "
                         f"WHERE w.alerted = 0 AND w.expires_at > ? AND w.check_id IN (SELECT check_id FROM check_indicators WHERE indicator IN ({q}))",
                         (now(), *inds)).fetchall()
    return [dict(r) for r in rows]


def export_packet(campaign_id: str) -> dict:
    """Takedown / referral packet for the bank's team: indicators, report serials, vault references. KeyError if unknown."""
    camp = next((x for x in list_campaigns() if x["id"] == campaign_id), None)
    if camp is None:
        raise KeyError(campaign_id)
    return {
        "type": "pramaan.campaign_packet.v1", "campaign": campaign_id, "title": camp["title"], "status": camp["status"],
        "decided_at": camp["decided_at"], "decided_by": camp["decided_by"],
        "mule_vpa_referral": [i.split(":", 1)[1] for i in camp["indicators"] if i.startswith("upi:")],
        "phone_numbers_for_chakshu": [i.split(":", 1)[1] for i in camp["indicators"] if i.startswith("phone:")],
        "domains_for_takedown": [i.split(":", 1)[1] for i in camp["indicators"] if i.startswith("domain:")],
        "reused_genuine_footage": [i.split(":", 1)[1] for i in camp["indicators"] if i.startswith("footage:")],
        "borrowed_circular_numbers": [i.split(":", 1)[1] for i in camp["indicators"] if i.startswith("circular:")],
        "reports": [{"serial": r["serial"], "reported_at": r["reported_at"], "vault_chain_seq": r["chain_seq"], "manifest_sha256": r["manifest_sha"]} for r in camp["reports"]],
        "reach": {"checks": camp["checks_seen"], "people": camp["people_seen"]},
        "generated_at": now(),
        "note": "Prepared for human review. Pramaan takes no external action itself.",
    }
