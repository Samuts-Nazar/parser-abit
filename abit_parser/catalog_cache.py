"""
Кеш для довідника область/ВНЗ/спеціальність (catalog.py) — окремо від
основного cache.py (той кешує пошук по імені за ключем (name, year), тут
ключ — довільний рядок, чіпати перевірений cache.py заради цього не варто).

Той самий TTL-патерн: {"fetched_at":, "records":}, CACHE_TTL_HOURS з config.py.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import List, Optional

from .config import CACHE_TTL_HOURS
from .paths import get_catalog_cache_dir


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return get_catalog_cache_dir() / f"{digest}.json"


def get(key: str) -> Optional[List[dict]]:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(data, dict) or "fetched_at" not in data:
        return None

    age_hours = (time.time() - data["fetched_at"]) / 3600
    if age_hours > CACHE_TTL_HOURS:
        return None

    return data.get("records")


def set(key: str, records: List[dict]) -> None:
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": time.time(), "records": records}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
