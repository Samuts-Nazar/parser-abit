import json
from dataclasses import asdict
from typing import Optional

from .contract_analysis import ContractResult
from .cross_analysis import CrossCheckResult
from .models import DirectionStats
from .verdict import VerdictResult


def write_json(
    path: str,
    stats: DirectionStats,
    result: VerdictResult,
    cross_result: Optional[CrossCheckResult] = None,
    contract_result: Optional[ContractResult] = None,
) -> None:
    payload = {
        "direction": asdict(stats),
        "verdict_v1": {
            "m": result.m,
            "optimistic_bound": result.optimistic_bound,
            "pessimistic_bound": result.pessimistic_bound,
            "verdict": result.verdict,
        },
        "competitors": [
            {**asdict(c.applicant), "category": c.category} for c in result.competitors
        ],
    }
    if cross_result is not None:
        payload["cross_check_v2"] = {
            "hard_count": cross_result.hard_count,
            "stays_count": cross_result.stays_count,
            "likely_count": cross_result.likely_count,
            "definite_count": cross_result.definite_count,
            "unknown_count": cross_result.unknown_count,
            "optimistic_bound": cross_result.optimistic_bound,
            "expected_count": cross_result.expected_count,
            "pessimistic_bound": cross_result.pessimistic_bound,
            "chance": cross_result.chance,
            "pessimistic_chance": cross_result.pessimistic_chance,
            "optimistic_chance": cross_result.optimistic_chance,
            "verdict": cross_result.verdict,
            "m": cross_result.m,
            "assessments": [
                {
                    "name": a.competitor.applicant.name,
                    "status": a.status,
                    "p_vacate": a.p_vacate,
                    "note": a.note,
                    "best_choice": asdict(a.best_choice) if a.best_choice else None,
                }
                for a in cross_result.assessments
            ],
        }
    if contract_result is not None:
        payload["contract_v4"] = {
            "k": contract_result.k,
            "pool_size_total": contract_result.pool_size_total,
            "hard_count": contract_result.hard_count,
            "stays_count": contract_result.stays_count,
            "likely_count": contract_result.likely_count,
            "definite_count": contract_result.definite_count,
            "unknown_count": contract_result.unknown_count,
            "optimistic_bound": contract_result.optimistic_bound,
            "expected_count": contract_result.expected_count,
            "pessimistic_bound": contract_result.pessimistic_bound,
            "chance": contract_result.chance,
            "pessimistic_chance": contract_result.pessimistic_chance,
            "optimistic_chance": contract_result.optimistic_chance,
            "verdict": contract_result.verdict,
            "assessments": [
                {
                    "name": a.member.applicant.name,
                    "origin": a.member.origin,
                    "status": a.status,
                    "p_vacate": a.p_vacate,
                    "note": a.note,
                    "best_choice": asdict(a.best_choice) if a.best_choice else None,
                }
                for a in contract_result.assessments
            ],
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Збережено JSON у {path}")


def write_md(path: str, report_text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("```\n")
        f.write(report_text)
        f.write("\n```\n")
    print(f"Збережено Markdown у {path}")
