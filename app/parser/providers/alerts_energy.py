"""Резервне джерело: HTML-сторінка alerts.energy/zaporizka-oblast (без ключа).

Використовується, коли OE_OUTAGE_DATA недоступний. Мінуси: HTML може змінитись,
а alerts.energy іноді віддає 429 спільним IP хостингів (Fly.io тощо).
"""
import re

import httpx
from bs4 import BeautifulSoup

from datetime import datetime

from app.parser.base import TZ, Schedule, title_for

URL = "https://alerts.energy/zaporizka-oblast"

TIME_RE = re.compile(r"(\d{2}:\d{2})[–\-](\d{2}:\d{2})")


def _parse_queues_from_log(text: str) -> tuple[dict[str, list[tuple[str, str]]], str | None]:
    """
    Парсимо блок «Оновлення графіків відключень за сьогодні». Формат запису:

        16:51ПАТ «Запоріжжяобленерго»оновлено графік на сьогодні
        Черга 1.1: 02:30-06:00, 11:30-16:30→02:30-06:00, 11:30-16:30, 20:30-24:00Додано ...

    Старе расписание від нового відділяє стрілка «→» — беремо лише те, що після
    неї (до «Додано»/«Скасовано»). Якщо стрілки немає (перший запис дня),
    беремо все до ключового слова. Для кожної черги зберігаємо останній запис.
    """
    log_start = text.find("Оновлення графіків відключень за сьогодні")
    if log_start == -1:
        return {}, None
    log_text = text[log_start:]

    block_re = re.compile(r"(\d{2}:\d{2})ПАТ\s*«Запоріжжяобленерго»оновлено графік на сьогодні\s+")
    blocks = block_re.split(log_text)

    queues: dict[str, list[tuple[str, str]]] = {}
    last_time: str | None = None

    i = 1
    while i + 1 < len(blocks):
        upd_time = blocks[i]
        body     = blocks[i + 1]
        i += 2

        m = re.match(r"Черга\s+(\d\.\d):\s*(.+)", body, re.S)
        if not m:
            continue

        q    = m.group(1)
        rest = m.group(2)

        # Новий графік — після «→» (те, що перед нею, це старе расписание)
        arrow = rest.find("→")
        new_part = rest[arrow + 1:] if arrow != -1 else rest

        # Обрізаємо частку «Додано/Скасовано відключення: ...» — там теж є часи
        boundary = len(new_part)
        kw = re.search(r"(?i)(додано|скасовано)", new_part)
        if kw:
            boundary = kw.start()

        intervals = TIME_RE.findall(new_part[:boundary])
        if not intervals:
            continue

        # Для кожної черги перемагає останній (найсвіжіший) запис
        queues[q] = [(a, b) for a, b in intervals]
        last_time = upd_time

    return queues, last_time


async def fetch() -> Schedule | None:
    async with httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "uk-UA,uk;q=0.9",
        },
        follow_redirects=True,
        timeout=20,
    ) as client:
        resp = await client.get(URL)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text("\n")

    queues, updated = _parse_queues_from_log(text)
    if not queues:
        return None

    gav_warning = bool(re.search(r"аварійні відключення", text, re.I))

    now = datetime.now(TZ)
    return Schedule(
        title=title_for(now.day, now.month),
        day=now.day,
        month=now.month,
        updated=updated,
        queues=queues,
        gav_warning=gav_warning,
    )
