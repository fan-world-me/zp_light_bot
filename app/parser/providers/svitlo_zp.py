"""Первинне джерело: CSV з Google Sheets проєкту svitlo.zp.ua.

svitlo.zp.ua — волонтерський моніторинг Запоріжжя. Свій пайплайн пише розклад
ПАТ «Запоріжжяобленерго» у дві публічні Google-таблиці (авто-парсер і
«напівавто» з перевіркою людини) — по одному рядку на день, включно ізtomorrow.
CSV публічний (export?format=csv), доступний з будь-якого регіону; на випадок
блоків є Cloudflare Worker-проксі того ж сайту.

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
from datetime import datetime, timedelta

import httpx

from app.parser.base import TZ, Schedule, title_for

log = logging.getLogger(__name__)

SHEET_ID_AUTO      = "1EbK2ssChtIBRxu1FXXOEFcUJEP0mpXYBRT7LiScBOFk"
SHEET_ID_SEMI_AUTO = "1IPXo_cCgySsmSpEojsz04Fb_J8o8HeRKVtybDvphzG0"
GID                = "0"
WORKER_URL         = "https://svitlo-proxy.uaprof.workers.dev"

_GROUP_LINE_RE = re.compile(r"(\d\.\d)\s*:")
_INTERVAL_RE   = re.compile(r"(\d{1,2}:\d{2})\s*[–\-]\s*(\d{1,2}:\d{2})", re.U)


def _csv_urls(sheet_id: str) -> list[str]:
    ts = int(datetime.now().timestamp() * 1000)
    google = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/export?format=csv&gid={GID}&t={ts}"
    )
    return [google, f"{WORKER_URL}?sheetId={sheet_id}&gid={GID}"]


def _hhmm(value: str | None) -> int:
    """'17:35' -> 1735, для порівняння свіжості рядків."""
    if not value:
        return -1
    m = re.match(r"(\d{1,2}):(\d{2})", str(value).strip())
    return int(m.group(1)) * 100 + int(m.group(2)) if m else -1


def _row_freshness(row: dict) -> int:
    return _hhmm(row.get("site_updated_time") or row.get("time"))


def _fetch_rows_from_csv(csv_text: str) -> dict[str, dict]:
    """date (dd.mm.yyyy) -> найсвіжіший рядок дня."""
    rows: dict[str, dict] = {}
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        d = (row.get("date") or "").strip()
        if not d:
            continue
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


def _schedule_from_row(row: dict, date_str: str) -> Schedule | None:
    try:
        d = datetime.strptime(date_str, "%d.%m.%Y").date()
    except ValueError:
        return None

    raw_json = row.get("raw_data_json") or ""
    queues = _parse_raw_json(raw_json) if raw_json.strip() else None
    if queues is None:
        queues = _parse_raw_text(row.get("raw_data") or "")

    if not queues and not (row.get("raw_data") or "").strip():
        # Рядок на день є, даних нема — трактуємо як "без відключень"
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


def _rows_to_schedules(rows: dict[str, dict]) -> tuple[Schedule | None, Schedule | None]:
    now = datetime.now(TZ).date()
    return (
        _schedule_from_row(rows[now.strftime("%d.%m.%Y")], now.strftime("%d.%m.%Y"))
        if now.strftime("%d.%m.%Y") in rows
        else None,
        _schedule_from_row(
            rows[(now + timedelta(days=1)).strftime("%d.%m.%Y")],
            (now + timedelta(days=1)).strftime("%d.%m.%Y"),
        )
        if (now + timedelta(days=1)).strftime("%d.%m.%Y") in rows
        else None,
    )


async def _fetch_sheet(client: httpx.AsyncClient, sheet_id: str) -> str:
    last_exc: Exception | None = None
    for url in _csv_urls(sheet_id):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            last_exc = e
            log.warning("svitlo sheet %s via %s failed: %s: %s",
                        sheet_id[:8], url.split("/")[2], type(e).__name__, e)
    raise last_exc or RuntimeError("sheet fetch failed")


async def fetch() -> tuple[Schedule | None, Schedule | None]:
    """(на сьогодні, на завтра). Кращий рядок із двох таблиць за свіжістю."""
    async with httpx.AsyncClient(
        headers={"User-Agent": "zp_light_bot/2.0 (+https://github.com/fan-world-me/zp_light_bot)"},
        follow_redirects=True,
        timeout=20,
    ) as client:
        results = await _gather_two(client)

    rows: dict[str, dict] = {}
    for csv_text in results:
        if not csv_text:
            continue
        for d, row in _fetch_rows_from_csv(csv_text).items():
            old = rows.get(d)
            if old is None or _row_freshness(row) >= _row_freshness(old):
                rows[d] = row

    if not rows:
        return None, None
    return _rows_to_schedules(rows)


async def _gather_two(client: httpx.AsyncClient) -> list[str | None]:
    import asyncio

    async def safe(sheet_id: str) -> str | None:
        try:
            return await _fetch_sheet(client, sheet_id)
        except Exception as e:
            log.warning("svitlo sheet %s unavailable: %s", sheet_id[:8], e)
            return None

    auto, semi = await asyncio.gather(safe(SHEET_ID_AUTO), safe(SHEET_ID_SEMI_AUTO))
    return [auto, semi]
