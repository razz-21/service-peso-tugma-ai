# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FastAPI backend for **peso-tugma-ai**. Python 3.12, async-first, MongoDB via Beanie/Motor, JWT auth. Dependencies and venv are managed with **uv** (see `uv.lock`).

## Commands

All commands assume `uv` is installed; `uv run` resolves the project venv automatically.

```bash
# Install / sync dependencies (creates .venv from uv.lock)
uv sync

# Run the API with auto-reload
uv run uvicorn app.main:app --reload

# Tests
uv run pytest                                  # full suite
uv run pytest tests/test_health.py             # one file
uv run pytest tests/test_health.py::test_healthz  # one test
uv run pytest -k healthz                       # by name pattern
uv run pytest -x -vv                           # stop on first failure, verbose

# Lint / format / type-check
uv run ruff check .
uv run ruff format .
uv run mypy app
```

`pytest` is configured with `asyncio_mode = "auto"` — async test functions do **not** need `@pytest.mark.asyncio`.

`mypy` runs in **strict** mode with the `pydantic.mypy` plugin enabled. Treat type errors as build failures.

`ruff` enforces `E, F, I, B, UP, N, SIM` at line-length 100, targeting `py312` — prefer modern syntax (`X | None`, `dict[str, T]`, `match`, `datetime.now(UTC)`).

## Environment

Settings are loaded by `app/core/config.py` from environment variables and an optional `.env` (see `.env.example`). All settings are `case_sensitive=True` and `extra="ignore"`. Access them via the singleton `from app.core.config import settings` (cached by `lru_cache`).

A reachable MongoDB at `MONGODB_URI` is required to start the app — `lifespan` calls `init_mongo()` on startup and `close_mongo()` on shutdown. `JWT_SECRET_KEY` must be ≥ 8 chars or Pydantic validation fails at import time.

## Architecture

Layered, with strict directional dependencies — **routes → services → models**, all reading config/security from `core`. Never import upward.

```
app/main.py            FastAPI factory + lifespan (Mongo init/close), CORS, mounts api_router under settings.API_V1_PREFIX
app/core/
  config.py            Pydantic Settings singleton — `settings`
  security.py          bcrypt hash/verify + JWT encode/decode (HS256 by default)
app/db/mongodb.py      Module-level Motor client + `init_beanie(document_models=[...])`
app/models/            Beanie `Document` classes (Mongo collections). One file per aggregate.
app/schemas/           Pydantic request/response DTOs. Never leak Beanie `Document` objects out of routes.
app/services/          Async business logic — only place that calls `User.find_one` / `.insert` / etc.
app/api/deps.py        Shared FastAPI dependencies (e.g. `get_current_user` from Bearer token)
app/api/v1/router.py   Mounts route modules under tags: health (no prefix), auth, users
app/api/v1/routes/     One module per resource; thin handlers that delegate to services
```

### Adding a new MongoDB collection

1. Create a Beanie `Document` in `app/models/<name>.py`.
2. **Register it** in `app/db/mongodb.py` by adding it to the `document_models=[...]` list passed to `init_beanie`. Forgetting this is the most common bug — the model imports fine but every query raises at runtime.
3. Add Pydantic DTOs in `app/schemas/<name>.py` (Create / Read / Update variants).
4. Put query/mutation logic in `app/services/<name>_service.py` — routes should never call Beanie directly.
5. Add a route module in `app/api/v1/routes/<name>.py` and register it in `app/api/v1/router.py`.

### Auth flow

- `POST /auth/register` → `user_service.create_user` (bcrypt-hashes password).
- `POST /auth/login` uses `OAuth2PasswordRequestForm` (form-encoded `username`/`password`) and returns a JWT with `sub = str(user.id)`.
- Protected routes depend on `get_current_user` (`app/api/deps.py`), which decodes the Bearer token, validates the payload via `TokenPayload`, and loads the user by id via `User.get(payload.sub)`. The `tokenUrl` registered with `OAuth2PasswordBearer` is `f"{settings.API_V1_PREFIX}/auth/login"` — keep them in sync if the prefix changes.

### Tests

`tests/conftest.py` provides an `async client` fixture built on `httpx.AsyncClient` + `ASGITransport(app=create_app())`. It does **not** mock the database — anything hitting Mongo needs a reachable instance (or a fixture that swaps the DB). New tests should use this fixture rather than spinning up their own transport.
