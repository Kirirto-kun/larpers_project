from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    database_url: str = "postgresql+psycopg://campus:campus@127.0.0.1:5432/campus"
    storage_dir: Path = Path("campus-data")
    public_origin: str = "https://localhost"
    registration_pool_id: UUID | None = None
    secure_cookies: bool = True
    tls_fingerprint_sha256: str | None = None
    lease_ms: int = Field(default=60_000, ge=1000)
    heartbeat_interval_ms: int = Field(default=10_000, ge=250)
    long_poll_ms: int = Field(default=25_000, ge=0, le=25_000)
    offline_seconds: int = Field(default=90, ge=5)
    verification_seconds: int = Field(default=45, ge=2, le=300)
    maintenance_interval_seconds: float = Field(default=0.5, ge=0.05)
    upload_limit_bytes: int = 536_870_912
    owner_storage_limit_bytes: int = 2_147_483_648
    max_active_jobs_per_owner: int = 12
    max_tasks_per_job: int = 4096
    max_pending_uploads: int = 64
    upload_ttl_seconds: int = 86_400
    orphan_grace_seconds: int = Field(default=86_400, ge=60)
    event_replay_limit: int = Field(default=1000, ge=10)
    max_log_bytes_per_attempt: int = 262_144
    catalog_file: Path | None = None
    portal_dist: Path = Path("apps/portal/dist")
    console_dist: Path = Path("apps/console/dist")
    llm_provider: str = "disabled"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: SecretStr | None = None
    llm_json_mode: Literal["json_object", "json_schema"] = "json_object"
    llm_timeout_seconds: float = Field(default=20, gt=0, le=90)
    llm_planning_seconds: int = Field(default=70, ge=2, le=300)

    @model_validator(mode="after")
    def check_settings(self):
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("PostgreSQL via psycopg is required")
        if self.heartbeat_interval_ms >= self.lease_ms:
            raise ValueError("Heartbeat interval must be shorter than the lease")
        if not self.secure_cookies and self.public_origin not in {
            "http://localhost",
            "http://127.0.0.1",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        }:
            raise ValueError("Insecure cookies are limited to loopback development")
        return self
