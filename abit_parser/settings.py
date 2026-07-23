import json
from typing import Optional

from .paths import get_config_path


def _load_all() -> dict:
    path = get_config_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_all(data: dict) -> None:
    with open(get_config_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_gemini_key() -> Optional[str]:
    return _load_all().get("gemini_api_key")


def save_gemini_key(key: str) -> None:
    data = _load_all()
    data["gemini_api_key"] = key
    _save_all(data)


def clear_gemini_key() -> None:
    data = _load_all()
    if data.pop("gemini_api_key", None) is not None:
        _save_all(data)
