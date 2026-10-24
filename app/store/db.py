"""SQLite store for checks. Small, file-based, no server to run.

Data minimisation (DPDP Act 2023):
- We store the verdict and a pseudonymous channel reference (salted hash),
  never a phone number or name.
- Uploaded media is copied to data/store/ only so the proof page can play it,
  and is deleted after 24 hours (purge_expired), unless the user reports it.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import DB_PATH, STORE_DIR

_LOCK = threading.Lock()
MEDIA_TTL = timedelta(hours=24)

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
  id TEXT PRIMARY KEY,
  serial TEXT UNIQUE NOT NULL,
  created_at TEXT NOT NULL,
  channel TEXT NOT NULL,
  chat_ref TEXT,
  level TEXT NOT NULL,
  rule_id TEXT,
  payload TEXT NOT NULL,       -- signed payload (what the QR proves)
  signature TEXT NOT NULL,
  verdict TEXT NOT NULL,       -- full verdict JSON (lanes, reasons, evidence)
  proof TEXT NOT NULL,         -- extra evidence for the proof page (diffs, frames)
  media TEXT NOT NULL,         -- transient media files kept for the proof page
  media_expires_at TEXT,
  watch INTEGER DEFAULT 0,     -- user opted in to a Retro-Alert
  reported INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS checks_chat ON checks(chat_ref);
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS prefs (chat_ref TEXT PRIMARY KEY, lang TEXT NOT NULL);
-- Only for users who tap "Watch this for me": the chat address needed to send a
-- Retro-Alert. Explicit opt-in, deleted after 30 days.
CREATE TABLE IF NOT EXISTS watchers (
  check_id TEXT NOT NULL, channel TEXT NOT NULL, chat_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  alerted INTEGER DEFAULT 0, PRIMARY KEY (check_id, channel, chat_id)
);
-- "Ask someone I trust": a one-time link the user forwards to a family member. Holds the
-- requester's chat address only so we can tell them "your contact is calling you". 24 h.
CREATE TABLE IF NOT EXISTS trust_links (
  token TEXT PRIMARY KEY, check_id TEXT NOT NULL, channel TEXT NOT NULL, requester_chat TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, opened_at TEXT, answered_at TEXT
);
-- "This looks wrong": a user disputes a verdict. Who appealed is never stored.
CREATE TABLE IF NOT EXISTS appeals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, check_id TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', decided_at TEXT, decided_by TEXT
);
"""


@contextmanager
def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        c.execute("PRAGMA journal_mode=WAL")
        cols = {r["name"] for r in c.execute("PRAGMA table_info(appeals)")}
        for col in ("notify_channel", "notify_chat", "note_out"):   # added 24 Oct: the outcome goes back to the person
            if col not in cols:
                c.execute(f"ALTER TABLE appeals ADD COLUMN {col} TEXT")
    from app.store import campaigns
    campaigns.init()


def pseudonym(channel: str, raw_id: str | int) -> str:
    """Salted one-way reference to a chat, so we can send a Retro-Alert without storing who it is."""
    from app.store.signing import _key  # the server's secret doubles as the salt source
    salt = hashlib.sha256(_key().private_bytes_raw()).hexdigest()[:16]
    return hashlib.sha256(f"{salt}:{channel}:{raw_id}".encode()).hexdigest()[:24]


