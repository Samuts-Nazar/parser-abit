import argparse
import sys
from typing import Optional

from . import config
from .contract_analysis import ContractResult
from .cross_analysis import CrossCheckResult
from .engine import AnalysisError, AnalysisResult, run_analysis, run_contract_chance
from .format_text import chance_line, rank_strip
from .models import DirectionStats
from .output import write_json, write_md
from .summarize import PROVIDER_ANTHROPIC, SummarizeError, build_depersonalized_payload, generate_summary
from .verdict import VerdictResult


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
    parser.add_argument(
        "--contract",
        action="store_true",
        help="Порахувати шанс на контракт (v4) — окремі мережеві запити, не автоматично",
    )
    return parser


def format_report(
    stats: DirectionStats, result: VerdictResult, user_score: float, user_priority: int, user_funding: str
) -> str:
    def _fmt(v: object) -> str:
        return "—" if v is None else str(v)

    lines = []
    lines.append(f"Напрям: {stats.title}")
    comp = f"  Конкурс на бюджет={stats.competition}" if stats.competition is not None else ""
    lines.append(
        f"ВМ={_fmt(stats.vm)}  БМmax={stats.bm_max}  К={_fmt(stats.k)}  Заяв={_fmt(stats.zayav)}{comp}"
    )
    if stats.k is None:
        lines.append("(контрактних місць у шапці не вказано — контрактна фіча вимкнена для цього напряму)")
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
    lines.append("")
    lines.append(rank_strip(cc.optimistic_bound, cc.expected_count, cc.pessimistic_bound, cc.m))
    lines.append(f"ВЕРДИКТ: {cc.verdict.upper()}")
    lines.append(chance_line(cc.chance, cc.pessimistic_chance, cc.optimistic_chance))
    lines.append("")
    lines.append("Деталі по групі ризику:")
    for a in cc.assessments:
        applicant = a.competitor.applicant
        choice_note = ""
        if a.best_choice:
            bc = a.best_choice
            choice_note = (
                f" -> вищий пріоритет: {bc.university} / {bc.specialty} поз.{bc.position} "
                f"(БМmax={bc.seats.bm_max}, БМmin={bc.seats.bm_min})"
            )
        note = f"  ({a.note})" if a.note else ""
        lines.append(f"  {applicant.name:<25} [{a.status}]{choice_note}{note}")
    return "\n".join(lines)


def format_contract_report(cr: ContractResult) -> str:
    lines = []
    lines.append("")
    lines.append("=== Шанс на контракт (v4) ===")
    lines.append(f"K (контрактних місць): {cr.k}   Розмір контрактного пулу (усі бали): {cr.pool_size_total}")
    lines.append(
        f"Тверді конкуренти (пріоритет 1): {cr.hard_count}  |  "
        f"лишаються: {cr.stays_count}  ймовірно підуть: {cr.likely_count}  "
        f"точно підуть: {cr.definite_count}  невизначено: {cr.unknown_count}"
    )
    lines.append("")
    lines.append(rank_strip(cr.optimistic_bound, cr.expected_count, cr.pessimistic_bound, cr.k))
    lines.append(f"ВЕРДИКТ (контракт): {cr.verdict.upper()}")
    lines.append(chance_line(cr.chance, cr.pessimistic_chance, cr.optimistic_chance))
    if cr.assessments:
        lines.append("")
        lines.append("Деталі по контрактному пулу вище вашого балу:")
        for a in cr.assessments:
            applicant = a.member.applicant
            choice_note = ""
            if a.best_choice:
                bc = a.best_choice
                choice_note = (
                    f" -> вищий пріоритет: {bc.university} / {bc.specialty} поз.{bc.position} "
                    f"(БМmax={bc.seats.bm_max}, БМmin={bc.seats.bm_min}, К={bc.seats.k})"
                )
            note = f"  ({a.note})" if a.note else ""
            lines.append(f"  {applicant.name:<25} [{a.member.origin}/{a.status}]{choice_note}{note}")
    return "\n".join(lines)


def _console_progress(done: int, total: int, name: str) -> None:
    print(f"  крос-аналіз {done}/{total}: {name}...")


def _console_contract_progress(done: int, total: int, name: str) -> None:
    print(f"  контракт {done}/{total}: {name}...")


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    print(f"Завантажую: {args.url}")

    try:
        result: AnalysisResult = run_analysis(
            args.url,
            args.score,
            args.priority,
            args.funding,
            cross_check=args.cross_check,
            p_likely=args.p_likely,
            use_cache=args.use_cache,
            on_progress=_console_progress if args.cross_check else None,
        )
    except AnalysisError as e:
        print(str(e))
        return 1

    report = format_report(result.stats, result.verdict, args.score, args.priority, args.funding)
    print()
    print(f"Бюджетних заявок у списку (після відкидання дублів/відмов): {result.budget_count}")
    print()
    print(report)

    for w in result.warnings:
        print(f"\n{w}")

    if result.cross_check is not None:
        print(format_cross_check_report(result.cross_check))

    contract_result: Optional[ContractResult] = None
    if args.contract:
        try:
            contract_result = run_contract_chance(
                result,
                p_likely=args.p_likely,
                use_cache=args.use_cache,
                on_progress=_console_contract_progress,
            )
            print(format_contract_report(contract_result))
        except AnalysisError as e:
            print(f"\n{e}")

    if args.explain:
        payload = build_depersonalized_payload(result, contract_result)
        try:
            explanation = generate_summary(payload, provider=PROVIDER_ANTHROPIC)
            print("\n=== Пояснення (LLM) ===")
            print(explanation)
        except SummarizeError as e:
            print(f"\n{e}")

    if args.json:
        write_json(args.json, result.stats, result.verdict, result.cross_check, contract_result)
    if args.md:
        md_text = report
        if result.cross_check is not None:
            md_text += format_cross_check_report(result.cross_check)
        if contract_result is not None:
            md_text += format_contract_report(contract_result)
        write_md(args.md, md_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
