import json
from dataclasses import asdict

from .models import DirectionStats
from .verdict import VerdictResult


def write_json(path: str, stats: DirectionStats, result: VerdictResult) -> None:
    payload = {
        "direction": asdict(stats),
        "verdict": {
            "m": result.m,
            "optimistic_bound": result.optimistic_bound,
            "pessimistic_bound": result.pessimistic_bound,
            "verdict": result.verdict,
        },
        "competitors": [
            {**asdict(c.applicant), "category": c.category} for c in result.competitors
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