def next_serial(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%y%m%d")
    with _LOCK, conn() as c:
        c.execute("INSERT INTO counters(name, value) VALUES(?, 1) ON CONFLICT(name) DO UPDATE SET value = value + 1", (f"serial:{day}",))
        n = c.execute("SELECT value FROM counters WHERE name = ?", (f"serial:{day}",)).fetchone()[0]
    return f"PR-{day}-{n:04d}"


def new_id() -> str:
    return secrets.token_urlsafe(9)


def save_media(check_id: str, paths: list[Path]) -> list[str]:
    out = []
    d = STORE_DIR / check_id
    for i, p in enumerate(paths):
        d.mkdir(parents=True, exist_ok=True)
        dst = d / f"m{i}{p.suffix.lower()}"
        shutil.copyfile(p, dst)
        out.append(dst.name)
    return out


def insert_check(row: dict) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO checks(id, serial, created_at, channel, chat_ref, level, rule_id, payload, signature, verdict, proof, media, media_expires_at)"
            " VALUES(:id, :serial, :created_at, :channel, :chat_ref, :level, :rule_id, :payload, :signature, :verdict, :proof, :media, :media_expires_at)",
            row,
        )


def get_check(check_id: str | None = None, serial: str | None = None) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM checks WHERE id = ? OR serial = ?", (check_id or "", serial or "")).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("payload", "verdict", "proof", "media"):
        d[k] = json.loads(d[k])
    return d


def set_flag(check_id: str, flag: str, value: int = 1) -> None:
    assert flag in ("watch", "reported")
    with conn() as c:
        c.execute(f"UPDATE checks SET {flag} = ? WHERE id = ?", (value, check_id))


def recent_count(chat_ref: str, minutes: int = 60) -> int:
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM checks WHERE chat_ref = ? AND created_at >= ?", (chat_ref, since)).fetchone()[0]


_PROOF_KEEP = ("timings", "source", "media")   # after expiry: which original matched and how long it took, nothing said


def scrub_content(verdict: dict, proof: dict) -> tuple[dict, dict]:
    """What a check keeps after 24 h: the verdict, its reasons and the scammer-side identifiers
    (UPI IDs, numbers, links) that campaigns and Retro-Alerts match on. The message text,
    transcripts, word diffs and quoted evidence are removed."""
    ex = verdict.get("extraction", {})
    ex["text"] = ""
    ex["ai_saw"] = None
    for cp in ex.get("contact_points", []):
        cp["raw"], cp["context"] = cp.get("value", ""), ""
    for cl in ex.get("claims", []):
        cl["evidence"] = ""
    for lane in verdict.get("lanes", {}).values():
        lane.get("detail", {}).pop("diff", None)
    proof = {k: v for k, v in proof.items() if k in _PROOF_KEEP} | {"media": [], "expired": True}
    return verdict, proof


def purge_expired() -> int:
    """After 24 h, delete the media and the message content of every unreported check
    (reported items are kept for the evidence vault)."""
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    with conn() as c:
        rows = c.execute("SELECT id, verdict, proof FROM checks WHERE media_expires_at IS NOT NULL AND media_expires_at < ? AND reported = 0",
                         (now,)).fetchall()
        for r in rows:
            shutil.rmtree(STORE_DIR / r["id"], ignore_errors=True)
            verdict, proof = scrub_content(json.loads(r["verdict"]), json.loads(r["proof"]))
            c.execute("UPDATE checks SET media = '[]', media_expires_at = NULL, verdict = ?, proof = ? WHERE id = ?",
                      (json.dumps(verdict, ensure_ascii=False), json.dumps(proof, ensure_ascii=False), r["id"]))
            n += 1
    return n


