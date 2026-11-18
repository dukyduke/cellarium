"""Runtime configuration, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
GUIDES_DIR = PACKAGE_DIR / "guides"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path
    admin_password: str | None = None
    admin_user: str = "admin"
    secure_cookies: bool = False
    session_days: int = 30
    image_api_base: str = "https://api.openai.com/v1"
    image_api_key: str | None = None
    image_model: str = "gpt-image-1"
    image_size: str = "1024x1024"
    public_url: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cellarium.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def image_generation_enabled(self) -> bool:
        return bool(self.image_api_key)

    @classmethod
    def from_env(cls, env_file: str | os.PathLike | None = ".env", **overrides) -> "Settings":
        if env_file:
            _load_dotenv(Path(env_file))
        env = os.environ
        values: dict[str, Any] = dict(
            data_dir=Path(env.get("CELLARIUM_DATA_DIR", "data")).expanduser().resolve(),
            admin_password=env.get("CELLARIUM_ADMIN_PASSWORD") or None,
            admin_user=env.get("CELLARIUM_ADMIN_USER") or "admin",
            secure_cookies=_bool(env.get("CELLARIUM_SECURE_COOKIES")),
            session_days=int(env.get("CELLARIUM_SESSION_DAYS", "30")),
            image_api_base=env.get("CELLARIUM_IMAGE_API_BASE", "https://api.openai.com/v1").rstrip("/"),
            image_api_key=env.get("CELLARIUM_IMAGE_API_KEY") or None,
            image_model=env.get("CELLARIUM_IMAGE_MODEL", "gpt-image-1"),
            image_size=env.get("CELLARIUM_IMAGE_SIZE", "1024x1024"),
            public_url=env.get("CELLARIUM_PUBLIC_URL") or None,
        )
        values.update(overrides)
        settings = cls(**values)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.images_dir.mkdir(parents=True, exist_ok=True)
        return settings
