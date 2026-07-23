import argparse
import sys

import requests
from bs4 import BeautifulSoup

from .output import write_json, write_md
from .parse import parse_applicants, parse_stats
from .scraper import fetch_page
from .verdict import VerdictResult, budget_applicants, build_verdict
from .models import DirectionStats


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
    lines.append(f"ВЕРДИКТ: {result.verdict.upper()}")
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

    if args.json:
        write_json(args.json, stats, result)
    if args.md:
        write_md(args.md, report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
