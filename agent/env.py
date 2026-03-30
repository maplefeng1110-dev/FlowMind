import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


AGENT_DIR = Path(__file__).resolve().parent
AGENT_ENV_PATH = AGENT_DIR / ".env"


def _resolve_env_path(env_path: str | Path | None = None) -> Path:
    return Path(env_path) if env_path else AGENT_ENV_PATH


def load_agent_env(env_path: str | Path | None = None, *, override: bool = False) -> Path:
    path = _resolve_env_path(env_path)
    load_dotenv(path, override=override)
    return path


def get_agent_env(name: str, default: str | None = None, *, env_path: str | Path | None = None) -> str | None:
    path = _resolve_env_path(env_path)
    if path.exists():
        file_values = dotenv_values(path)
        file_value = file_values.get(name)
        if file_value is not None:
            return str(file_value)
    return os.getenv(name, default)
