import re
from dataclasses import asdict
from typing import List

import certifi
import requests
from bs4 import BeautifulSoup

from . import cache
from .config import SEARCH_URL
from .models import Seats, SearchApplication
from .scraper import HEADERS as BASE_HEADERS
from .throttle import throttled_request

SEARCH_HEADERS = {
    **BASE_HEADERS,
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://abit-poisk.org.ua/",
    "Origin": "https://abit-poisk.org.ua",
}

PRIORITY_RE = re.compile(r"(\d+)\s*\((Б|К)\)")
DIRECTION_ID_RE = re.compile(r"/direction/(\d+)")
VM_RE = re.compile(r"ВМ\s+(\d+)")
BM_MAX_RE = re.compile(r"БМ\s*MAX\s+(\d+)", re.IGNORECASE)
BM_MIN_RE = re.compile(r"БМ\s*MIN\s+(\d+)", re.IGNORECASE)
K_RE = re.compile(r"К\s+(\d+)")


def _post_search(query: str, timeout: int = 15) -> dict:
    data = f"search={query}".encode("utf-8")
    with throttled_request():
        try:
            resp = requests.post(
                SEARCH_URL, headers=SEARCH_HEADERS, data=data, timeout=timeout, verify=certifi.where()
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.SSLError:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = requests.post(
                SEARCH_URL, headers=SEARCH_HEADERS, data=data, timeout=timeout, verify=False
            )
            resp.raise_for_status()
            return resp.json()


def _parse_seats(text: str) -> Seats:
    vm_m = VM_RE.search(text)
    bmmax_m = BM_MAX_RE.search(text)
    bmmin_m = BM_MIN_RE.search(text)
    k_m = K_RE.search(text)
    return Seats(
        vm=int(vm_m.group(1)) if vm_m else None,
        bm_max=int(bmmax_m.group(1)) if bmmax_m else None,
        bm_min=int(bmmin_m.group(1)) if bmmin_m else None,
        k=int(k_m.group(1)) if k_m else None,
    )


def parse_search_results(html: str) -> List[SearchApplication]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []

    thead = table.find("thead")
    tbody = table.find("tbody")
    if thead is None or tbody is None:
        return []
    headers = [th.get_text(strip=True) for th in thead.find_all("th")]

    results: List[SearchApplication] = []
    for row in tbody.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) != len(headers):
            continue
        cells = dict(zip(headers, tds))

        num_cell = cells.get("№")
        num_link = num_cell.find("a") if num_cell else None
        if num_link is None:
            continue
        dir_match = DIRECTION_ID_RE.search(num_link.get("href", ""))
        if not dir_match:
            continue
        pos_text = num_link.get_text(strip=True)
        if not pos_text.isdigit():
            continue

        pr_cell = cells.get("П")
        if pr_cell is None:
            continue
        pr_match = PRIORITY_RE.search(pr_cell.get_text())
        if not pr_match:
            continue

        score_cell = cells.get("∑")
        if score_cell is None:
            continue
        score_text = score_cell.get_text(strip=True).replace(",", ".")
        try:
            score = float(score_text)
        except ValueError:
            continue

        status_cell = cells.get("С")
        status = status_cell.get_text(strip=True) if status_cell else ""

        university_cell = cells.get("ВНЗ")
        university = university_cell.get_text(strip=True) if university_cell else ""

        specialty_cell = cells.get("Спец.")
        specialty = specialty_cell.get_text(separator=" ", strip=True) if specialty_cell else ""

        seats_cell = cells.get("Місця")
        seats = _parse_seats(seats_cell.get_text(separator=" ", strip=True)) if seats_cell else Seats(
            None, None, None, None
        )

        quota_cell = cells.get("Кв")
        quota_text = quota_cell.get_text(strip=True) if quota_cell else None
        quota = quota_text if quota_text and quota_text != "—" else None

        results.append(
            SearchApplication(
                direction_id=int(dir_match.group(1)),
                position=int(pos_text),
                priority=int(pr_match.group(1)),
                funding=pr_match.group(2),
                score=score,
                status=status,
                university=university,
                specialty=specialty,
                seats=seats,
                quota=quota,
            )
        )

    return results


def search_applicant(name: str, year: int, use_cache: bool = True) -> List[SearchApplication]:
    if use_cache:
        cached = cache.get(name, year)
        if cached is not None:
            return [SearchApplication(**{**c, "seats": Seats(**c["seats"])}) for c in cached]

    payload = _post_search(f"{name} {year}")
    results = parse_search_results(payload.get("html", ""))

    if use_cache:
        cache.set(name, year, [asdict(r) for r in results])

    return results
