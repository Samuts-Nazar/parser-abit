from dataclasses import dataclass
from typing import Optional


@dataclass
class Applicant:
    position: int
    name: str
    priority: int
    funding: str  # "Б" or "К"
    score: float
    status: str
    quota: Optional[str]


@dataclass
class DirectionStats:
    title: str
    vm: int  # всього місць
    bm_max: int  # M — максимум бюджетних місць
    k: int  # контрактних місць
    zayav: int  # кількість заяв
    competition: Optional[float]  # конкурс на бюджет
