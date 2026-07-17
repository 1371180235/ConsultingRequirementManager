from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "咨询项目全流程需求管理系统"
    app_env: str = "development"
    database_url: str = "sqlite:///./consulting_remote.db"
    cookie_name: str = "crm_session"
    cookie_secure: bool = True
    cookie_domain: str | None = None
    session_hours: int = Field(default=12, ge=1, le=168)
    max_login_failures: int = Field(default=5, ge=3, le=20)
    lock_minutes: int = Field(default=15, ge=1, le=1440)
    upload_dir: Path = Path("./data/uploads")
    max_upload_mb: int = Field(default=50, ge=1, le=1024)
    auto_create_tables: bool = True
    auto_seed: bool = True
    admin_username: str = "admin"
    admin_password: str | None = None
    cors_origins: str = ""

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