def purge_caches(hours: float = 24) -> int:
    """The AI-reading cache (data/cache/llm) holds what the AI quoted from a message, and the site cache holds which
    sites people asked about: both follow the same 24-hour rule as the content itself on a public server.
    On by default when Pramaan is served over HTTPS; PRAMAAN_PURGE_CACHES=on/off overrides (the team's laptop keeps
    the cache of the synthetic and public evaluation items, so a re-run reproduces the same AI answers)."""
    import os
    import time
    from app.config import DATA_DIR, get_settings
    flag = os.getenv("PRAMAAN_PURGE_CACHES", "auto").lower()
    if flag in ("0", "off", "false") or (flag == "auto" and not get_settings().public_base_url.startswith("https://")):
        return 0
    cutoff, n = time.time() - hours * 3600, 0
    for sub in ("llm", "site"):
        for p in (DATA_DIR / "cache" / sub).glob("*.json"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    n += 1
            except OSError:
                pass
    return n


def get_lang(chat_ref: str) -> str | None:
    with conn() as c:
        r = c.execute("SELECT lang FROM prefs WHERE chat_ref = ?", (chat_ref,)).fetchone()
    return r["lang"] if r else None


def set_lang(chat_ref: str, lang: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO prefs(chat_ref, lang) VALUES(?, ?) ON CONFLICT(chat_ref) DO UPDATE SET lang = excluded.lang", (chat_ref, lang))


WATCH_TTL = timedelta(days=30)


def add_watcher(check_id: str, channel: str, chat_id: str) -> None:
    now = datetime.now(timezone.utc)
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO watchers(check_id, channel, chat_id, created_at, expires_at, alerted) VALUES(?, ?, ?, ?, ?, 0)",
                  (check_id, channel, chat_id, now.isoformat(), (now + WATCH_TTL).isoformat()))
    set_flag(check_id, "watch", 1)


def watchers_for(check_ids: list[str]) -> list[dict]:
    if not check_ids:
        return []
    now = datetime.now(timezone.utc).isoformat()
    q = ",".join("?" * len(check_ids))
    with conn() as c:
        rows = c.execute(f"SELECT * FROM watchers WHERE check_id IN ({q}) AND alerted = 0 AND expires_at > ?", (*check_ids, now)).fetchall()
    return [dict(r) for r in rows]


def mark_alerted(check_id: str, channel: str, chat_id: str) -> None:
    with conn() as c:
        c.execute("UPDATE watchers SET alerted = 1 WHERE check_id = ? AND channel = ? AND chat_id = ?", (check_id, channel, chat_id))


def purge_watchers() -> int:
    now = datetime.now(timezone.utc).isoformat()
    with conn() as c:
        return c.execute("DELETE FROM watchers WHERE expires_at < ?", (now,)).rowcount + \
            c.execute("DELETE FROM trust_links WHERE expires_at < ?", (now,)).rowcount


def add_appeal(check_id: str, note: str = "", notify: tuple[str, str] | None = None) -> int:
    """notify: (channel, chat id) to tell the person the outcome. Kept only until the appeal is decided.
    A repeat appeal on the same check while one is open returns the open one (no queue flooding)."""
    with conn() as c:
        open_one = c.execute("SELECT id FROM appeals WHERE check_id = ? AND status = 'open'", (check_id,)).fetchone()
        if open_one:
            if notify:
                c.execute("UPDATE appeals SET notify_channel = ?, notify_chat = ? WHERE id = ?", (*notify, open_one["id"]))
            return open_one["id"]
        cur = c.execute("INSERT INTO appeals(check_id, note, created_at, notify_channel, notify_chat) VALUES(?, ?, ?, ?, ?)",
                        (check_id, note.strip()[:300], datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         notify[0] if notify else None, notify[1] if notify else None))
        return cur.lastrowid


def list_appeals(status: str | None = None, limit: int = 50) -> list[dict]:
    q = "SELECT a.*, c.serial, c.level FROM appeals a LEFT JOIN checks c ON c.id = a.check_id"
    args: tuple = ()
    if status:
        q, args = q + " WHERE a.status = ?", (status,)
    with conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY a.id DESC LIMIT ?", (*args, limit)).fetchall()]


def decide_appeal(appeal_id: int, decision: str, by: str, note_out: str = "") -> dict | None:
    """Decide an open appeal. Returns the appeal as it was (with where to send the outcome), or None.
    The chat address is erased in the same step: it is never kept after the answer."""
    assert decision in ("upheld", "dismissed")
    with conn() as c:
        r = c.execute("SELECT * FROM appeals WHERE id = ? AND status = 'open'", (appeal_id,)).fetchone()
        if r is None:
            return None
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        c.execute("UPDATE appeals SET status = ?, decided_at = ?, decided_by = ?, note_out = ?, notify_channel = NULL, notify_chat = NULL "
                  "WHERE id = ?", (decision, now, by, note_out.strip()[:300], appeal_id))
    out = dict(r) | {"status": decision, "decided_at": now}
    out["minutes"] = round((datetime.fromisoformat(now) - datetime.fromisoformat(r["created_at"])).total_seconds() / 60, 1)
    return out


def upheld_appeal(check_id: str) -> dict | None:
    """A verdict an analyst has corrected after an appeal: shown on its proof and verify pages."""
    with conn() as c:
        r = c.execute("SELECT id, decided_at, note_out FROM appeals WHERE check_id = ? AND status = 'upheld' ORDER BY id DESC LIMIT 1",
                      (check_id,)).fetchone()
    return dict(r) if r else None


def appeal_counts() -> dict:
    """Live counts, plus how long a decided appeal waited for a person (median minutes)."""
    with conn() as c:
        rows = c.execute("SELECT status, COUNT(*) n FROM appeals GROUP BY status").fetchall()
        waits = [(datetime.fromisoformat(r["decided_at"]) - datetime.fromisoformat(r["created_at"])).total_seconds() / 60
                 for r in c.execute("SELECT created_at, decided_at FROM appeals WHERE decided_at IS NOT NULL")]
    out = {"open": 0, "upheld": 0, "dismissed": 0}
    out.update({r["status"]: r["n"] for r in rows})
    out["median_minutes"] = round(sorted(waits)[len(waits) // 2], 1) if waits else None
    return out


def needs_analyst(days: int = 7, limit: int = 30) -> list[dict]:
    """Verified sender + a high-risk ask: possibly a hacked genuine account. Shown to analysts, never to the public."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out = []
    with conn() as c:
        rows = c.execute("SELECT id, serial, created_at, level, verdict FROM checks WHERE created_at >= ? ORDER BY created_at DESC LIMIT 500",
                         (since,)).fetchall()
    for r in rows:
        v = json.loads(r["verdict"])
        if v.get("needs_analyst"):
            out.append({"id": r["id"], "serial": r["serial"], "created_at": r["created_at"], "level": r["level"],
                        "reasons": [x["text_en"] for x in v.get("reasons", [])[:2]]})
            if len(out) >= limit:
                break
    return out


TRUST_TTL = timedelta(hours=24)


def new_trust_link(check_id: str, channel: str, requester_chat: str) -> str:
    token = secrets.token_urlsafe(12)
    now = datetime.now(timezone.utc)
    with conn() as c:
        c.execute("INSERT INTO trust_links(token, check_id, channel, requester_chat, created_at, expires_at) VALUES(?, ?, ?, ?, ?, ?)",
                  (token, check_id, channel, requester_chat, now.isoformat(), (now + TRUST_TTL).isoformat()))
    return token


def open_trust_link(token: str) -> dict | None:
    """The trusted contact opened the link. Returns it while valid; one contact per link."""
    now = datetime.now(timezone.utc).isoformat()
    with conn() as c:
        r = c.execute("SELECT * FROM trust_links WHERE token = ? AND expires_at > ?", (token, now)).fetchone()
        if r and not r["opened_at"]:
            c.execute("UPDATE trust_links SET opened_at = ? WHERE token = ?", (now, token))
    return dict(r) if r else None


def answer_trust_link(token: str) -> dict | None:
    """The contact tapped "I'll call them". Returns the link the first time only."""
    now = datetime.now(timezone.utc).isoformat()
    with conn() as c:
        r = c.execute("SELECT * FROM trust_links WHERE token = ? AND expires_at > ? AND answered_at IS NULL", (token, now)).fetchone()
        if r:
            c.execute("UPDATE trust_links SET answered_at = ? WHERE token = ?", (now, token))
    return dict(r) if r else None
