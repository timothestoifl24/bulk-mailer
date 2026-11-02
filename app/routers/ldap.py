"""Old /ldap URLs.

LDAP connections are set up on the Authentication page since 2.0, and
directory imports run through the import wizard. Old links and bookmarks land
on their new home.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import LdapProfile
from ..security import require_user

router = APIRouter(prefix="/ldap", dependencies=[Depends(require_user)])


@router.get("")
def index():
    return RedirectResponse("/settings/auth", status_code=308)


@router.get("/{profile_id}")
def detail(profile_id: int, db: Session = Depends(get_db)):
    # The target is built from the stored provider, never from the request
    # path, so nothing the caller sends ends up in the Location header.
    provider = db.get(LdapProfile, profile_id)
    if provider is None:
        return RedirectResponse("/settings/auth", status_code=308)
    return RedirectResponse(f"/settings/auth/ldap/{provider.id}", status_code=308)
