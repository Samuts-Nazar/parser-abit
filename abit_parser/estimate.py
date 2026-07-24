"""
Спільна логіка "межі → вердикт/шанс" для v2 (крос-аналіз) і v4 (контракт).

Раніше `chance` інтерполював позицію "очікуваного" в діапазоні
[оптимістична, песимістична] МІЖ САМИМИ МЕЖАМИ, ігноруючи, де в цьому
діапазоні взагалі лежить ліміт (M/K). Це давало реальний кейс, де при
очікуваному ранзі 45.8 і M=44 (тобто очікуваний УЖЕ ЗА межею) шанс
показувало 67% — бо широкий діапазон [опт, песим] "розбавляв" перетин
з лімітом. Тут `chance` центрується на самому ліміті — тому воно не може
завищити, коли очікуваний ранг уже гірший за M/K.
"""

from typing import NamedTuple

from .verdict import VERDICT_BORDERLINE as V1_VERDICT_BORDERLINE

VERDICT_PASS = "проходиш"
VERDICT_FAIL = "пролітаєш"
VERDICT_BORDERLINE_BAD = "на межі (радше пролітаєш)"
VERDICT_BORDERLINE_GOOD = "на межі (радше проходиш)"

# Спрощений тир для аналізу по кількох пріоритетах одразу (engine.run_priority_chain) —
# там нема сенсу розрізняти v1/v2 чи "радше так/радше ні", треба лише знати,
# чи це кандидат на "приземлення", чи ні. VERDICT_PASS/VERDICT_FAIL тут — ті самі
# рядки, що й у v1 (verdict.py), тому окремих v1-аліасів для них не треба;
# лише v1 "на межі" (без інформації про нахил) — рядок, якого нема у v2.
TIER_PASS = "pass"
TIER_BORDERLINE = "borderline"
TIER_FAIL = "fail"

_TIER_BY_VERDICT = {
    VERDICT_PASS: TIER_PASS,
    VERDICT_BORDERLINE_GOOD: TIER_BORDERLINE,
    VERDICT_BORDERLINE_BAD: TIER_BORDERLINE,
    V1_VERDICT_BORDERLINE: TIER_BORDERLINE,
    VERDICT_FAIL: TIER_FAIL,
}


def tier_of(verdict: str) -> str:
    """Нормалізує 4-станний вердикт v2 і 3-станний вердикт v1 в один tier —
    щоб ланцюжок пріоритетів не звірявся з рядками вердикту напряму."""
    return _TIER_BY_VERDICT.get(verdict, TIER_FAIL)


class Estimate(NamedTuple):
    verdict: str
    chance: float  # "очікуваний" % — евристика, центрована на ліміті
    pessimistic_chance: float  # 0 або 1 — чи проходить навіть найгірший сценарій
    optimistic_chance: float  # 0 або 1 — чи проходить бодай найкращий сценарій


def estimate(optimistic: int, expected: float, pessimistic: int, limit: int) -> Estimate:
    pessimistic_chance = 1.0 if pessimistic <= limit else 0.0
    optimistic_chance = 1.0 if optimistic <= limit else 0.0

    if pessimistic <= limit:
        return Estimate(VERDICT_PASS, 1.0, pessimistic_chance, optimistic_chance)
    if optimistic > limit:
        return Estimate(VERDICT_FAIL, 0.0, pessimistic_chance, optimistic_chance)

    span = pessimistic - optimistic
    chance = 0.5 + (limit - expected) / span if span else 0.5
    chance = max(0.0, min(1.0, chance))

    verdict = VERDICT_BORDERLINE_BAD if expected > limit else VERDICT_BORDERLINE_GOOD
    return Estimate(verdict, chance, pessimistic_chance, optimistic_chance)
