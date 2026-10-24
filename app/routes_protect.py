"""PROTECT before the money moves: Pramaan Lookup, the partner API, and the (mock) pay-time nudge."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.guard import enforce_rate, partner_for_key
from app.lookup import lookup

router = APIRouter()


def _env():
    from app.server import env
    return env


@router.get("/api/v1/lookup")
async def api_lookup(request: Request, q: str = ""):
    """Check a UPI ID, phone number or link. Partners send X-Pramaan-Key; the public is rate-limited."""
    enforce_rate(request, "lookup")
    res = await asyncio.to_thread(lookup, q)
    res["partner"] = partner_for_key(request.headers.get("x-pramaan-key", ""))
    return JSONResponse(res, headers={"Cache-Control": "no-store"})


@router.post("/api/appeal-lookup")
async def appeal_lookup(request: Request):
    """'This is my own ID and the result is wrong': goes to the Desk's Appeals. Who asked is never stored."""
    import re
    from fastapi import HTTPException
    from app.store import db
    enforce_rate(request, "appeal")
    from app.server import json_body
    body = await json_body(request)
    ind = str(body.get("indicator", ""))
    if not re.fullmatch(r"(?:upi|phone|domain):[\w.@\-]{3,120}", ind):
        raise HTTPException(400, "Unknown identifier.")
    aid = await asyncio.to_thread(db.add_appeal, f"lookup:{ind}", str(body.get("note", "")))
    return {"appeal": aid, "status": "open"}


@router.get("/lookup", response_class=HTMLResponse)
async def lookup_page(request: Request, q: str = ""):
    res = None
    if q.strip():
        enforce_rate(request, "lookup")
        res = await asyncio.to_thread(lookup, q)
    return _env().get_template("lookup.html").render(q=q, res=res)


@router.get("/partners", response_class=HTMLResponse)
async def partners():
    import json
    from app.config import ROOT
    p = ROOT / "eval" / "results" / "latest.json"
    lat = json.loads(p.read_text(encoding="utf-8")).get("latency_seconds") if p.exists() else None
    return _env().get_template("partners.html").render(latency=lat)


@router.get("/demo-bank/pay", response_class=HTMLResponse)
async def pay_mock():
    """MOCK of a bank app's UPI pay screen, to show where Pramaan sits at the payment moment. No money moves."""
    return _env().get_template("pay_mock.html").render()
