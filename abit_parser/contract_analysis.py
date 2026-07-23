"""
v4 — шанс на контракт.

Механіка (EDBO): при подачі на бюджет людина, що НЕ проходить на бюджет,
автоматично падає в контрактний пул тієї ж спеціальності. Контракт-онлі
заявки (К) туди входять напряму.

Рахується ПІСЛЯ бюджету (build_contract_pool потребує знати, хто з (Б) не
пройшов) і переюзає інфраструктуру v2 (search.py/cache.py, дизамбіг по
дублікату пріоритету, градація точно/ймовірно/лишається/невизначено) —
лише з ширшим критерієм вильоту: конкурент вище користувача звільняє
контрактне місце, якщо на пріоритетнішому виборі ловить БУДЬ-ЯКЕ місце
(бюджет АБО контракт), а не тільки бюджет.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .config import P_LIKELY_DEFAULT
from .estimate import estimate
from .models import Applicant, DirectionStats, SearchApplication, Seats
from .search import search_applicant
from .verdict import EXCLUDED_STATUSES, budget_applicants

STATUS_DEFINITE = "точно"
STATUS_LIKELY = "ймовірно"
STATUS_STAYS = "лишається"
STATUS_UNKNOWN = "невизначено"

SCORE_EPS = 0.001


@dataclass
class ContractPoolMember:
    applicant: Applicant
    origin: str  # "контракт" | "бюджет-невдаха"


@dataclass
class ContractAssessment:
    member: ContractPoolMember
    status: str  # точно | ймовірно | лишається | невизначено
    p_vacate: float
    best_choice: Optional[SearchApplication] = None
    note: str = ""


@dataclass
class ContractResult:
    k: int
    hard_count: int
    pool_size_total: int  # весь контрактний пул (усі бали), інформативно
    assessments: List[ContractAssessment]  # лише пул вище користувача
    stays_count: int
    likely_count: int
    definite_count: int
    unknown_count: int
    optimistic_bound: int
    expected_count: float
    pessimistic_bound: int
    chance: float  # "очікуваний" % — евристика, центрована на K (див. estimate.py)
    pessimistic_chance: float  # 0 або 1 — проходить навіть найгірший сценарій?
    optimistic_chance: float  # 0 або 1 — проходить бодай найкращий сценарій?
    verdict: str  # проходиш | на межі (радше проходиш/пролітаєш) | пролітаєш


def build_contract_pool(applicants: List[Applicant], m: int) -> List[ContractPoolMember]:
    """(К)-рядки ∪ (Б)-рядки з рангом > M у бюджетному списку, дедуп по (ПІБ, бал).

    Спрощення: "не проходить бюджет" тут — проста рангова позиція в (Б)-списку
    за балом (без урахування вильотів по пріоритету для КОЖНОГО (Б)-заявника —
    це роздуло б кількість мережевих запитів на порядки). Вильоти рахуються
    лише для пул-учасників ВИЩЕ користувача (build_contract_pool сам мережу
    не чіпає) — той самий принцип, що й у v2.
    """
    budget_ranked = sorted(budget_applicants(applicants), key=lambda a: -a.score)
    budget_failures = budget_ranked[m:]

    contract_only = [a for a in applicants if a.funding == "К" and a.status not in EXCLUDED_STATUSES]

    raw = [ContractPoolMember(a, "контракт") for a in contract_only] + [
        ContractPoolMember(a, "бюджет-невдаха") for a in budget_failures
    ]

    # Та сама людина може мати і (Б), і (К) рядок на цій спеціальності (з
    # однаковим балом, різними пріоритетами) — контрактне місце займе одне.
    # З двох рядків лишаємо той, що з меншим (сеньйорнішим) пріоритетом.
    dedup: Dict[Tuple[str, float], ContractPoolMember] = {}
    for member in raw:
        key = (member.applicant.name, round(member.applicant.score, 3))
        existing = dedup.get(key)
        if existing is None or member.applicant.priority < existing.applicant.priority:
            dedup[key] = member
    return list(dedup.values())


def _grade_wide(position: int, seats: Seats, p_likely: float) -> Tuple[str, float]:
    """Як v2._grade, але "ловить місце" = бюджет АБО контракт на вищому пріоритеті."""
    if seats.bm_min is not None and position <= seats.bm_min:
        return STATUS_DEFINITE, 1.0
    capacity = (seats.bm_max or 0) + (seats.k or 0)
    if capacity and position <= capacity:
        return STATUS_LIKELY, p_likely
    return STATUS_STAYS, 0.0


def assess_pool_member(
    member: ContractPoolMember,
    year: int,
    current_direction_id: int,
    use_cache: bool = True,
    p_likely: float = P_LIKELY_DEFAULT,
) -> ContractAssessment:
    applicant = member.applicant

    if applicant.quota:
        return ContractAssessment(
            member=member,
            status=STATUS_STAYS,
            p_vacate=0.0,
            note="Вступ за квотою — механіка звільнення місця не аналізується (спрощення)",
        )

    try:
        apps = search_applicant(applicant.name, year, use_cache=use_cache)
    except Exception as e:
        return ContractAssessment(
            member=member, status=STATUS_UNKNOWN, p_vacate=0.0, note=f"Помилка пошуку: {e}"
        )

    priorities = [a.priority for a in apps]
    if len(priorities) != len(set(priorities)):
        return ContractAssessment(
            member=member,
            status=STATUS_UNKNOWN,
            p_vacate=0.0,
            note="Дублікат пріоритету в результатах пошуку — схоже на тезку, не гадаємо",
        )

    higher = [a for a in apps if a.priority < applicant.priority]
    if not higher:
        return ContractAssessment(
            member=member,
            status=STATUS_STAYS,
            p_vacate=0.0,
            note="Немає вищих пріоритетів — це і є її найвищий вибір",
        )

    best_status, best_p, best_choice = STATUS_STAYS, 0.0, None
    for a in higher:
        status, p = _grade_wide(a.position, a.seats, p_likely)
        if p > best_p:
            best_status, best_p, best_choice = status, p, a

    return ContractAssessment(member=member, status=best_status, p_vacate=best_p, best_choice=best_choice)


def run_contract_analysis(
    applicants: List[Applicant],
    stats: DirectionStats,
    user_score: float,
    year: int,
    current_direction_id: int,
    use_cache: bool = True,
    p_likely: float = P_LIKELY_DEFAULT,
    on_progress: Optional[Callable[[str], None]] = None,
) -> ContractResult:
    k = stats.k
    pool = build_contract_pool(applicants, stats.bm_max)
    above_user = [m for m in pool if m.applicant.score > user_score]

    hard_count = sum(1 for m in above_user if m.applicant.priority == 1)
    to_check = [m for m in above_user if m.applicant.priority != 1]

    assessments: List[ContractAssessment] = []
    for member in to_check:
        assessments.append(assess_pool_member(member, year, current_direction_id, use_cache, p_likely))
        if on_progress:
            on_progress(member.applicant.name)

    matched = [a for a in assessments if a.status != STATUS_UNKNOWN]
    unknown = [a for a in assessments if a.status == STATUS_UNKNOWN]

    stays_count = sum(1 for a in matched if a.status == STATUS_STAYS)
    likely_count = sum(1 for a in matched if a.status == STATUS_LIKELY)
    definite_count = sum(1 for a in matched if a.status == STATUS_DEFINITE)
    unknown_count = len(unknown)

    expected_stay = sum(1.0 - a.p_vacate for a in matched)

    optimistic_bound = hard_count + stays_count
    pessimistic_bound = hard_count + stays_count + likely_count + unknown_count
    expected_count = hard_count + expected_stay

    est = estimate(optimistic_bound, expected_count, pessimistic_bound, k)

    return ContractResult(
        k=k,
        hard_count=hard_count,
        pool_size_total=len(pool),
        assessments=assessments,
        stays_count=stays_count,
        likely_count=likely_count,
        definite_count=definite_count,
        unknown_count=unknown_count,
        optimistic_bound=optimistic_bound,
        expected_count=expected_count,
        pessimistic_bound=pessimistic_bound,
        chance=est.chance,
        pessimistic_chance=est.pessimistic_chance,
        optimistic_chance=est.optimistic_chance,
        verdict=est.verdict,
    )
