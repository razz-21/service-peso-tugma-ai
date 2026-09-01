import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.routes.users import users_service
from app.api.v1.routes.users.users_models import User
from app.api.v1.routes.users.users_schemas import UserCreate
from app.core import rate_limit
from app.core.config import settings
from app.core.rate_limit import email_key, enforce, hit, parse_rate, peek, reset
from app.core.rate_limit_models import RateLimitCounter
from app.main import create_app

# Every test in this module talks to MongoDB (like the rest of the suite) and
# needs the limiter switched on regardless of the ambient environment.


@pytest.fixture(autouse=True)
async def _rate_limit_env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    import app.db.mongodb as mongodb

    # pytest-asyncio runs each test on a fresh event loop, but AsyncMongoClient is
    # pinned to the loop it was created on. Abandon any client from a previous
    # test's (now-dead) loop and rebind a fresh one to this test's loop.
    mongodb._client = None
    await mongodb.init_mongo()
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    coll = RateLimitCounter.get_pymongo_collection()
    await coll.delete_many({})
    yield
    await coll.delete_many({})


# --- parse_rate ------------------------------------------------------------


def test_parse_rate_units() -> None:
    assert parse_rate("20/15m") == (20, 900)
    assert parse_rate("5/1h") == (5, 3600)
    assert parse_rate("10/30s") == (10, 30)


# --- hit() core semantics --------------------------------------------------


async def test_hit_allows_up_to_limit_then_blocks() -> None:
    key = f"test:{uuid4()}"
    results = [await hit(key, limit=3, window_seconds=60) for _ in range(4)]

    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results] == [2, 1, 0, 0]
    # The 4th request is the one that first crossed the threshold.
    assert results[3].first_trip is True
    assert results[3].retry_after >= 1


async def test_hit_restarts_window_after_expiry() -> None:
    key = f"test:{uuid4()}"
    # Fill the bucket, then rewind its window into the past directly (no sleep).
    await hit(key, limit=2, window_seconds=60)
    blocked = await hit(key, limit=1, window_seconds=60)
    assert blocked.allowed is False

    coll = RateLimitCounter.get_pymongo_collection()
    await coll.update_one(
        {"_id": key},
        {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
    )

    # The expired window is treated as fresh: count restarts at 1 and is allowed,
    # proving enforcement never trusts the (lazy) TTL reaper.
    restarted = await hit(key, limit=1, window_seconds=60)
    assert restarted.allowed is True
    assert restarted.count == 1


async def test_concurrent_hits_are_atomic() -> None:
    # The headline correctness test: 50 concurrent hits against one key with a
    # limit of 10 must admit *exactly* 10. This is what proves the aggregation-
    # pipeline upsert has no read-then-write race between callers.
    key = f"test:{uuid4()}"
    results = await asyncio.gather(*(hit(key, limit=10, window_seconds=60) for _ in range(50)))

    allowed = sum(1 for r in results if r.allowed)
    assert allowed == 10

    doc = await RateLimitCounter.get_pymongo_collection().find_one({"_id": key})
    assert doc is not None
    assert doc["count"] == 50  # every attempt was counted, none lost


# --- peek() ----------------------------------------------------------------


async def test_peek_does_not_consume() -> None:
    key = f"test:{uuid4()}"
    for _ in range(5):
        result = await peek(key, limit=3)
        assert result.allowed is True  # never blocks; never increments

    assert await RateLimitCounter.get_pymongo_collection().find_one({"_id": key}) is None


async def test_peek_blocks_once_bucket_is_full() -> None:
    key = f"test:{uuid4()}"
    for _ in range(3):
        await hit(key, limit=3, window_seconds=60)
    # Bucket is full (3/3): a read-only peek now reports blocked.
    assert (await peek(key, limit=3)).allowed is False


async def test_reset_clears_bucket() -> None:
    key = f"test:{uuid4()}"
    await hit(key, limit=1, window_seconds=60)
    await hit(key, limit=1, window_seconds=60)  # blocked now
    await reset(key)
    assert (await hit(key, limit=1, window_seconds=60)).allowed is True


# --- enforce() response contract -------------------------------------------


async def test_enforce_raises_429_with_retry_after() -> None:
    key = f"test:{uuid4()}"
    await hit(key, limit=1, window_seconds=60)
    blocked = await hit(key, limit=1, window_seconds=60)

    with pytest.raises(Exception) as exc_info:
        enforce(blocked)
    exc = exc_info.value
    assert getattr(exc, "status_code", None) == 429
    assert "Retry-After" in exc.headers  # type: ignore[attr-defined]
    assert int(exc.headers["Retry-After"]) >= 1  # type: ignore[attr-defined]


# --- fail-open on store unavailability -------------------------------------


async def test_dependency_fails_open_when_store_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("mongo down")

    monkeypatch.setattr(rate_limit, "hit", boom)
    dependency = rate_limit.rate_limit("test", "1/1h")

    class _Req:
        headers: dict[str, str] = {}
        client = None

    # Must return normally (no 429) even though the store is unreachable.
    assert await dependency(_Req(), current_user=None) is None  # type: ignore[arg-type]


# --- login endpoint: per-account lockout counts failures only --------------


@pytest.fixture
async def make_user() -> AsyncIterator[Callable[[str, str], Awaitable[User]]]:
    created: list[User] = []

    async def _make(email: str, password: str) -> User:
        user = await users_service.create_user(
            UserCreate(id=uuid4(), fullname="Rate Limit Test", email=email, password=password)
        )
        created.append(user)
        return user

    yield _make

    for user in created:
        await users_service.delete_user(user)


async def _login(client: AsyncClient, email: str, password: str) -> int:
    response = await client.post(
        f"{settings.API_V1_PREFIX}/auth/login",
        data={"username": email, "password": password},
    )
    return response.status_code


async def test_login_email_lockout_after_five_failures(
    make_user: Callable[[str, str], Awaitable[User]],
) -> None:
    email = f"lockout-{uuid4().hex}@example.com"
    other = f"other-{uuid4().hex}@example.com"
    await make_user(email, "correct-horse-battery")
    await make_user(other, "correct-horse-battery")

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Five wrong-password attempts all authenticate-and-fail with 401.
        for _ in range(5):
            assert await _login(client, email, "wrong-password") == 401
        # The sixth is blocked by the per-account limiter before authenticating.
        blocked = await client.post(
            f"{settings.API_V1_PREFIX}/auth/login",
            data={"username": email, "password": "wrong-password"},
        )
        assert blocked.status_code == 429
        assert "Retry-After" in blocked.headers
        # A different account from the same client is unaffected.
        assert await _login(client, other, "wrong-password") == 401


async def test_successful_login_forgives_prior_failures(
    make_user: Callable[[str, str], Awaitable[User]],
) -> None:
    email = f"forgive-{uuid4().hex}@example.com"
    password = "correct-horse-battery"
    await make_user(email, password)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(4):
            assert await _login(client, email, "wrong-password") == 401
        # A good password clears the bucket...
        assert await _login(client, email, password) == 200

    # ...so the email counter is gone and the next window starts clean.
    assert await peek(email_key(email), limit=5) == await peek(f"unused:{uuid4()}", limit=5)
