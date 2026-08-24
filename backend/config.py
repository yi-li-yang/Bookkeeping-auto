"""
Central configuration.

Single source of truth for env vars and filesystem paths. Every other module
reads settings from here rather than calling os.environ directly, so paths stay
repo-relative and there is one place to look when something is misconfigured.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# Explicit path rather than load_dotenv()'s CWD search, so the app picks up the
# same .env whether it is started from the repo root, from backend/, or by pytest.
load_dotenv(REPO_ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    db_path: Path
    input_dir: Path
    token_dir: Path
    frontend_dist: Path

    anthropic_api_key: str | None

    t212_env: str
    # (account_slug, key_id, secret) for every complete pair found in the env.
    t212_key_pairs: list[tuple[str, str, str]] = field(default_factory=list)

    eb_application_id: str | None = None
    eb_private_key_path: Path | None = None
    eb_redirect_uri: str = "http://localhost:8000/api/banks/callback"

    monzo_client_id: str | None = None
    monzo_client_secret: str | None = None
    monzo_redirect_uri: str = "http://localhost:8000/api/banks/monzo/callback"
    monzo_token_path: Path | None = None

    sync_interval_minutes: int = 360
    sync_on_startup: bool = True

    @property
    def monzo_configured(self) -> bool:
        return bool(self.monzo_client_id and self.monzo_client_secret)

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    def require_anthropic_key(self) -> str:
        """
        Return the Anthropic key, or explain how to set it.

        Deliberately raised at call time rather than import time: the dashboard
        should still start and serve already-ingested data without a key.
        """
        if not self.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add "
                f"your key (expected at {self.repo_root / '.env'})."
            )
        return self.anthropic_api_key

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = REPO_ROOT / "data"
        settings = cls(
            repo_root=REPO_ROOT,
            db_path=_env_path("DB_PATH", data_dir / "bookkeeping.db"),
            input_dir=_env_path("INPUT_DIR", data_dir / "input"),
            token_dir=_env_path("TOKEN_DIR", data_dir / "tokens"),
            frontend_dist=REPO_ROOT / "frontend" / "dist",
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            t212_env=(os.environ.get("T212_ENV") or "live").strip().lower(),
            t212_key_pairs=_t212_key_pairs(),
            eb_application_id=os.environ.get("EB_APPLICATION_ID") or None,
            eb_private_key_path=_env_path(
                "EB_PRIVATE_KEY_PATH", data_dir / "tokens" / "eb_private.pem"
            ),
            eb_redirect_uri=(
                os.environ.get("EB_REDIRECT_URI")
                or "http://localhost:8000/api/banks/callback"
            ),
            monzo_client_id=os.environ.get("MONZO_CLIENT_ID") or None,
            monzo_client_secret=os.environ.get("MONZO_CLIENT_SECRET") or None,
            monzo_redirect_uri=(
                os.environ.get("MONZO_REDIRECT_URI")
                or "http://localhost:8000/api/banks/monzo/callback"
            ),
            monzo_token_path=_env_path(
                "MONZO_TOKEN_PATH", data_dir / "tokens" / "monzo.json"
            ),
            sync_interval_minutes=_env_int("SYNC_INTERVAL_MINUTES", 360),
            sync_on_startup=_env_bool("SYNC_ON_STARTUP", True),
        )
        # Replaces the Dockerfile's `RUN mkdir -p /app/data/input`.
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        settings.input_dir.mkdir(parents=True, exist_ok=True)
        settings.token_dir.mkdir(parents=True, exist_ok=True)
        return settings


def _t212_key_pairs() -> list[tuple[str, str, str]]:
    """
    Collect every complete Trading 212 key+secret pair present in the env.

    Returns (slug, key_id, secret). The slug is a hint only -- which account a
    key actually belongs to is confirmed by calling the API, since Trading 212
    issues one key pair per account and the env name may be wrong.

    Also accepts the legacy T212_API_KEY_ID / T212_API_KEY naming this project
    used before the convention was aligned with the trading212-api skill.
    """
    candidates = [
        ("invest", "T212_API_KEY_INVEST", "T212_API_SECRET_INVEST"),
        ("stocks_isa", "T212_API_KEY_STOCKS_ISA", "T212_API_SECRET_STOCKS_ISA"),
        ("default", "T212_API_KEY", "T212_API_SECRET"),
        ("legacy", "T212_API_KEY_ID", "T212_API_KEY"),
    ]
    pairs: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for slug, key_var, secret_var in candidates:
        key_id = (os.environ.get(key_var) or "").strip()
        secret = (os.environ.get(secret_var) or "").strip()
        if not key_id or not secret or key_id == secret:
            continue
        if key_id in seen:
            continue
        seen.add(key_id)
        pairs.append((slug, key_id, secret))
    return pairs


settings = Settings.from_env()
