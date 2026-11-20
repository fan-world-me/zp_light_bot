"""Єдине джерело: CSV з Google Sheets проєкту svitlo.zp.ua.

svitlo.zp.ua — волонтерський моніторинг Запоріжжя. Свій пайплайн пише розклад
ПАТ «Запоріжжяобленерго» у дві публічні Google-таблиці (авто-парсер і
«напівавто» з перевіркою людини) — по одному рядку на день, включно із завтрашнім.

Таблиця містить історію за багато днів, тому качаємо не весь CSV, а лише рядки
на сьогодні/завтра через gviz-запит (відповідь ~5 КБ замість всієї історії).
Порядок джерел (наступне — лише якщо попереднє впало або не віддало рядків):
  1. авто-таблиця (gviz, лише потрібні дати);
  2. напівавто-таблиця (gviz, лише потрібні дати);
  3. Cloudflare Worker-проксі того ж сайту (повний CSV авто-таблиці).

Стовпці: date, time, date_information_update, source, raw_data, raw_data_json
raw_data_json: {"date":"15.11.2026","updated_time":"17:37",
                "site_updated_time":"17:35","source":"АТ «Запоріжжяобленерго»",
                "schedule":[{"subqueue":"2.1",
                             "intervals":[{"start":"15:30","end":"18:00"}, ...]}]}
"""
import csv
import io
import json
import logging
import re
import time
import urllib.parse
from datetime import datetime, timedelta

import aiohttp

from app.parser.base import TZ, Schedule, title_for

log = logging.getLogger(__name__)

SHEET_ID_AUTO      = "1EbK2ssChtIBRxu1FXXOEFcUJEP0mpXYBRT7LiScBOFk"
SHEET_ID_SEMI_AUTO = "1IPXo_cCgySsmSpEojsz04Fb_J8o8HeRKVtybDvphzG0"
GID                = "0"
WORKER_URL         = "https://svitlo-proxy.uaprof.workers.dev"

_USER_AGENT = "zp_light_bot/2.1 (+https://github.com/fan-world-me/zp_light_bot)"

_GROUP_LINE_RE = re.compile(r"(\d\.\d)\s*:")
_INTERVAL_RE   = re.compile(r"(\d{1,2}:\d{2})\s*[–\-]\s*(\d{1,2}:\d{2})", re.U)

# Один HTTP-клієнт на весь застосунок (а не новий на кожне оновлення)
_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            headers={"User-Agent": _USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=6, connect=3),
            connector=aiohttp.TCPConnector(limit=4),
        )
    return _session


async def close() -> None:
    """Закрити HTTP-клієнт (на shutdown)."""
    if _session is not None and not _session.closed:
        await _session.close()


def _gviz_url(sheet_id: str, dates: tuple[str, ...]) -> str:
    """CSV лише з рядками потрібних дат. Колонка A = date."""
    cond = " or ".join(f"A contains '{d}'" for d in dates)
    query = urllib.parse.quote(f"select * where {cond}")
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
        f"?tqx=out:csv&gid={GID}&headers=1&tq={query}&t={int(time.time())}"
    )


def _candidate_urls(dates: tuple[str, ...]) -> list[str]:
    return [
        _gviz_url(SHEET_ID_AUTO, dates),
        _gviz_url(SHEET_ID_SEMI_AUTO, dates),
        f"{WORKER_URL}?sheetId={SHEET_ID_AUTO}&gid={GID}",
    ]


def _hhmm(value: str | None) -> int:
    """'17:35' -> 1735, для порівняння свіжості рядків."""
    if not value:
        return -1
    m = re.match(r"(\d{1,2}):(\d{2})", str(value).strip())
    return int(m.group(1)) * 100 + int(m.group(2)) if m else -1


def _row_freshness(row: dict) -> int:
    return _hhmm(row.get("site_updated_time") or row.get("time"))


def _fetch_rows_from_csv(csv_text: str, wanted: set[str] | None = None) -> dict[str, dict]:
    """date (dd.mm.yyyy) -> найсвіжіший рядок дня.

    Словник будуємо лише для рядків із потрібною датою (дешевше за DictReader).
    """
    rows: dict[str, dict] = {}
    reader = csv.reader(io.StringIO(csv_text))
    header = next(reader, None)
    if not header:
        return rows
    header = [h.strip() for h in header]
    try:
        date_idx = header.index("date")
    except ValueError:
        return rows

    for cells in reader:
        if date_idx >= len(cells):
            continue
        d = cells[date_idx].strip()
        if not d or (wanted is not None and d not in wanted):
            continue
        row = dict(zip(header, cells))
        old = rows.get(d)
        if old is None or _row_freshness(row) >= _row_freshness(old):
            rows[d] = row
    return rows


