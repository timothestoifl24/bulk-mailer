"""Small admin CLI, mainly for getting back in when nobody can sign in.

    python -m app.cli list-users
    python -m app.cli set-password admin
    python -m app.cli create-admin alice --password ...
    python -m app.cli disable-ldap-login

In a container:  podman exec -it <container> python -m app.cli list-users
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import func, select

from .db import session_scope
from .models import User
from .security import hash_password
from .services import settings_store

MIN_PASSWORD_LENGTH = 8


def _read_password(supplied: str | None) -> str:
    password = supplied or getpass.getpass("New password: ")
    if not supplied:
        if password != getpass.getpass("Repeat: "):
            raise SystemExit("The passwords do not match.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"The password must be at least {MIN_PASSWORD_LENGTH} characters.")
    return password


def list_users(_args) -> int:
    with session_scope() as db:
        users = list(db.scalars(select(User).order_by(User.username)))
        if not users:
            print("No accounts exist. The next start will create one from ADMIN_PASSWORD.")
            return 0
        width = max(len(u.username) for u in users)
        print(f"{'USERNAME'.ljust(width)}  SOURCE  RIGHTS  STATE     LAST SIGN-IN")
        for user in users:
            print(
                f"{user.username.ljust(width)}  "
                f"{user.auth_source:<6}  "
                f"{'admin' if user.is_admin else '-':<6}  "
                f"{'active' if user.is_active else 'disabled':<8}  "
                f"{user.last_login_at.strftime('%Y-%m-%d %H:%M') if user.last_login_at else 'never'}"
            )
    return 0


def set_password(args) -> int:
    with session_scope() as db:
        user = db.scalar(select(User).where(func.lower(User.username) == args.username.lower()))
        if user is None:
            raise SystemExit(f"No account named {args.username!r}.")
        if user.auth_source != "local":
            raise SystemExit(
                f"{user.username!r} signs in through the directory; there is no local password. "
                "Use create-admin to add a local account instead."
            )
        user.password_hash = hash_password(_read_password(args.password))
        user.is_active = True
        print(f"Password updated for {user.username!r}.")
    return 0


def create_admin(args) -> int:
    with session_scope() as db:
        existing = db.scalar(select(User).where(func.lower(User.username) == args.username.lower()))
        if existing is not None:
            raise SystemExit(f"{args.username!r} already exists; use set-password.")
        db.add(
            User(
                username=args.username,
                password_hash=hash_password(_read_password(args.password)),
                auth_source="local",
                is_admin=True,
                display_name=args.display_name or "",
            )
        )
        print(f"Created local administrator {args.username!r}.")
    return 0


def promote(args) -> int:
    with session_scope() as db:
        user = db.scalar(select(User).where(func.lower(User.username) == args.username.lower()))
        if user is None:
            raise SystemExit(f"No account named {args.username!r}.")
        user.is_admin = True
        user.is_active = True
        print(f"{user.username!r} is now an administrator.")
    return 0


def disable_ldap_login(_args) -> int:
    """Escape hatch when a bad LDAP setting is keeping everyone out."""
    with session_scope() as db:
        settings_store.set_value(db, "auth_ldap_enabled", "0")
        admins = db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.is_admin.is_(True), User.is_active.is_(True), User.auth_source == "local")
        )
    print("LDAP sign-in disabled.")
    if not admins:
        print("Warning: no active local administrator exists. Run create-admin next.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-users", help="show every account").set_defaults(func=list_users)

    p = sub.add_parser("set-password", help="set a local account's password")
    p.add_argument("username")
    p.add_argument("--password", help="read from the terminal if omitted")
    p.set_defaults(func=set_password)

    p = sub.add_parser("create-admin", help="create a local administrator")
    p.add_argument("username")
    p.add_argument("--password", help="read from the terminal if omitted")
    p.add_argument("--display-name", default="")
    p.set_defaults(func=create_admin)

    p = sub.add_parser("promote", help="grant admin rights to an existing account")
    p.add_argument("username")
    p.set_defaults(func=promote)

    sub.add_parser(
        "disable-ldap-login", help="turn LDAP sign-in off (use when it locks everyone out)"
    ).set_defaults(func=disable_ldap_login)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
