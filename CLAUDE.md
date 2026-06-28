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

**Vertical slices** — every resource owns a self-contained package under `app/api/v1/routes/<resource>/`, holding its model, schemas, service, and routes together. Within a slice the dependency direction is still strict — **routes → service → model** — all reading config/security from `core`. Never import upward (a model must not import its service/routes).

```
app/main.py              FastAPI factory + lifespan (Mongo init/close), CORS, mounts api_router under settings.API_V1_PREFIX
app/core/
  config.py              Pydantic Settings singleton — `settings`
  security.py            bcrypt hash/verify + JWT encode/decode (HS256 by default)
app/db/mongodb.py        Module-level Motor client + `init_beanie(document_models=[...])`
app/api/deps.py          Shared, cross-cutting FastAPI dependencies (e.g. `get_current_user` from Bearer token)
app/api/v1/router.py     Mounts each slice's `router` under tags: health (no prefix), auth, me, users
app/api/v1/routes/       One package per resource (vertical slice):
  <resource>/
    __init__.py          Re-exports `router` (so `router.py` can do `<resource>.router`)
    <resource>_models.py    Beanie `Document` classes (Mongo collections). One per aggregate.
    <resource>_schemas.py   Pydantic request/response DTOs. Never leak Beanie `Document` objects out of routes.
    <resource>_service.py   Async business logic — only place that calls `.find_one` / `.insert` / etc.
    <resource>_routes.py    Thin handlers that delegate to the service; defines `router = APIRouter()`
```

Conventions:

- **Files are prefixed with the resource name** (`users_models.py`, not `models.py`) so they stay unambiguous in editor tabs and search. Use underscores — `users.models.py` is not an importable Python module name.
- **Imports within a slice are relative** (`from .users_models import User`), which keeps each package portable. Imports across slices / from infra use absolute paths (`from app.api.v1.routes.users.users_models import User`).
- A slice only includes the files it needs. `health/` and `me/` have just `*_routes.py`; `auth/` has `auth_routes.py` + `auth_schemas.py` and reuses the `users` service (no own model). Don't create empty `*_models.py` / `*_service.py` stubs.

### Adding a new resource (vertical slice)

1. Create the package `app/api/v1/routes/<name>/` with an `__init__.py` containing `from .<name>_routes import router`.
2. Add the Beanie `Document` in `app/api/v1/routes/<name>/<name>_models.py`.
3. **Register it** in `app/db/mongodb.py` by adding it to the `document_models=[...]` list passed to `init_beanie`. Forgetting this is the most common bug — the model imports fine but every query raises at runtime.
4. Add Pydantic DTOs in `<name>_schemas.py` (Create / Read / Update variants).
5. Put query/mutation logic in `<name>_service.py` — routes should never call Beanie directly.
6. Define `router = APIRouter()` and the handlers in `<name>_routes.py`, delegating to the service.
7. Register the slice in `app/api/v1/router.py`: import it (`from app.api.v1.routes import <name>`) and `api_router.include_router(<name>.router, prefix="/<name>", tags=["<name>"])`.

### Auth flow

- `POST /auth/register` → `user_service.create_user` (bcrypt-hashes password).
- `POST /auth/login` uses `OAuth2PasswordRequestForm` (form-encoded `username`/`password`) and returns a JWT with `sub = str(user.id)`.
- The `Token` / `TokenPayload` DTOs live in the auth slice (`app/api/v1/routes/auth/auth_schemas.py`); `auth` has no model of its own and reuses the `users` service for `authenticate` / `create_user`.
- Protected routes depend on `get_current_user` (`app/api/deps.py` — kept shared since it spans slices), which decodes the Bearer token, validates the payload via `TokenPayload`, and loads the user by id via `User.get(payload.sub)`. The `tokenUrl` registered with `OAuth2PasswordBearer` is `f"{settings.API_V1_PREFIX}/auth/login"` — keep them in sync if the prefix changes.

### Tests

`tests/conftest.py` provides an `async client` fixture built on `httpx.AsyncClient` + `ASGITransport(app=create_app())`. It does **not** mock the database — anything hitting Mongo needs a reachable instance (or a fixture that swaps the DB). New tests should use this fixture rather than spinning up their own transport.