def _parse_raw_json(raw: str) -> dict[str, list[tuple[str, str]]] | None:
    """raw_data_json -> {"1.1": [("02:30","06:00"), ...]} або None."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    schedule = payload.get("schedule")
    if not isinstance(schedule, list):
        return None
    queues: dict[str, list[tuple[str, str]]] = {}
    for item in schedule:
        q = str(item.get("subqueue", "")).strip()
        if not q:
            continue
        intervals = []
        for iv in item.get("intervals") or []:
            s, e = iv.get("start"), iv.get("end")
            if s and e:
                intervals.append((s.strip(), e.strip()))
        queues[q] = intervals
    return queues


def _parse_raw_text(raw: str) -> dict[str, list[tuple[str, str]]]:
    """Фолбэк: текст '1.1: 02:30 – 06:00, 11:30 – 16:00 1.2: ...'."""
    queues: dict[str, list[tuple[str, str]]] = {}
    parts = re.split(r"(?=(?:\d\.\d)\s*:)", raw)
    for part in parts:
        m = _GROUP_LINE_RE.match(part.strip())
        if not m:
            continue
        q = m.group(1)
        intervals = [(a.strip(), b.strip()) for a, b in _INTERVAL_RE.findall(part)]
        queues[q] = intervals
    return queues


def _update_label(row: dict) -> str | None:
    for key in ("site_updated_time", "time"):
        value = str(row.get(key) or "").strip()
        if _hhmm(value) >= 0:
            return value
    return None


def _schedule_from_row(row: dict, date_str: str, allow_empty: bool = True) -> Schedule | None:
    try:
        d = datetime.strptime(date_str, "%d.%m.%Y").date()
    except ValueError:
        return None

    raw_json = row.get("raw_data_json") or ""
    queues = _parse_raw_json(raw_json) if raw_json.strip() else None
    if queues is None:
        queues = _parse_raw_text(row.get("raw_data") or "")

    if not queues and not (row.get("raw_data") or "").strip():
        if not allow_empty:
            # Для «завтра» порожній рядок = графік ще не опублікований, а не «відключень нема»
            return None
        # Рядок на сьогодні є, даних нема — трактуємо як "без відключень"
        queues = {f"{g}.{s}": [] for g in range(1, 7) for s in (1, 2)}
    if not queues:
        return None

    return Schedule(
        title=title_for(d.day, d.month),
        day=d.day,
        month=d.month,
        updated=_update_label(row),
        queues=queues,
    )


def _wanted_dates() -> tuple[str, str]:
    now = datetime.now(TZ).date()
    return (
        now.strftime("%d.%m.%Y"),
        (now + timedelta(days=1)).strftime("%d.%m.%Y"),
    )


def _rows_to_schedules(rows: dict[str, dict]) -> tuple[Schedule | None, Schedule | None]:
    today_str, tomorrow_str = _wanted_dates()
    today = (
        _schedule_from_row(rows[today_str], today_str, allow_empty=True)
        if today_str in rows else None
    )
    tomorrow = (
        _schedule_from_row(rows[tomorrow_str], tomorrow_str, allow_empty=False)
        if tomorrow_str in rows else None
    )
    return today, tomorrow


async def _get_csv(url: str) -> str:
    async with _get_session().get(url) as resp:
        resp.raise_for_status()
        return await resp.text(encoding="utf-8")


async def fetch() -> tuple[Schedule | None, Schedule | None]:
    """(на сьогодні, на завтра). Джерела пробуються по черзі, до першого з рядками."""
    dates = _wanted_dates()
    wanted = set(dates)
    for url in _candidate_urls(dates):
        host = url.split("/")[2]
        try:
            text = await _get_csv(url)
            rows = _fetch_rows_from_csv(text, wanted)
            del text
        except Exception as e:
            log.warning("svitlo %s failed: %s: %s", host, type(e).__name__, e)
            continue
        if rows:
            return _rows_to_schedules(rows)
        log.info("svitlo %s: no rows for %s", host, ", ".join(dates))
    return None, None
