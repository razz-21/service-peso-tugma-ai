# service-peso-tugma-ai

FastAPI backend for **peso-tugma-ai** — an async Python service backed by MongoDB (via Beanie/Motor) with JWT-based authentication.

## Stack

- **Python** 3.12+
- **FastAPI** + **Uvicorn**
- **MongoDB** with **Beanie** (ODM) on top of **Motor**
- **Pydantic v2** / **pydantic-settings**
- **PyJWT** + **passlib[bcrypt]** for auth
- **uv** for dependency management
- **pytest** / **pytest-asyncio**, **ruff**, **mypy** (strict)

## Project layout

```
app/
├── main.py             # FastAPI app factory + lifespan (Mongo init/close)
├── core/               # config, logging, security (JWT + password hashing)
├── db/                 # MongoDB client + Beanie initialization
├── models/             # Beanie documents (e.g. User)
├── schemas/            # Pydantic request/response models
├── services/           # Business logic (e.g. user_service)
└── api/
    ├── deps.py         # Shared dependencies (current_user, etc.)
    └── v1/
        ├── router.py
        └── routes/     # auth, users, health
tests/                  # pytest suite
```

## Getting started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) installed
- A running MongoDB instance (local or remote)

### Setup

```bash
# Install dependencies (creates .venv)
uv sync

# Copy environment template and edit values
cp .env.example .env
```

Set at minimum `MONGODB_URI`, `MONGODB_DB_NAME`, and a strong `JWT_SECRET_KEY` in `.env`.

### Run the server

```bash
uv run uvicorn app.main:app --reload
```

The API is served under the prefix from `API_V1_PREFIX` (default `/api/v1`).

- Interactive docs: <http://localhost:8000/docs>
- OpenAPI schema: <http://localhost:8000/openapi.json>
- Health check: <http://localhost:8000/api/v1/healthz>

## Configuration

All settings are loaded from environment variables (`.env` supported). See `.env.example` for the full list.

| Variable | Description | Default |
|---|---|---|
| `APP_ENV` | Environment name | `development` |
| `APP_DEBUG` | Enable FastAPI debug mode | `true` |
| `PROJECT_NAME` | OpenAPI title | `peso-tugma-ai` |
| `API_V1_PREFIX` | Prefix for v1 routes | `/api/v1` |
| `CORS_ORIGINS` | Comma-separated allowed origins | `http://localhost:3000` |
| `MONGODB_URI` | MongoDB connection string | `mongodb://localhost:27017` |
| `MONGODB_DB_NAME` | MongoDB database name | `peso_tugma_ai` |
| `JWT_SECRET_KEY` | HMAC secret for access tokens | — |
| `JWT_ALGORITHM` | JWT signing algorithm | `HS256` |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Access token lifetime | `60` |

## API

All routes are mounted under `API_V1_PREFIX` (default `/api/v1`).

| Method | Path | Description | Auth |
|---|---|---|---|
| `GET` | `/healthz` | Liveness probe | — |
| `POST` | `/auth/register` | Create a new user | — |
| `POST` | `/auth/login` | Exchange credentials for a JWT (OAuth2 password flow) | — |
| `GET` | `/users/me` | Current authenticated user | Bearer |

Login uses the OAuth2 password form: send `username` (email) and `password` as `application/x-www-form-urlencoded`. Authenticated calls require an `Authorization: Bearer <access_token>` header.

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type-check (strict)
uv run mypy app
```

## License

Private — not licensed for redistribution.
