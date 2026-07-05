"""
core/security.py
API key authentication + per-key in-memory rate limiting.

Keys are stored as SHA-256 hashes in SQLite — the raw key is shown once
at creation and never stored. Rate limiting uses a sliding-window counter
per hashed key ID, reset every 60 seconds (configurable).

FIXES applied:
- asyncio.Lock instead of threading.Lock (was blocking the event loop)
- Dead-key eviction in _RATE_WINDOWS to prevent unbounded memory growth
- Keys with zero timestamps are pruned entirely from the dict

NOTE: single-process in-memory rate limiting. workers=1 in production
or add Redis for multi-instance deployments.
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from collections import defaultdict

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from core.logging import get_logger

logger = get_logger(__name__)

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# asyncio.Lock — not threading.Lock — so we don't block the event loop.
# threading.Lock.acquire() is a blocking call; inside an async handler it
# would stall the entire uvicorn worker for the lock duration.
_rate_lock = asyncio.Lock()
_RATE_WINDOWS: dict[str, list[float]] = defaultdict(list)

# Evict dead keys periodically to prevent unbounded dict growth.
# A key is "dead" if it has had no requests in the last 5 minutes.
_DEAD_KEY_TTL = 300.0
_last_cleanup = 0.0
_CLEANUP_INTERVAL = 60.0  # run cleanup at most once per minute


def hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw key — used as storage/lookup identifier."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Returns (raw_key, hashed_key). raw_key shown once, hash stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_key(raw)


async def check_rate_limit(key_hash: str, limit_per_minute: int) -> None:
    """Raises HTTP 429 if this key has exceeded its per-minute call budget.
    Uses a sliding 60-second window.
    """
    global _last_cleanup
    now = time.monotonic()
    window_start = now - 60.0

    async with _rate_lock:
        # Periodic dead-key cleanup — prevents unbounded _RATE_WINDOWS growth
        # for revoked/unused keys that never make another request.
        if now - _last_cleanup > _CLEANUP_INTERVAL:
            dead_cutoff = now - _DEAD_KEY_TTL
            dead_keys = [
                k for k, ts in _RATE_WINDOWS.items()
                if not ts or max(ts) < dead_cutoff
            ]
            for k in dead_keys:
                del _RATE_WINDOWS[k]
            _last_cleanup = now

        # Evict timestamps outside current window
        _RATE_WINDOWS[key_hash] = [t for t in _RATE_WINDOWS[key_hash] if t > window_start]
        count = len(_RATE_WINDOWS[key_hash])

        if count >= limit_per_minute:
            logger.warning("rate_limit_exceeded", extra={"extra": {
                "key_hash_prefix": key_hash[:8],
                "count": count,
                "limit": limit_per_minute,
            }})
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limit_exceeded",
                    "limit_per_minute": limit_per_minute,
                    "retry_after_seconds": 60,
                },
                headers={"Retry-After": "60"},
            )

        _RATE_WINDOWS[key_hash].append(now)


async def require_api_key(
    request: Request,
    api_key_header: str | None = Security(_API_KEY_HEADER),
) -> dict:
    """FastAPI dependency — validates X-API-Key against DB, checks rate limit."""
    from core.config import get_settings
    from core.database import get_key_by_hash

    if not api_key_header:
        raise HTTPException(
            status_code=401,
            detail={"error": "missing_api_key", "hint": "Set X-API-Key header"},
        )

    settings = get_settings()
    if secrets.compare_digest(api_key_header.encode(), settings.admin_api_key.encode()):
        return {
            "key_id": "admin",
            "label": "admin",
            "rate_limit_per_minute": 999999,
            "is_active": True,
        }

    key_hash = hash_key(api_key_header)
    key_record = await get_key_by_hash(key_hash)

    if key_record is None or not key_record["is_active"]:
        logger.warning("auth_failed", extra={"extra": {
            "key_hash_prefix": key_hash[:8],
            "path": str(request.url.path),
        }})
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_api_key"},
        )

    await check_rate_limit(key_hash, key_record["rate_limit_per_minute"])
    return key_record


def validate_admin_key(api_key_header: str | None, admin_key: str) -> None:
    """Constant-time comparison — prevents timing-based key oracle attacks."""
    if not api_key_header or not secrets.compare_digest(
        api_key_header.encode(), admin_key.encode()
    ):
        raise HTTPException(
            status_code=403,
            detail={"error": "admin_key_required"},
        )
