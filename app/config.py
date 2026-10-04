from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    port: int = 8000

    hospital_directory_base_url: str = "https://hospital-directory.onrender.com"
    request_timeout_seconds: float = 10.0
    max_retries: int = 3
    retry_base_delay_seconds: float = 0.5
    max_concurrency: int = 5

    max_csv_rows: int = 20
    max_upload_size_bytes: int = 1_048_576  # 1 MiB; 20 rows of CSV is at most a few KB


@lru_cache
def get_settings() -> Settings:
    return Settings()
