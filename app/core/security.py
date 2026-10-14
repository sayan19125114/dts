"""Password hashing (Argon2id) + session token helpers + login throttling."""
from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict
from typing import Dict, List, Tuple

from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    if not (12 <= len(password) <= 128):
        raise ValueError("Password must be 12-128 characters")
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hash.verify(password, password_hash)
    except Exception:
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _password_hash.check(password_hash) is False
    except Exception:
        return True


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_user_agent(ua: str | None) -> str | None:
    if not ua:
        return None
    return hashlib.sha256(ua.encode()[:500]).hexdigest()[:32]


# ---- login throttling (in-memory, per-process) ----
# key -> list[timestamps]
_attempts: Dict[str, List[float]] = defaultdict(list)


def _prune(key: str, window: int) -> None:
    now = time.time()
    _attempts[key] = [t for t in _attempts[key] if now - t < window]


def is_rate_limited(key: str, max_attempts: int, window: int) -> bool:
    _prune(key, window)
    return len(_attempts[key]) >= max_attempts


def record_failed_login(key: str) -> None:
    _attempts[key].append(time.time())


def clear_login_attempts(key: str) -> None:
    _attempts.pop(key, None)


def reset_throttle_store() -> None:
    _attempts.clear()
