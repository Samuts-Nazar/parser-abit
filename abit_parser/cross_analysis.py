from dataclasses import dataclass
from typing import Callable, List, Optional

from .config import P_LIKELY_DEFAULT
from .models import SearchApplication, Seats
from .search import search_applicant
from .verdict import Competitor, VerdictResult

STATUS_DEFINITE = "точно"
STATUS_LIKELY = "ймовірно"
STATUS_STAYS = "лишається"
STATUS_UNKNOWN = "невизначено"

SCORE_EPS = 0.001


@dataclass
class RiskAssessment:
    competitor: Competitor
    status: str  # точно | ймовірно | лишається | невизначено
    p_vacate: float
    best_choice: Optional[SearchApplication] = None
    note: str = ""


@dataclass
class CrossCheckResult:
    hard_count: int
    assessments: List[RiskAssessment]
    stays_count: int
    likely_count: int
    definite_count: int
    unknown_count: int
    optimistic_bound: int
    expected_count: float
    pessimistic_bound: int
    chance: float
    m: int


def _grade(position: int, seats: Seats, p_likely: float) -> "tuple[str, float]":
    if seats.bm_min is not None and position <= seats.bm_min:
        return STATUS_DEFINITE, 1.0
    if seats.bm_max is not None and position <= seats.bm_max:
        return STATUS_LIKELY, p_likely
    return STATUS_STAYS, 0.0


def assess_competitor(
    competitor: Competitor,
    year: int,
    current_direction_id: int,
    use_cache: bool = True,
    p_likely: float = P_LIKELY_DEFAULT,
) -> RiskAssessment:
    applicant = competitor.applicant

    if applicant.quota:
        return RiskAssessment(
            competitor=competitor,
            status=STATUS_STAYS,
            p_vacate=0.0,
            note="Вступ за квотою — механіка звільнення місця не аналізується (спрощення v2)",
        )

    try:
        apps = search_applicant(applicant.name, year, use_cache=use_cache)
    except Exception as e:
        return RiskAssessment(
            competitor=competitor, status=STATUS_UNKNOWN, p_vacate=0.0, note=f"Помилка пошуку: {e}"
        )

    anchors = [
        a
        for a in apps
        if a.direction_id == current_direction_id
        and a.priority == applicant.priority
        and abs(a.score - applicant.score) < SCORE_EPS
    ]
    if len(anchors) != 1:
        return RiskAssessment(
            competitor=competitor,
            status=STATUS_UNKNOWN,
            p_vacate=0.0,
            note="Не вдалось однозначно зматчити заяву в результатах пошуку",
        )

    # Σ бал НЕ стабільний для однієї людини між заявками — перераховується з
    # іншими коефіцієнтами під кожну спеціальність. Тому "інші заяви цієї
    # людини" — це решта рядків пошуку за цим ПІБ+рік, а не рядки з тим самим
    # балом. Один пріоритет може належати лише одній заяві людини — якщо в
    # результатах є дублікат пріоритету, це ознака тезки (колізії імені), і
    # ми чесно здаємось на "невизначено", а не гадаємо.
    priorities = [a.priority for a in apps]
    if len(priorities) != len(set(priorities)):
        return RiskAssessment(
            competitor=competitor,
            status=STATUS_UNKNOWN,
            p_vacate=0.0,
            note="Дублікат пріоритету в результатах пошуку — схоже на тезку, не гадаємо",
        )

    higher = [a for a in apps if a.priority < applicant.priority]

    if not higher:
        return RiskAssessment(
            competitor=competitor,
            status=STATUS_STAYS,
            p_vacate=0.0,
            note="Немає вищих пріоритетів — це і є її найвищий вибір",
        )

    best_status, best_p, best_choice = STATUS_STAYS, 0.0, None
    for a in higher:
        status, p = _grade(a.position, a.seats, p_likely)
        if p > best_p:
            best_status, best_p, best_choice = status, p, a

    return RiskAssessment(competitor=competitor, status=best_status, p_vacate=best_p, best_choice=best_choice)


def run_cross_check(
    verdict_result: VerdictResult,
    year: int,
    current_direction_id: int,
    use_cache: bool = True,
    p_likely: float = P_LIKELY_DEFAULT,
    on_progress: Optional[Callable[[str], None]] = None,
) -> CrossCheckResult:
    pool = [c for c in verdict_result.competitors if c.category == "група ризику"]
    hard_count = sum(1 for c in verdict_result.competitors if c.category == "залізний")

    assessments: List[RiskAssessment] = []
    for c in pool:
        assessments.append(assess_competitor(c, year, current_direction_id, use_cache, p_likely))
        if on_progress:
            on_progress(c.applicant.name)

    matched = [a for a in assessments if a.status != STATUS_UNKNOWN]
    unknown = [a for a in assessments if a.status == STATUS_UNKNOWN]

    stays_count = sum(1 for a in matched if a.status == STATUS_STAYS)
    likely_count = sum(1 for a in matched if a.status == STATUS_LIKELY)
    definite_count = sum(1 for a in matched if a.status == STATUS_DEFINITE)
    unknown_count = len(unknown)

    expected_stay = sum(1.0 - a.p_vacate for a in matched)

    m = verdict_result.m
    optimistic_bound = hard_count + stays_count
    pessimistic_bound = hard_count + stays_count + likely_count + unknown_count
    expected_count = hard_count + expected_stay

    if pessimistic_bound <= m:
        chance = 1.0
    elif optimistic_bound > m:
        chance = 0.0
    else:
        chance = (pessimistic_bound - expected_count) / (pessimistic_bound - optimistic_bound)
        chance = max(0.0, min(1.0, chance))

    return CrossCheckResult(
        hard_count=hard_count,
        assessments=assessments,
        stays_count=stays_count,
        likely_count=likely_count,
        definite_count=definite_count,
        unknown_count=unknown_count,
        optimistic_bound=optimistic_bound,
        expected_count=expected_count,
        pessimistic_bound=pessimistic_bound,
        chance=chance,
        m=m,
    )
