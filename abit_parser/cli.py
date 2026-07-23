import argparse
import re
import sys

import requests
from bs4 import BeautifulSoup

from . import config
from .cross_analysis import CrossCheckResult, run_cross_check
from .explain import generate_explanation
from .models import DirectionStats
from .output import write_json, write_md
from .parse import parse_applicants, parse_stats
from .scraper import fetch_page
from .verdict import VerdictResult, budget_applicants, build_verdict

DIRECTION_ID_RE = re.compile(r"/direction/(\d+)")
YEAR_RE = re.compile(r"/rate(\d{4})/")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Персональний список конкурентів за бюджетне місце (abit-poisk.org.ua)."
    )
    parser.add_argument(
        "url", help="Посилання на напрям, напр. https://abit-poisk.org.ua/rate2026/direction/1613482"
    )
    parser.add_argument("--score", type=float, required=True, help="Ваш конкурсний бал")
    parser.add_argument(
        "--priority", type=int, required=True, help="Ваш пріоритет заяви на цю спеціальність"
    )
    parser.add_argument("--funding", choices=["Б", "К"], required=True, help="Б = бюджет, К = контракт")
    parser.add_argument("--json", metavar="PATH", help="Додатково зберегти результат у JSON")
    parser.add_argument("--md", metavar="PATH", help="Додатково зберегти результат у Markdown")
    parser.add_argument(
        "--cross-check",
        dest="cross_check",
        action="store_true",
        default=True,
        help="Уточнити вердикт крос-аналізом по імені (v2, дефолт — увімкнено)",
    )
    parser.add_argument(
        "--no-cross-check",
        dest="cross_check",
        action="store_false",
        help="Вимкнути крос-аналіз v2, лишити тільки широку вилку v1",
    )
    parser.add_argument(
        "--p-likely",
        type=float,
        default=config.P_LIKELY_DEFAULT,
        help=f"Ймовірність, що 'ймовірний' конкурент звільнить місце (дефолт {config.P_LIKELY_DEFAULT})",
    )
    parser.add_argument(
        "--no-cache",
        dest="use_cache",
        action="store_false",
        default=True,
        help="Не використовувати дисковий кеш результатів пошуку (v2)",
    )
    parser.add_argument(
        "--explain", action="store_true", help="Переказати результат людською мовою через Anthropic API"
    )
    return parser


def format_report(
    stats: DirectionStats, result: VerdictResult, user_score: float, user_priority: int, user_funding: str
) -> str:
    lines = []
    lines.append(f"Напрям: {stats.title}")
    comp = f"  Конкурс на бюджет={stats.competition}" if stats.competition is not None else ""
    lines.append(f"ВМ={stats.vm}  БМmax={stats.bm_max}  К={stats.k}  Заяв={stats.zayav}{comp}")
    lines.append(f"Ваші дані: бал={user_score}  пріоритет={user_priority}  {user_funding}")
    lines.append("")
    lines.append(f"Оптимістична межа місця: {result.optimistic_bound}  (лише залізні конкуренти)")
    lines.append(f"Песимістична межа місця: {result.pessimistic_bound}  (залізні + група ризику)")
    lines.append(f"M (бюджетних місць): {result.m}")
    lines.append(f"ВЕРДИКТ v1: {result.verdict.upper()}")
    lines.append("")
    if result.competitors:
        lines.append("Персональний список конкурентів (бюджетники з вищим балом):")
        for c in result.competitors:
            a = c.applicant
            lines.append(
                f"  #{a.position:>3}  {a.name:<25} бал={a.score:<8} пріоритет={a.priority}  [{c.category}]"
            )
    else:
        lines.append("Конкурентів з вищим балом серед бюджетників немає.")
    return "\n".join(lines)


