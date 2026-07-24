from dataclasses import dataclass
from typing import List

from .models import Applicant, DirectionStats

EXCLUDED_STATUSES = {"Відмова"}

VERDICT_PASS = "проходиш"
VERDICT_BORDERLINE = "на межі"
VERDICT_FAIL = "пролітаєш"


@dataclass
class Competitor:
    applicant: Applicant
    category: str  # "залізний" | "група ризику"


@dataclass
class VerdictResult:
    m: int
    optimistic_bound: int  # ранг користувача, якщо рахувати лише залізних конкурентів
    pessimistic_bound: int  # ранг користувача, якщо рахувати залізних + групу ризику
    verdict: str  # "проходиш" | "на межі" | "пролітаєш"
    competitors: List[Competitor]


def budget_applicants(applicants: List[Applicant]) -> List[Applicant]:
    """Бюджетники (Б), без контрактних дублів і без відмовлених заяв."""
    return [a for a in applicants if a.funding == "Б" and a.status not in EXCLUDED_STATUSES]


def build_verdict(
    applicants: List[Applicant],
    stats: DirectionStats,
    user_score: float,
) -> VerdictResult:
    budget = budget_applicants(applicants)
    higher = [a for a in budget if a.score > user_score]

    competitors = [
        Competitor(applicant=a, category="залізний" if a.priority == 1 else "група ризику")
        for a in higher
    ]
    iron_count = sum(1 for c in competitors if c.category == "залізний")
    total_count = len(competitors)

    m = stats.bm_max
    optimistic_bound = iron_count + 1
    pessimistic_bound = total_count + 1

    if pessimistic_bound <= m:
        verdict = VERDICT_PASS
    elif optimistic_bound <= m:
        verdict = VERDICT_BORDERLINE
    else:
        verdict = VERDICT_FAIL

    return VerdictResult(
        m=m,
        optimistic_bound=optimistic_bound,
        pessimistic_bound=pessimistic_bound,
        verdict=verdict,
        competitors=competitors,
    )
