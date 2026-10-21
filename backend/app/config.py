"""Runtime settings, read from environment variables (see .env.example)."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    config_dir: Path
    static_dir: Path
    public_url: str
    hermes_url: str
    hermes_api_key: str
    hermes_model: str
    mcp_token: str
    approval_timeout: int


@lru_cache
def settings() -> Settings:
    root = Path(__file__).resolve().parents[2]
    return Settings(
        data_dir=Path(env("LAUNCHPAD_DATA_DIR") or root / "data"),
        config_dir=Path(env("LAUNCHPAD_CONFIG_DIR") or root / "config"),
        static_dir=Path(env("LAUNCHPAD_STATIC_DIR") or root / "frontend" / "dist"),
        public_url=env("PUBLIC_URL").rstrip("/"),
        hermes_url=env("HERMES_URL", "http://hermes:8642/v1").rstrip("/"),
        hermes_api_key=env("HERMES_API_KEY"),
        hermes_model=env("HERMES_MODEL", "hermes-agent"),
        mcp_token=env("LAUNCHPAD_MCP_TOKEN"),
        approval_timeout=int(env("APPROVAL_TIMEOUT", "300")),
    )
