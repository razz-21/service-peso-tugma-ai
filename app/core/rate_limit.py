"""MongoDB TTL-backed fixed-window request throttling.

Counters live in MongoDB, not process memory: the backend runs as Vercel
serverless functions, so any in-memory limiter would reset on every cold start
and give each parallel instance its own counters — an attacker spreading
requests across instances would never be throttled. A shared store is mandatory;
we reuse the MongoDB we already run (see app/core/rate_limit_models.py).
"""

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from pymongo import ReturnDocument

from app.api.deps import get_current_user_optional
from app.api.v1.routes.users.users_models import User
from app.core.config import settings
from app.core.rate_limit_models import RateLimitCounter

logger = logging.getLogger(__name__)


def parse_rate(spec: str) -> tuple[int, int]:
    """Parse a ``"<count>/<window>"`` spec into ``(count, window_seconds)``.

    The window is an integer suffixed ``s`` (seconds), ``m`` (minutes), or
    ``h`` (hours). ``parse_rate("20/15m") == (20, 900)``.
    """
    count_str, _, window_str = spec.partition("/")
    count = int(count_str)
    unit = window_str[-1]
    value = int(window_str[:-1])
    multiplier = {"s": 1, "m": 60, "h": 3600}[unit]
    return count, value * multiplier


def client_ip(request: Request) -> str:
    """Resolve the real caller IP behind Vercel's edge proxy.

    ``request.client.host`` is the proxy's address on Vercel, which would put
    every caller worldwide into one shared bucket. ``x-vercel-forwarded-for`` is
    set by Vercel's edge and cannot be spoofed by the client; prefer it. Fall
    back to ``x-real-ip``, then the first hop of the client-appendable
    ``x-forwarded-for``, then the socket address.
    """
    for header in ("x-vercel-forwarded-for", "x-real-ip"):
        value = request.headers.get(header)
        if value:
            return value.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def email_key(email: str) -> str:
    """Bucket key for the per-account login limiter.

    Hashed so a raw email (PII) never lands in the rate-limit collection.
    """
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]
    return f"login:email:{digest}"


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int  # seconds until the window resets
    count: int  # requests recorded in the current window (after this call)
    first_trip: bool  # True only on the request that first crossed the threshold


async def hit(key: str, limit: int, window_seconds: int) -> RateLimitResult:
    """Consume one unit from ``key``'s bucket and report whether it is allowed.

    A single atomic aggregation-pipeline upsert (MongoDB 4.2+): reset-if-expired
    and increment-if-live in one operation, so concurrent serverless instances
    never race on a read-then-write. The pipeline compares ``expires_at`` against
    ``now`` itself, so a stale document the TTL monitor has not yet reaped is
    treated as a fresh window — enforcement never trusts the TTL index.
    """
    now = datetime.now(UTC)
    reset_at = now + timedelta(seconds=window_seconds)

    # Beanie 2.x exposes the native PyMongo async collection here.
    coll = RateLimitCounter.get_pymongo_collection()

    doc = await coll.find_one_and_update(
        {"_id": key},
        [
            {
                "$set": {
                    # Live window → increment. Expired or absent → restart at 1.
                    "count": {
                        "$cond": [
                            {"$gt": ["$expires_at", now]},
                            {"$add": [{"$ifNull": ["$count", 0]}, 1]},
                            1,
                        ]
                    },
                    "expires_at": {
                        "$cond": [
                            {"$gt": ["$expires_at", now]},
                            "$expires_at",
                            reset_at,
                        ]
                    },
                }
            }
        ],
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    # `upsert=True` with `ReturnDocument.AFTER` always returns the post-update doc.
    assert doc is not None

    count = int(doc["count"])
    expires = doc["expires_at"]
    if expires.tzinfo is None:  # BSON datetimes come back naive UTC
        expires = expires.replace(tzinfo=UTC)

    return RateLimitResult(
        allowed=count <= limit,
        limit=limit,
        remaining=max(0, limit - count),
        retry_after=max(1, int((expires - now).total_seconds())),
        count=count,
        first_trip=count == limit + 1,
    )


async def peek(key: str, limit: int) -> RateLimitResult:
    """Read-only view of a bucket — checks the limit without consuming from it.

    Used on the login email key so that merely *checking* whether an account is
    locked out does not itself count against the account. ``allowed`` is True
    while the bucket has room for one more unit (``count < limit``); a stale
    (expired-but-unreaped) document is treated as empty via the same guard as
    :func:`hit`.
    """
    now = datetime.now(UTC)
    doc = await RateLimitCounter.get_pymongo_collection().find_one({"_id": key})

    count = 0
    retry_after = 1
    if doc is not None:
        expires = doc["expires_at"]
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires > now:  # ignore an expired window the TTL monitor kept around
            count = int(doc["count"])
            retry_after = max(1, int((expires - now).total_seconds()))

    return RateLimitResult(
        allowed=count < limit,
        limit=limit,
        remaining=max(0, limit - count),
        retry_after=retry_after,
        count=count,
        first_trip=False,
    )


async def reset(key: str) -> None:
    """Clear a bucket — forgives an account's failures after a good login."""
    await RateLimitCounter.get_pymongo_collection().delete_one({"_id": key})


def enforce(result: RateLimitResult) -> None:
    """Raise 429 with ``Retry-After`` / ``X-RateLimit-*`` headers when blocked.

    The response passes back out through ``CORSMiddleware`` (added before the
    router in app/main.py), so the browser can read the 429 body rather than
    seeing an opaque CORS error.
    """
    if result.allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests. Please try again later.",
        headers={
            "Retry-After": str(result.retry_after),
            "X-RateLimit-Limit": str(result.limit),
            "X-RateLimit-Remaining": "0",
        },
    )


def rate_limit(scope: str, spec: str, by: str = "ip") -> Callable[..., Awaitable[None]]:
    """Build a FastAPI dependency that throttles a route.

    Applied per-route rather than as global middleware so only the endpoints that
    need protection pay for the MongoDB write. ``by`` is ``"ip"`` or ``"user"``;
    a user-keyed limiter falls back to the IP for an unauthenticated caller.
    Fails open on store errors (matching audit_logs_service.record_audit): if
    MongoDB is down the limiter is briefly ineffective, but the system stays up —
    and login can't read the user collection to authenticate anyway.
    """
    limit, window_seconds = parse_rate(spec)

    async def dependency(
        request: Request,
        current_user: Annotated[User | None, Depends(get_current_user_optional)] = None,
    ) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return
        ident = str(current_user.id) if by == "user" and current_user else client_ip(request)
        try:
            result = await hit(f"{scope}:{by}:{ident}", limit, window_seconds)
        except Exception:
            logger.exception("rate limiter unavailable; failing open for scope=%s", scope)
            return
        enforce(result)

    return dependency
