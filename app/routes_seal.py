"""Web routes for Pramaan Seal: the public seal page, and the issuer console for institutions."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.guard import enforce_rate, issuer_for_token
from app.store import seal

router = APIRouter()


def _env():
    from app.server import env
    return env


def _issuer(request: Request) -> str | None:
    return issuer_for_token(request.cookies.get("issuer", ""))


@router.get("/s/{code}", response_class=HTMLResponse)
async def seal_page(code: str):
    """What a phone camera opens when it scans a seal: who signed it, when, and the exact text they sent."""
    rec = await asyncio.to_thread(seal.load, code)
    status = {"valid": False, "why": "not_issued"} if rec is None else await asyncio.to_thread(seal.verify_record, rec)
    who = seal.issuers().get(rec["payload"]["iss"], {}) if rec else {}
    return HTMLResponse(_env().get_template("seal.html").render(code=code.upper(), rec=rec, status=status, who=who),
                        status_code=200 if rec else 404)


@router.get("/issuer/login", response_class=HTMLResponse)
async def issuer_login_page(error: str | None = None):
    return _env().get_template("issuer_login.html").render(error=error)


@router.post("/issuer/login")
async def issuer_login(request: Request, token: str = Form("")):
    enforce_rate(request, "login")
    if issuer_for_token(token.strip()) is None:
        return HTMLResponse(_env().get_template("issuer_login.html").render(error="That issuer token is not valid."), status_code=403)
    resp = RedirectResponse("/issuer", status_code=303)
    resp.set_cookie("issuer", token.strip(), httponly=True, samesite="strict", max_age=12 * 3600, path="/",
                    secure=request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https")
    return resp


@router.get("/issuer", response_class=HTMLResponse)
async def issuer_console(request: Request, new: str | None = None, video: str | None = None):
    who = _issuer(request)
    if who is None:
        return RedirectResponse("/issuer/login", status_code=303)
    seals = [s for s in await asyncio.to_thread(seal.list_seals) if s["issuer"] == who]
    fresh = await asyncio.to_thread(seal.load, new) if new else None
    sms = None
    if fresh and fresh["payload"]["type"] == "sms":
        from app.render.notice import sms_text
        sms = sms_text(fresh["text"], fresh["payload"]["code"])
    from app.media import live_ref
    videos = await asyncio.to_thread(live_ref.live_refs)
    return _env().get_template("issuer.html").render(issuer=seal.issuers()[who], issuer_id=who, seals=seals, fresh=fresh, sms=sms,
                                                     videos=[x for x in videos if x["official"] == seal.issuers()[who]["name"]], new_video=video)


@router.post("/issuer/seal")
async def issuer_seal(request: Request, doc_type: str = Form("notice"), title: str = Form(""), body: str = Form(""), ref: str = Form("")):
    who = _issuer(request)
    if who is None:
        raise HTTPException(403, "Sign in as an issuer at /issuer/login.")
    if not title.strip() or not body.strip() or len(body) > 4000:
        raise HTTPException(400, "A title and a body (up to 4,000 characters) are needed.")
    from app.render import notice
    if doc_type == "sms":
        rec = await asyncio.to_thread(notice.issue_sms, who, title, body)
    else:
        rec = await asyncio.to_thread(notice.issue_notice, who, title, body, ref)
        pdf, png = await notice.render_notice(rec, rec.get("layout"))
        code = rec["payload"]["code"]
        (seal.REGISTRY / f"{code}.pdf").write_bytes(pdf)
        (seal.REGISTRY / f"{code}.png").write_bytes(png)
    from app.store import vault
    vault.append_event("seal_issued", {"code": rec["payload"]["code"], "issuer": who, "type": rec["payload"]["type"]})
    return RedirectResponse(f"/issuer?new={rec['payload']['code']}", status_code=303)


@router.post("/issuer/video")
async def issuer_video(request: Request, title: str = Form(""), consent: str = Form(""), language: str = Form(""),
                       video: UploadFile = File(...)):
    """Register a short official video live, so Source-Match can find it inside any edited copy."""
    who = _issuer(request)
    if who is None:
        raise HTTPException(403, "Sign in as an issuer at /issuer/login.")
    if consent != "yes":
        raise HTTPException(400, "Tick the consent box: the person in the video agrees to its use for this demonstration.")
    enforce_rate(request, "register")
    import tempfile
    from pathlib import Path
    from app.media import live_ref
    data = await video.read(80 * 1024 * 1024 + 1)
    if len(data) > 80 * 1024 * 1024:
        raise HTTPException(413, "Video too large (80 MB max).")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / ("upload" + (Path(video.filename or "v.mp4").suffix.lower()[:6] or ".mp4"))
        src.write_bytes(data)
        try:
            ref = await asyncio.to_thread(live_ref.register, src, seal.issuers()[who]["name"], title, language or None)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"Could not register the video ({type(e).__name__}). Try a shorter clip.")
    from app.store import vault
    vault.append_event("video_registered", {"issuer": who, "ref": ref["id"]})
    return RedirectResponse(f"/issuer?video={ref['id']}", status_code=303)


@router.post("/issuer/video/{ref_id}/edit")
async def issuer_video_edit(ref_id: str, request: Request, voice: UploadFile = File(...)):
    """A labelled test edit: the registered footage with a new recording by the same consenting person."""
    if _issuer(request) is None:
        raise HTTPException(403, "Sign in as an issuer at /issuer/login.")
    enforce_rate(request, "register")
    import tempfile
    from pathlib import Path
    from app.media import live_ref
    data = await voice.read(40 * 1024 * 1024 + 1)
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(413, "Recording too large (40 MB max).")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / ("voice" + (Path(voice.filename or "v.m4a").suffix.lower()[:6] or ".m4a"))
        src.write_bytes(data)
        try:
            out = await asyncio.to_thread(live_ref.make_test_edit, ref_id, src)
        except KeyError:
            raise HTTPException(404, "That video is not registered (or has expired).")
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"Could not make the test edit ({type(e).__name__}).")
    return FileResponse(out, media_type="video/mp4", filename=out.name)


@router.post("/issuer/video/{ref_id}/delete")
async def issuer_video_delete(ref_id: str, request: Request):
    if _issuer(request) is None:
        raise HTTPException(403, "Sign in as an issuer at /issuer/login.")
    from app.media import live_ref
    await asyncio.to_thread(live_ref.remove, ref_id)
    return RedirectResponse("/issuer", status_code=303)


@router.get("/issuer/seal/{code}.{ext}")
async def issuer_file(code: str, ext: str, request: Request):
    who = _issuer(request)
    rec = seal.load(code)
    if who is None or rec is None or rec["payload"]["iss"] != who or ext not in ("pdf", "png"):
        raise HTTPException(404)
    p = seal.REGISTRY / f"{code.upper()}.{ext}"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, filename=f"{who}-notice-{code.upper()}.{ext}")
