"""System: a read-only summary of how this instance is set up.

Admin only (see main.py). Nothing here is editable; each row links to where
it can be changed, and secrets are only ever reported as set or not set.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import system_info
from ..web import render

router = APIRouter(prefix="/settings/system")


@router.get("")
def index(request: Request, db: Session = Depends(get_db)):
    info = system_info.collect(db)
    return render(request, "system.html", {"info": info, "as_text": info.as_text()})
