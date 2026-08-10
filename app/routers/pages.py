"""Mobile-first UI pages — responsive web app rendered by Jinja2 (mobile, tablet, PC).

Pages:
- /        landing (participant or admin)
- /admin   admin console
- /join    participant join form
- /room/{competition_id}  participant room (waiting/question/results)

All real-time logic runs in static/js/* over the existing REST + WebSocket API.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["ui"])

templates = Jinja2Templates(directory="templates")
templates.env.globals["static_version"] = (
    os.environ.get("RENDER_GIT_COMMIT", "")[:10] or "dev"
)


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    """Landing page: choose between playing and administering."""
    return templates.TemplateResponse(
        request=request, name="index.html", context={}
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request) -> HTMLResponse:
    """Admin console."""
    return templates.TemplateResponse(
        request=request, name="admin.html", context={}
    )


@router.get("/join", response_class=HTMLResponse)
async def join_page(request: Request) -> HTMLResponse:
    """Participant join form."""
    return templates.TemplateResponse(
        request=request, name="join.html", context={}
    )


@router.get("/room/{competition_id}", response_class=HTMLResponse)
async def room_page(competition_id: str, request: Request) -> HTMLResponse:
    """Participant room for one competition."""
    return templates.TemplateResponse(
        request=request,
        name="room.html",
        context={"competition_id": competition_id},
    )