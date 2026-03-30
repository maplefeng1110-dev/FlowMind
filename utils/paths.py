import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("FLOWMIND_DATA_DIR", ROOT_DIR / "data"))
LOG_DIR = Path(os.getenv("FLOWMIND_LOG_DIR", ROOT_DIR / "logs"))
UPLOAD_DIR = Path(os.getenv("FLOWMIND_UPLOAD_DIR", DATA_DIR / "uploads"))
REGISTRY_DB_PATH = Path(os.getenv("FLOWMIND_REGISTRY_DB", DATA_DIR / "registry.db"))


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
