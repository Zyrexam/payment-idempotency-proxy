from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://admin:password@localhost:5433/idempotency"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = "redispass"

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    uvicorn_reload: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
