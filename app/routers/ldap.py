"""Old /ldap URLs.

LDAP connections are set up on the Authentication page since 2.0, and
directory imports run through the import wizard. Old links and bookmarks land
on their new home.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from ..security import require_user

router = APIRouter(prefix="/ldap", dependencies=[Depends(require_user)])


@router.get("")
def index():
    return RedirectResponse("/settings/auth", status_code=308)


@router.get("/{profile_id}")
def detail(profile_id: int):
    return RedirectResponse(f"/settings/auth/ldap/{profile_id}", status_code=308)
