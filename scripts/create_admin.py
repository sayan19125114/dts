"""Safe initial-admin bootstrap. Refuses to silently reset existing admin."""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.security import hash_password
from app.db.base import Base
from app.db.models import ProjectMember, User
from app.db.session import get_engine, get_session_factory


def main() -> int:
    ap = argparse.ArgumentParser(description="Create initial admin (refuses if admin exists unless --force with explicit confirmation)")
    ap.add_argument("--username", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--display-name", default="Administrator")
    ap.add_argument("--password", default=None, help="If omitted, prompt securely")
    ap.add_argument("--force-reset", action="store_true", help="Allow resetting existing admin password (requires confirmation)")
    args = ap.parse_args()

    password = args.password or getpass.getpass("Admin password (12-128 chars): ")
    if not (12 <= len(password) <= 128):
        print("Password must be 12-128 characters", file=sys.stderr)
        return 2
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        existing_admin = db.execute(select(User).where(User.global_role == "admin")).scalars().first()
        username = args.username.strip().lower()
        email = args.email.strip().lower()
        existing = db.execute(
            select(User).where((User.username == username) | (User.email == email))
        ).scalar_one_or_none()
        now = dt.datetime.now(dt.timezone.utc)
        if existing_admin is not None and existing is None:
            print(f"An admin already exists ({existing_admin.username}). Refusing to create another via bootstrap; ask an admin to create users.", file=sys.stderr)
            return 3
        if existing is not None:
            if existing.global_role == "admin" and not args.force_reset:
                print("Admin user already exists. Refusing to silently reset. Re-run with --force-reset to rotate password explicitly.", file=sys.stderr)
                return 3
            if not args.force_reset:
                print("User already exists. Refusing to overwrite without --force-reset.", file=sys.stderr)
                return 3
            confirm = input(f"Type RESET to confirm resetting {existing.username}: ")
            if confirm.strip() != "RESET":
                print("Aborted.", file=sys.stderr)
                return 4
            existing.password_hash = hash_password(password)
            existing.is_active = True
            existing.global_role = "admin"
            existing.updated_at = now
            db.commit()
            print(f"Admin {existing.username} password rotated.")
            return 0
        u = User(
            username=username,
            email=email,
            display_name=args.display_name,
            password_hash=hash_password(password),
            global_role="admin",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(u)
        db.commit()
        print(f"Admin {username} created.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
