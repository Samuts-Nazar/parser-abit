import hashlib
import json
import re
import time
from pathlib import Path
from typing import List, Optional

from .config import CACHE_TTL_HOURS
from .paths import get_cache_dir


def _cache_key(name: str, year: int) -> str:
    normalized = re.sub(r"\s+", " ", name.strip()).lower()
    digest = hashlib.sha256(f"{normalized}|{year}".encode("utf-8")).hexdigest()[:24]
    return digest


def _cache_path(name: str, year: int) -> Path:
    return get_cache_dir() / f"{_cache_key(name, year)}.json"


def get(name: str, year: int) -> Optional[List[dict]]:
    path = _cache_path(name, year)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # Старий формат кешу (до TTL) — файл був голим списком записів, без
    # fetched_at. Немає як дізнатись вік — вважаємо протухлим, не падаємо.
    if not isinstance(data, dict) or "fetched_at" not in data:
        return None

    age_hours = (time.time() - data["fetched_at"]) / 3600
    if age_hours > CACHE_TTL_HOURS:
        return None

    return data.get("records")


def set(name: str, year: int, records: List[dict]) -> None:
    path = _cache_path(name, year)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": time.time(), "records": records}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
