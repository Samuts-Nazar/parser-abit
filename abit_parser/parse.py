import re
from typing import List

from bs4 import BeautifulSoup

from .models import Applicant, DirectionStats

PRIORITY_RE = re.compile(r"(\d+)\s*\((Б|К)\)")
VM_RE = re.compile(r"ВМ\s*(\d+)")
BM_MAX_RE = re.compile(r"БМ\s*max\s*(\d+)", re.IGNORECASE)
# Шапка нестабільна між ВНЗ/спеціальностями: К може бути відсутній, а кирилична
# К (U+041A) і латинська K (U+004B) виглядають однаково, але це різні символи —
# тому шукаємо обидва. Матчимо лише як окремий токен (з обох боків межа рядка/
# роздільника/пробілу), інакше "Конкурс" теж почався б з "К" і давав хибний збіг.
K_RE = re.compile(r"(?:^|[•\s])[КK]\s*(\d+)(?:$|[•\s])")
ZAYAV_RE = re.compile(r"Заяв\s*(\d+)")
COMPETITION_RE = re.compile(r"Конкурс на бюджет\s*([\d.]+)")


def parse_stats(soup: BeautifulSoup) -> DirectionStats:
    header = soup.find("div", class_="card-header")
    if header is None:
        raise ValueError("Не знайдено блок шапки сторінки (card-header) — верстка сайту могла змінитись.")

    title_tag = header.find("h2", class_="headline")
    title = title_tag.get_text(strip=True) if title_tag else ""

    stats_text = None
    for sh in header.find_all("div", class_="subhead-2"):
        text = sh.get_text(separator=" ", strip=True)
        if "БМ" in text:
            stats_text = text
            break
    if stats_text is None:
        raise ValueError("Не знайдено блок статистики (ВМ/БМmax/К) у шапці сторінки.")

    # Нерозривні пробіли (\xa0) та подвійні пробіли ламають регекси без \s*-меж —
    # нормалізуємо один раз, до всіх подальших пошуків.
    stats_text = re.sub(r"\s+", " ", stats_text.replace("\xa0", " ")).strip()

    def find_int(pattern: re.Pattern) -> "int | None":
        m = pattern.search(stats_text)
        return int(m.group(1)) if m else None

    vm = find_int(VM_RE)
    bm_max = find_int(BM_MAX_RE)
    if bm_max is None:
        raise ValueError(f"Не вдалося витягти 'БМmax' з шапки — без нього вердикт неможливий: {stats_text!r}")

    k = find_int(K_RE)
    # Дехто з ВНЗ шапку з К просто не показує — деривуємо з ВМ/БМmax, якщо можна.
    if k is None and vm is not None and vm > bm_max:
        k = vm - bm_max

    zayav = find_int(ZAYAV_RE)

    comp_match = COMPETITION_RE.search(stats_text)
    competition = float(comp_match.group(1)) if comp_match else None

    return DirectionStats(title=title, vm=vm, bm_max=bm_max, k=k, zayav=zayav, competition=competition)


def parse_applicants(soup: BeautifulSoup) -> List[Applicant]:
    table = soup.find("table")
    if table is None:
        raise ValueError(
            "Таблицю рейтингу не знайдено — ймовірно дані підвантажуються через JS або змінилась верстка сайту."
        )

    applicants: List[Applicant] = []
    rows = table.find_all("tr")[1:]  # перший рядок — заголовок

    for row in rows:
        cells = {td.get("data-header"): td for td in row.find_all("td")}

        pos_td = cells.get("#")
        priority_td = cells.get("Пріоритет")
        score_td = cells.get("Бал")
        name_td = row.find("td", class_="application-cell-ab-name")

        if pos_td is None or priority_td is None or score_td is None or name_td is None:
            continue  # рядок нестандартної форми (наприклад, мобільний дубль) — пропускаємо

        pos_text = pos_td.get_text(strip=True)
        if not pos_text.isdigit():
            continue
        position = int(pos_text)

        name_link = name_td.find("a")
        name = name_link.get_text(strip=True) if name_link else name_td.get_text(strip=True)

        pr_match = PRIORITY_RE.search(priority_td.get_text())
        if not pr_match:
            continue
        priority = int(pr_match.group(1))
        funding = pr_match.group(2)

        score_text = score_td.get_text(strip=True).replace(",", ".")
        try:
            score = float(score_text)
        except ValueError:
            continue

        status_td = cells.get("Статус")
        status = status_td.get_text(strip=True) if status_td else ""

        quota_td = cells.get("Квоти")
        quota_text = quota_td.get_text(strip=True) if quota_td else None
        quota = quota_text if quota_text and quota_text != "—" else None

        applicants.append(
            Applicant(
                position=position,
                name=name,
                priority=priority,
                funding=funding,
                score=score,
                status=status,
                quota=quota,
            )
        )

    return applicants
