"""Runtime settings.

Secrets come from the environment. Replace placeholders before a shared deploy.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# * Feature flag. Resemblance stays off the medical rank and off public deploys.
ENABLE_FACE_COMPARE = os.environ.get("ENABLE_FACE_COMPARE", "false").lower() in {
    "1",
    "true",
    "yes",
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _as_bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    """Process configuration."""

    database_path: Path
    session_secret: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    enable_face_compare: bool
    seed_on_empty: bool
    secure_cookies: bool = False
    contact_email: str = "privacy@example.com"


def load_settings() -> Settings:
    """Load settings from the environment.

    Returns:
        Settings for this process. DATABASE_PATH overrides DATA_DIR.
        On Vercel, the database defaults to temporary storage because the
        deployment filesystem is read-only outside ``/tmp``.
    """
    on_vercel = os.environ.get("VERCEL") == "1"
    if os.environ.get("DATABASE_PATH"):
        database_path = Path(os.environ["DATABASE_PATH"])
    elif on_vercel:
        # * Fluid instances can recycle. A missing file is reseeded on startup.
        database_path = Path("/tmp/donor-match.db")
    else:
        data_dir = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data"))
        database_path = data_dir / "app.db"
    session_secret = os.environ.get("SESSION_SECRET", "dev-only-change-me")
    if on_vercel and session_secret == "dev-only-change-me":
        logger.warning("SESSION_SECRET is still the development default.")
    contact_email = os.environ.get("CONTACT_EMAIL", "privacy@example.com").strip()
    return Settings(
        database_path=database_path,
        session_secret=session_secret,
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip(
            "/"
        ),
        llm_model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        enable_face_compare=ENABLE_FACE_COMPARE,
        seed_on_empty=_as_bool("SEED_ON_EMPTY", "true"),
        secure_cookies=on_vercel,
        contact_email=contact_email or "privacy@example.com",
    )
