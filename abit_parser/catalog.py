"""
Довідник область → ВНЗ → спеціальність — для інтерактивного вибору напряму
в боті (замість вставляння прямого посилання). Лише БУДУЄ /rate{year}/direction/{id}
посилання; сам рейтинговий список і надалі розбирає parse.py — тут нова логіка
розбору не додається.
"""

import re
from dataclasses import dataclass
from typing import List, Optional

from bs4 import BeautifulSoup

from . import catalog_cache
from .scraper import fetch_page

BASE_URL = "https://abit-poisk.org.ua"

# Порядок посилань на /rate-review/ — спадний за роком (2026, 2025, 2024...),
# перше й є поточною кампанією. Це живий факт із самого сайту, а не
# припущення з календаря — кампанії стартують не завжди в одну дату року,
# і хардкодити рік означало б ламати picker щороку заново.
YEAR_LINK_RE = re.compile(r'href="/rate(\d{4})/?"')
REGION_ID_RE = re.compile(r"/region/(\d+)")
UNIVER_ID_RE = re.compile(r"/univer/(\d+)")
DIRECTION_ID_RE = re.compile(r"/direction/(\d+)")


@dataclass
class Region:
    id: int
    name: str


@dataclass
class University:
    id: int
    name: str


@dataclass
class Specialty:
    direction_id: int
    title: str
    faculty: str


def _table(soup: BeautifulSoup) -> Optional[BeautifulSoup]:
    return soup.find("table", id="dataTable") or soup.find("table")


def get_current_year(use_cache: bool = True) -> int:
    if use_cache:
        cached = catalog_cache.get("current_year")
        if cached:
            return cached[0]["year"]

    html = fetch_page(f"{BASE_URL}/rate-review/")
    match = YEAR_LINK_RE.search(html)
    if not match:
        raise ValueError(
            "Не вдалось визначити поточний рік кампанії зі сторінки /rate-review/ — верстка сайту могла змінитись."
        )
    year = int(match.group(1))
    catalog_cache.set("current_year", [{"year": year}])
    return year


def list_regions(year: int, use_cache: bool = True) -> List[Region]:
    cache_key = f"regions:{year}"
    if use_cache:
        cached = catalog_cache.get(cache_key)
        if cached:
            return [Region(**r) for r in cached]

    html = fetch_page(f"{BASE_URL}/rate{year}")
    soup = BeautifulSoup(html, "html.parser")
    table = _table(soup)
    if table is None:
        raise ValueError("Таблицю областей не знайдено — верстка сторінки /rate{year} могла змінитись.")

    regions: List[Region] = []
    for row in table.find_all("tr"):
        link = row.find("a", href=REGION_ID_RE)
        if link is None:
            continue
        region_id = int(REGION_ID_RE.search(link["href"]).group(1))
        regions.append(Region(id=region_id, name=link.get_text(strip=True)))

    catalog_cache.set(cache_key, [{"id": r.id, "name": r.name} for r in regions])
    return regions


def list_universities(year: int, region_id: int, use_cache: bool = True) -> List[University]:
    cache_key = f"universities:{year}:{region_id}"
    if use_cache:
        cached = catalog_cache.get(cache_key)
        if cached:
            return [University(**u) for u in cached]

    html = fetch_page(f"{BASE_URL}/rate{year}/region/{region_id}")
    soup = BeautifulSoup(html, "html.parser")
    table = _table(soup)
    if table is None:
        raise ValueError("Таблицю ВНЗ не знайдено — верстка сторінки /rate{year}/region/{id} могла змінитись.")

    universities: List[University] = []
    for row in table.find_all("tr"):
        # Рядки-розділювачі категорій ("Університети"/"Інститути") не мають
        # посилання на /univer/ — просто пропускаються цим фільтром, без
        # окремого розпізнавання colspan.
        link = row.find("a", href=UNIVER_ID_RE)
        if link is None:
            continue
        univer_id = int(UNIVER_ID_RE.search(link["href"]).group(1))
        # title-атрибут містить повну назву без скорочення на кшталт "«НТУУ» ".
        name = link.get("title") or link.get_text(strip=True)
        universities.append(University(id=univer_id, name=name))

    catalog_cache.set(cache_key, [{"id": u.id, "name": u.name} for u in universities])
    return universities


def list_specialties(year: int, univer_id: int, use_cache: bool = True) -> List[Specialty]:
    cache_key = f"specialties:{year}:{univer_id}"
    if use_cache:
        cached = catalog_cache.get(cache_key)
        if cached:
            return [Specialty(**s) for s in cached]

    html = fetch_page(f"{BASE_URL}/rate{year}/univer/{univer_id}")
    soup = BeautifulSoup(html, "html.parser")
    table = _table(soup)
    if table is None:
        raise ValueError("Таблицю спеціальностей не знайдено — верстка сторінки /rate{year}/univer/{id} могла змінитись.")

    specialties: List[Specialty] = []
    for row in table.find_all("tr"):
        link = row.find("a", href=DIRECTION_ID_RE)
        if link is None:
            continue
        direction_id = int(DIRECTION_ID_RE.search(link["href"]).group(1))

        title_div = link.find("div", class_="title")
        title = title_div.get_text(separator=" ", strip=True) if title_div else link.get_text(strip=True)

        faculty = ""
        if title_div is not None and title_div.parent is not None:
            siblings = title_div.parent.find_all("div", recursive=False)
            if len(siblings) > 1:
                faculty = siblings[1].get_text(strip=True)

        specialties.append(Specialty(direction_id=direction_id, title=title, faculty=faculty))

    catalog_cache.set(
        cache_key,
        [{"direction_id": s.direction_id, "title": s.title, "faculty": s.faculty} for s in specialties],
    )
    return specialties
