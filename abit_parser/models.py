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
    bm_max: int  # M — максимум бюджетних місць. Єдине поле, без якого падаємо.
    vm: Optional[int] = None  # всього місць
    k: Optional[int] = None  # контрактних місць — шапка не завжди його показує
    zayav: Optional[int] = None  # кількість заяв
    competition: Optional[float] = None  # конкурс на бюджет


@dataclass
class Seats:
    vm: Optional[int]
    bm_max: Optional[int]
    bm_min: Optional[int]  # присутнє не для кожного напряму
    k: Optional[int]


@dataclass
class SearchApplication:
    """Один рядок з відповіді пошуку /api/statements/ — одна заява людини."""

    direction_id: int
    position: int  # сира позиція (бюджет+контракт разом)
    priority: int
    funding: str  # "Б" or "К"
    score: float
    status: str
    university: str
    specialty: str
    seats: Seats
    quota: Optional[str] = None
