import os
from pathlib import Path

APP_NAME = "parser-abit"


def get_app_data_dir() -> Path:
    """%APPDATA%\\parser-abit\\ на Windows, ~/.parser-abit деінде (fallback)."""
    base = os.environ.get("APPDATA")
    path = Path(base) / APP_NAME if base else Path.home() / f".{APP_NAME}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_cache_dir() -> Path:
    path = get_app_data_dir() / "cache" / "search"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_config_path() -> Path:
    return get_app_data_dir() / "config.json"
