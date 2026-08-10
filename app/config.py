"""Application settings loaded from environment variables (.env).

Never put real keys here — they are read from the environment at startup.
Never log the values of secrets after loading.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Final

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("quran_quiz.config")

# Names of environment variables we can never print, even in error messages.
_SECRET_ENV_NAMES: Final = {
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "ADMIN_API_KEY",
}

# Variables without which the server refuses to start.
_REQUIRED_ENV: Final = {
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "ADMIN_API_KEY",
}

# Variable that is loaded (kept for future browser-side Direct/RLS usage) but
# not strictly required by this server.
_OPTIONAL_ENV: Final = {"SUPABASE_KEY"}


@dataclass(frozen=True)
class Settings:
    """Validated, immutable application settings."""

    supabase_url: str
    supabase_service_role_key: str
    admin_api_key: str
    admin_key_passwords: tuple[str, ...] = ("mohamed", "mahmoud")
    supabase_key: str = ""
    cors_origins: tuple[str, ...] = field(default_factory=tuple)
    log_level: str = "INFO"


PRESENCE_WINDOW = 15  # seconds — REST polling keepalive counts as "connected"


def _fail_missing(missing: set[str]) -> None:
    """Print a clear error and stop startup.

    Only variable *names* are shown, never their values.
    """
    names = ", ".join(sorted(missing))
    print(
        f"[FATAL] Missing required environment variable(s): {names}. "
        f"See .env.example and copy it to .env with real values.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _warn_optional() -> None:
    logger.warning(
        "SUPABASE_KEY is not set. It is optional for this server (reserved "
        "for future browser-side Supabase usage) and will be ignored."
    )


def load_settings() -> Settings:
    """Read and validate settings from the environment."""
    missing = {name for name in _REQUIRED_ENV if not os.getenv(name)}
    if missing:
        _fail_missing(missing)
    if not os.getenv("SUPABASE_KEY"):
        _warn_optional()

    raw_origins = os.getenv("CORS_ORIGINS", "").strip()
    origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in raw_origins.split(",")
        if origin.strip()
    )
    if not origins:
        logger.warning("CORS_ORIGINS is empty: no cross-origin requests will be allowed.")

    raw_passwords = os.getenv("ADMIN_KEY_PASSWORDS", "").strip()
    passwords: tuple[str, ...] = tuple(
        pwd.strip() for pwd in raw_passwords.split(",") if pwd.strip()
    ) if raw_passwords else ("mohamed", "mahmoud")

    return Settings(
        supabase_url=os.environ["SUPABASE_URL"].strip(),
        supabase_service_role_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip(),
        admin_api_key=os.environ["ADMIN_API_KEY"].strip(),
        admin_key_passwords=passwords,
        supabase_key=os.getenv("SUPABASE_KEY", "").strip(),
        cors_origins=origins,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


settings: Settings = load_settings()