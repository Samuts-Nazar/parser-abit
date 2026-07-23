import hashlib
import json
import re
from pathlib import Path
from typing import List, Optional

from .config import CACHE_DIR


def _cache_key(name: str, year: int) -> str:
    normalized = re.sub(r"\s+", " ", name.strip()).lower()
    digest = hashlib.sha256(f"{normalized}|{year}".encode("utf-8")).hexdigest()[:24]
    return digest


def _cache_path(name: str, year: int) -> Path:
    return Path(CACHE_DIR) / f"{_cache_key(name, year)}.json"


def get(name: str, year: int) -> Optional[List[dict]]:
    path = _cache_path(name, year)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def set(name: str, year: int, records: List[dict]) -> None:
    path = _cache_path(name, year)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
