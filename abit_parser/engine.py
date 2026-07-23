import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import requests
from bs4 import BeautifulSoup

from .config import P_LIKELY_DEFAULT
from .cross_analysis import CrossCheckResult, run_cross_check
from .models import Applicant, DirectionStats
from .parse import parse_applicants, parse_stats
from .scraper import fetch_page
from .verdict import VerdictResult, budget_applicants, build_verdict

DIRECTION_ID_RE = re.compile(r"/direction/(\d+)")
YEAR_RE = re.compile(r"/rate(\d{4})/")

# (виконано, всього, ім'я поточної людини)
ProgressCallback = Callable[[int, int, str], None]


class AnalysisError(Exception):
    """Помилка, після якої рахувати нема з чим (мережа, розбір сторінки, порожній список)."""


@dataclass
class AnalysisResult:
    url: str
    stats: DirectionStats
    applicants: List[Applicant]
    budget_count: int
    verdict: VerdictResult
    cross_check: Optional[CrossCheckResult] = None
    warnings: List[str] = field(default_factory=list)


def run_analysis(
    url: str,
    score: float,
    priority: int,
    funding: str,
    cross_check: bool = True,
    p_likely: float = P_LIKELY_DEFAULT,
    use_cache: bool = True,
    on_progress: Optional[ProgressCallback] = None,
) -> AnalysisResult:
    """Єдина точка входу до всієї логіки — CLI, GUI та сумаризатор викликають лише це."""
    try:
        html = fetch_page(url)
    except requests.RequestException as e:
        raise AnalysisError(f"Помилка запиту: {e}") from e

    soup = BeautifulSoup(html, "html.parser")

    try:
        stats = parse_stats(soup)
        applicants = parse_applicants(soup)
    except ValueError as e:
        raise AnalysisError(f"Помилка розбору сторінки: {e}") from e

    if not applicants:
        raise AnalysisError("Не знайдено жодного рядка заявок — перевірте посилання.")

    budget = budget_applicants(applicants)
    verdict = build_verdict(applicants, stats, score)

    warnings: List[str] = []
    cross_result: Optional[CrossCheckResult] = None

    if cross_check:
        dir_match = DIRECTION_ID_RE.search(url)
        year_match = YEAR_RE.search(url)
        risk_pool_size = sum(1 for c in verdict.competitors if c.category == "група ризику")

        if not dir_match or not year_match:
            warnings.append(
                "Не вдалось витягти direction_id/рік з URL — крос-аналіз v2 пропущено."
            )
        elif risk_pool_size == 0:
            pass  # нема кого перевіряти — вердикт v1 вже фінальний
        else:
            direction_id = int(dir_match.group(1))
            year = int(year_match.group(1))
            done = 0

            def _forward_progress(name: str) -> None:
                nonlocal done
                done += 1
                if on_progress:
                    on_progress(done, risk_pool_size, name)

            try:
                cross_result = run_cross_check(
                    verdict,
                    year,
                    direction_id,
                    use_cache=use_cache,
                    p_likely=p_likely,
                    on_progress=_forward_progress,
                )
            except Exception as e:
                warnings.append(f"Крос-аналіз v2 впав ({e}) — лишається вердикт v1 без змін.")
                cross_result = None

    return AnalysisResult(
        url=url,
        stats=stats,
        applicants=applicants,
        budget_count=len(budget),
        verdict=verdict,
        cross_check=cross_result,
        warnings=warnings,
    )
