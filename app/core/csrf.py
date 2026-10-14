"""CSRF helpers: synchronizer token per session."""
from __future__ import annotations

from fastapi import Header, Request


def csrf_token_from_header(request: Request, header_name: str = "X-CSRF-Token") -> str | None:
    # header names are case-insensitive; starlette normalizes to lower
    token = request.headers.get(header_name)
    if token is None:
        token = request.headers.get(header_name.lower())
    return token
