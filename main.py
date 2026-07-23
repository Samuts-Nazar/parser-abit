"""
Персональний список конкурентів за бюджетне місце — abit-poisk.org.ua

Приклад:
    python main.py https://abit-poisk.org.ua/rate2026/direction/1613482 \
        --score 180.5 --priority 3 --funding Б
"""

import sys

# Консоль Windows (cmd.exe, і так само замороженим PyInstaller-exe) типово
# віддає cp1252 замість utf-8 — кирилиця в print() падає з UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from abit_parser.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
