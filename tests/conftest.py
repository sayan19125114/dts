"""Pytest fixtures: isolated SQLite DB + TestClient with auth helpers."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.security import reset_throttle_store
from app.db.base import Base
import app.db.models  # noqa
from app.db.session import get_db
from app.main import create_app


@pytest.fixture()
def tmp_dirs(tmp_path):
    upload = tmp_path / "uploads"
    upload.mkdir()
    dbfile = tmp_path / "test.db"
    return {"upload": str(upload), "db": str(dbfile)}


@pytest.fixture()
def client(tmp_dirs, monkeypatch):
    # point settings to temp upload dir via env + clear cache
    from app.core import config as cfgmod

    monkeypatch.setenv("UPLOAD_DIR", tmp_dirs["upload"])
    # create engine for temp db
    url = f"sqlite:///{tmp_dirs['db']}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, rec):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
        finally:
            cur.close()

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    # override upload dir setting by monkeypatching get_settings? simplest: patch config module
    # create app after patching env: need fresh settings cache
    cfgmod.get_settings.cache_clear()
    # ensure upload dir env is read: our Settings uses field upload_dir mapped from env? pydantic-settings maps case-insensitive; UPLOAD_DIR matches upload_dir
    os.environ["UPLOAD_DIR"] = tmp_dirs["upload"]
    cfgmod.get_settings.cache_clear()
    # also patch get_upload_dir usage: attachments uses get_settings().upload_dir, so ensure settings picks it up
    # force settings object
    s = cfgmod.get_settings()
    s.upload_dir = tmp_dirs["upload"]

    app = create_app()

    def _override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    reset_throttle_store()
    c = TestClient(app)
    # attach helpers
    c._testing_session_factory = TestingSession  # type: ignore
    yield c
    cfgmod.get_settings.cache_clear()
    if "UPLOAD_DIR" in os.environ:
        del os.environ["UPLOAD_DIR"]
    engine.dispose()


def _register_and_login(client: TestClient, username="admin", email="admin@example.com", password="AdminPassword123!", global_role="admin", display="Admin"):
    """Create user directly in DB then login via API. Returns (user_id, csrf)."""
    SessionFactory = client._testing_session_factory  # type: ignore
    from app.core.security import hash_password
    from app.db.models import User
    import datetime as dt

    db = SessionFactory()
    try:
        u = User(
            username=username.lower(),
            email=email.lower(),
            display_name=display,
            password_hash=hash_password(password),
            global_role=global_role,
            is_active=True,
            created_at=dt.datetime.now(dt.timezone.utc),
            updated_at=dt.datetime.now(dt.timezone.utc),
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        uid = u.id
    finally:
        db.close()
    r = client.post("/api/v1/auth/login", json={"username_or_email": username, "password": password})
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    # TestClient stores cookies automatically; also set header for subsequent writes
    client.headers.update({"X-CSRF-Token": csrf})
    return uid, csrf


@pytest.fixture()
def admin_client(client):
    _register_and_login(client, username="admin", email="admin@example.com", password="AdminPassword123!")
    return client


@pytest.fixture()
def admin_user_id(client):
    # used when need fresh client separation; creates admin on given client
    uid, _ = _register_and_login(client)
    return uid