def format_cross_check_report(cc: CrossCheckResult) -> str:
    lines = []
    lines.append("")
    lines.append("=== Крос-аналіз v2 (уточнення групи ризику) ===")
    lines.append(
        f"Тверді конкуренти (пріоритет 1): {cc.hard_count}  |  "
        f"лишаються: {cc.stays_count}  ймовірно підуть: {cc.likely_count}  "
        f"точно підуть: {cc.definite_count}  невизначено: {cc.unknown_count}"
    )
    lines.append(f"Оптимістична межа: {cc.optimistic_bound}  Очікувана: {cc.expected_count:.1f}  Песимістична: {cc.pessimistic_bound}  (M={cc.m})")
    lines.append(f"Оцінка шансу проходження: {cc.chance * 100:.0f}%  (евристика, не строга ймовірність)")
    lines.append("")
    lines.append("Деталі по групі ризику:")
    for a in cc.assessments:
        applicant = a.competitor.applicant
        choice_note = ""
        if a.best_choice:
            bc = a.best_choice
            choice_note = f" -> вищий пріоритет: {bc.university} / {bc.specialty} поз.{bc.position} (БМmax={bc.seats.bm_max}, БМmin={bc.seats.bm_min})"
        note = f"  ({a.note})" if a.note else ""
        lines.append(f"  {applicant.name:<25} [{a.status}]{choice_note}{note}")
    return "\n".join(lines)


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    print(f"Завантажую: {args.url}")
    try:
        html = fetch_page(args.url)
    except requests.RequestException as e:
        print(f"Помилка запиту: {e}")
        return 1

    soup = BeautifulSoup(html, "html.parser")

    try:
        stats = parse_stats(soup)
        applicants = parse_applicants(soup)
    except ValueError as e:
        print(f"Помилка розбору сторінки: {e}")
        return 1

    if not applicants:
        print("Не знайдено жодного рядка заявок — перевірте посилання.")
        return 1

    budget = budget_applicants(applicants)
    result = build_verdict(applicants, stats, args.score)

    report = format_report(stats, result, args.score, args.priority, args.funding)
    print()
    print(f"Бюджетних заявок у списку (після відкидання дублів/відмов): {len(budget)}")
    print()
    print(report)

    cross_result = None
    if args.cross_check:
        dir_match = DIRECTION_ID_RE.search(args.url)
        year_match = YEAR_RE.search(args.url)
        if not dir_match or not year_match:
            print(
                "\nНе вдалось витягти direction_id/рік з URL — пропускаю крос-аналіз v2, лишаю вердикт v1."
            )
        elif not result.competitors:
            pass  # нема кого перевіряти — v1 вже фінальний
        else:
            direction_id = int(dir_match.group(1))
            year = int(year_match.group(1))
            try:
                cross_result = run_cross_check(
                    result, year, direction_id, use_cache=args.use_cache, p_likely=args.p_likely
                )
                print(format_cross_check_report(cross_result))
            except Exception as e:
                print(f"\nКрос-аналіз v2 впав ({e}) — лишаю вердикт v1 без змін.")
                cross_result = None

    if args.explain:
        payload = {
            "direction": stats.title,
            "verdict_v1": result.verdict,
            "m": result.m,
            "optimistic_bound": result.optimistic_bound,
            "pessimistic_bound": result.pessimistic_bound,
        }
        if cross_result is not None:
            payload["cross_check_v2"] = {
                "chance": cross_result.chance,
                "optimistic_bound": cross_result.optimistic_bound,
                "expected_count": cross_result.expected_count,
                "pessimistic_bound": cross_result.pessimistic_bound,
                "hard_count": cross_result.hard_count,
                "stays_count": cross_result.stays_count,
                "likely_count": cross_result.likely_count,
                "definite_count": cross_result.definite_count,
                "unknown_count": cross_result.unknown_count,
            }
        explanation = generate_explanation(payload)
        if explanation:
            print("\n=== Пояснення (LLM) ===")
            print(explanation)

    if args.json:
        write_json(args.json, stats, result, cross_result)
    if args.md:
        write_md(args.md, report + (format_cross_check_report(cross_result) if cross_result else ""))

    return 0


if __name__ == "__main__":
    sys.exit(main())
