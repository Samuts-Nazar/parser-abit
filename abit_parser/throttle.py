"""
Глобальний тротлінг запитів на abit-poisk.org.ua.

Один домашній IP на всіх користувачів бота — тому пауза 1-3с з рандомом
має витримуватись ГЛОБАЛЬНО між буквально послідовними запитами (незалежно
від того, з якого потоку/користувача вони прийшли), а не незалежно в
кожному потоці окремим time.sleep(). Інакше два паралельні аналізи можуть
випадково вистрелити запитами майже одночасно.
"""

import random
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from .config import THROTTLE_MAX_SECONDS, THROTTLE_MIN_SECONDS

_lock = threading.Lock()
_last_request_at: float = 0.0


@contextmanager
def throttled_request() -> Iterator[None]:
    """Захопити перед КОЖНИМ запитом на сайт. Серіалізує запити по всьому
    процесу і витримує рандомну паузу від моменту завершення попереднього
    запиту (глобально), а не від початку поточного виклику."""
    global _last_request_at
    with _lock:
        wait_for = random.uniform(THROTTLE_MIN_SECONDS, THROTTLE_MAX_SECONDS)
        remaining = wait_for - (time.monotonic() - _last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        try:
            yield
        finally:
            _last_request_at = time.monotonic()
