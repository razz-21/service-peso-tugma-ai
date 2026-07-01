from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    PROJECT_NAME: str = "peso-tugma-ai"
    API_V1_PREFIX: str = "/api/v1"

    CORS_ORIGINS: str = "http://localhost:3000"

    MONGODB_USERNAME: str = "username"
    MONGODB_PASSWORD: str = "password"
    MONGODB_SCHEME: str = "mongodb"
    MONGODB_HOST: str = "localhost:27017"
    MONGODB_OPTIONS: str = ""
    MONGODB_URI: str = ""
    MONGODB_DB_NAME: str = "development"

    JWT_SECRET_KEY: str = Field(default="change-me", min_length=8)
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 10
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Auth cookies. `COOKIE_SECURE` must be True in production (HTTPS only);
    # keep it False for local http. Use `COOKIE_SAMESITE="none"` (with secure)
    # only when the frontend is served from a cross-site domain.
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    COOKIE_DOMAIN: str | None = None

    @model_validator(mode="after")
    def assemble_mongodb_uri(self) -> "Settings":
        # Prefer an explicit MONGODB_URI from the environment; otherwise build it
        # from the env-provided components. Credentials are URL-encoded so special
        # characters (e.g. "@", "/", ":") don't break URI parsing.
        if not self.MONGODB_URI:
            username = quote_plus(self.MONGODB_USERNAME)
            password = quote_plus(self.MONGODB_PASSWORD)
            self.MONGODB_URI = (
                f"{self.MONGODB_SCHEME}://{username}:{password}"
                f"@{self.MONGODB_HOST}{self.MONGODB_OPTIONS}"
            )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
