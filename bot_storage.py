"""
Персистентні підписки на стеження за напрямом — Telegram-специфічний стан,
тому окремо від abit_parser (спільного рушія для CLI/GUI/бота).

Файл: %APPDATA%\\parser-abit\\bot_subscriptions.json
"""

import json
from pathlib import Path
from typing import Dict, Optional

from abit_parser.paths import get_app_data_dir

SUBSCRIPTIONS_FILE = "bot_subscriptions.json"


def _path() -> Path:
    return get_app_data_dir() / SUBSCRIPTIONS_FILE


def load_all() -> Dict[str, dict]:
    path = _path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_all(data: Dict[str, dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_subscription(chat_id: int) -> Optional[dict]:
    return load_all().get(str(chat_id))


def set_subscription(chat_id: int, subscription: dict) -> None:
    data = load_all()
    data[str(chat_id)] = subscription
    save_all(data)


def remove_subscription(chat_id: int) -> None:
    data = load_all()
    if data.pop(str(chat_id), None) is not None:
        save_all(data)
