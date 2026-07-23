"""
Персональний список конкурентів за бюджетне місце — abit-poisk.org.ua

Приклад:
    python main.py https://abit-poisk.org.ua/rate2026/direction/1613482 \
        --score 180.5 --priority 3 --funding Б
"""

from abit_parser.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
