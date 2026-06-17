from functools import lru_cache

from pydantic import Field
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

    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "peso_tugma_ai"

    JWT_SECRET_KEY: str = Field(default="change-me", min_length=8)
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
