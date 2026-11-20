"""Local database, data source and map settings.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve data paths relative to the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Load settings from the environment and the local .env file."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        default="postgresql+psycopg://yatirimradar:yatirimradar@localhost:5432/yatirimradar",
        description="SQLAlchemy database URL using the psycopg 3 driver.",
    )

    # Document extraction
    anthropic_api_key: str = Field(
        default="",
        description="Optional Anthropic API key for document extraction.",
    )

    # HTTP requests
    http_user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        description="User-Agent header sent to public data sources.",
    )
    request_delay_seconds: float = Field(
        default=2.0, ge=0.0, description="Minimum delay between requests in seconds."
    )

    # Optional DNS override for cadastral services
    public_dns_servers: list[str] = Field(
        default=["8.8.8.8", "1.1.1.1"],
        description="Public DNS servers to use for the configured hosts.",
    )
    public_dns_hosts: list[str] = Field(
        default=["parselsorgu.tkgm.gov.tr", "cbsapi.tkgm.gov.tr"],
        description="Hosts to resolve with public DNS while preserving the TLS server name.",
    )

    # Geocoding
    geocode_confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum confidence setting for geocoded locations.",
    )

    # Default region
    pilot_il: str = Field(default="Ankara")
    pilot_ilce: str = Field(default="Polatlı")

    # Local data directories
    data_raw_dir: Path = Field(default=PROJECT_ROOT / "data" / "raw")
    data_processed_dir: Path = Field(default=PROJECT_ROOT / "data" / "processed")
    rayic_dizini: Path = Field(default=PROJECT_ROOT / "data" / "yerel" / "rayic")

    # Logging
    log_level: str = Field(default="INFO")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
