"""
Завантажувач текстів бота з texts.toml — усе, що бот показує користувачу,
живе в тому файлі, а не тут. Тут лише механіка: прочитати й підставити
значення в шаблон.

Формат ключа — "секція.назва" (відповідає [секція] у TOML, назва — рядок
усередині неї), напр. t("greeting.welcome") або t("analysis.progress",
done=3, total=42, name="...").
"""

import tomllib
from pathlib import Path
from typing import Any, List

_TEXTS_PATH = Path(__file__).resolve().parent / "texts.toml"

with open(_TEXTS_PATH, "rb") as _f:
    _TEXTS: dict = tomllib.load(_f)


def _lookup(key: str) -> Any:
    node: Any = _TEXTS
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"texts.toml: немає ключа '{key}' (не знайшов '{part}')")
        node = node[part]
    return node


def t(key: str, **kwargs: Any) -> str:
    value = _lookup(key)
    if not isinstance(value, str):
        raise TypeError(f"texts.toml: '{key}' — не рядок ({type(value).__name__})")
    return value.format(**kwargs) if kwargs else value


def t_list(key: str) -> List[str]:
    value = _lookup(key)
    if not isinstance(value, list):
        raise TypeError(f"texts.toml: '{key}' — не список ({type(value).__name__})")
    return value
