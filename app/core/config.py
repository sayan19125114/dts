"""Typed settings with safe defaults."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Defect Tracking System"
    debug: bool = False
    secret_key: str = "change-me-in-production-use-long-random-secret"
    database_url: str = "sqlite:///./defect_tracker.db"
    # Session / cookie
    session_cookie_name: str = "dts_session"
    session_expire_seconds: int = 8 * 3600  # 8h
    session_idle_timeout_seconds: int = 30 * 60  # 30 min idle
    cookie_secure: bool = False  # True in production behind HTTPS
    cookie_samesite: str = "lax"
    # CSRF
    csrf_header_name: str = "X-CSRF-Token"
    # CORS
    cors_origins: List[str] = []
    trusted_hosts: List[str] = ["*"]
    # Uploads
    upload_dir: str = "./storage/uploads"
    max_upload_mb: int = 10
    allowed_extensions: List[str] = ["png", "jpg", "jpeg", "webp", "pdf", "txt", "log"]
    allowed_mime: List[str] = [
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/pdf",
        "text/plain",
    ]
    # Auth throttling
    login_max_attempts: int = 5
    login_window_seconds: int = 300
    # Pagination
    default_page_size: int = 20
    max_page_size: int = 100
    # Feature flags (Phases 2-3). High-risk enforcement stays off until piloted;
    # flags are not the only security boundary — server authz always applies.
    feature_custom_workflows: bool = False
    feature_custom_fields: bool = True
    feature_links: bool = True
    feature_bulk_ops: bool = True
    feature_notifications: bool = True
    feature_saved_views: bool = True
    feature_mentions: bool = True
    # Phase 4 flags
    feature_sla: bool = True
    feature_analytics: bool = True
    feature_releases: bool = True
    feature_async_exports: bool = True
    # SLA scheduler/worker. Off by default so imports/tests never spawn a thread;
    # enable in production and/or drive via POST /api/v1/admin/jobs/run.
    sla_scheduler_enabled: bool = False
    sla_tick_seconds: int = 300
    default_timezone: str = "UTC"
    # Exports
    export_dir: str = "./storage/exports"
    export_expire_seconds: int = 3600
    export_inline: bool = True  # process small exports synchronously; worker handles the rest


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_upload_dir() -> Path:
    s = get_settings()
    p = Path(s.upload_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p
