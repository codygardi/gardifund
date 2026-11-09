from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    production_api_key: str = Field(default="", alias="PRODUCTION_API_KEY", repr=False)
    setups_password_hash: str = Field(default="", alias="SETUPS_PASSWORD_HASH", repr=False)
    auth_mode: Literal["cloudflare", "local"] = Field(default="local", alias="GARDIFUND_AUTH_MODE")
    local_port: int = Field(default=8765, ge=1024, le=65535, alias="GARDIFUND_LOCAL_PORT")
    remote_port: int = Field(default=8766, ge=1024, le=65535, alias="GARDIFUND_REMOTE_PORT")
    cf_team_domain: str = Field(default="", alias="CF_ACCESS_TEAM_DOMAIN")
    cf_audience: str = Field(default="", alias="CF_ACCESS_AUD")
    share_position_details: bool = Field(default=False, alias="GARDIFUND_SHARE_POSITION_DETAILS")
    data_dir: Path = Field(default=ROOT / "data", alias="GARDIFUND_DATA_DIR")
    start_worker: bool = Field(default=True, alias="GARDIFUND_START_WORKER")

    @property
    def viewing_address(self) -> tuple[str, int]:
        return "127.0.0.1", self.remote_port

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gardifund.sqlite3"
