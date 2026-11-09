from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_NETWORKS = tuple(IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    production_api_key: str = Field(default="", alias="PRODUCTION_API_KEY", repr=False)
    setups_password_hash: str = Field(default="", alias="SETUPS_PASSWORD_HASH", repr=False)
    auth_mode: Literal["cloudflare", "local"] = Field(default="cloudflare", alias="GARDIFUND_AUTH_MODE")
    local_port: int = Field(default=8765, ge=1024, le=65535, alias="GARDIFUND_LOCAL_PORT")
    remote_port: int = Field(default=8766, ge=1024, le=65535, alias="GARDIFUND_REMOTE_PORT")
    private_view_ip: str = Field(default="", alias="GARDIFUND_PRIVATE_VIEW_IP")
    cf_team_domain: str = Field(default="", alias="CF_ACCESS_TEAM_DOMAIN")
    cf_audience: str = Field(default="", alias="CF_ACCESS_AUD")
    data_dir: Path = Field(default=ROOT / "data", alias="GARDIFUND_DATA_DIR")
    start_worker: bool = Field(default=True, alias="GARDIFUND_START_WORKER")

    @field_validator("private_view_ip")
    @classmethod
    def validate_private_view_ip(cls, value: str) -> str:
        if not value:
            return ""
        address = IPv4Address(value)
        if not any(address in network for network in PRIVATE_NETWORKS):
            raise ValueError("GARDIFUND_PRIVATE_VIEW_IP must be one RFC 1918 IPv4 address assigned to this PC")
        return str(address)

    @property
    def viewing_address(self) -> tuple[str, int]:
        # Cloudflare private HTTP applications issue browser Access tokens on port 80.
        return (self.private_view_ip, 80) if self.private_view_ip else ("127.0.0.1", self.remote_port)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gardifund.sqlite3"
