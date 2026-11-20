"""Фасад отримання графіка. Єдине джерело — svitlo.zp.ua (Google Sheets, див. providers/svitlo_zp.py).

Кеш у стилі stale-while-revalidate: хендлери бота не чекають на мережу, якщо в кеші
вже є дані. Оновлення йде у фоні (одне на всіх), а якщо дані змінилися — хендлер
надсилає користувачу нове повідомлення. Планувальник викликає
fetch_schedules(force=True) і чекає на свіжі дані.
"""
import asyncio
import logging
import time
from datetime import datetime

from app.parser.base import TZ, Schedule
from app.parser.providers import svitlo_zp

log = logging.getLogger(__name__)

Data = tuple[Schedule | None, Schedule | None]

_FETCH_TIMEOUT = 20   # сек — загальний ліміт одного оновлення (усі джерела разом)
_CACHE_TTL = 60       # сек — свіжий кеш віддаємо без оновлення
_STALE_MAX = 900      # сек — старіші за 15 хв дані вже не показуємо
_RETRY_BACKOFF = 30   # сек — пауза після невдалого фонового оновлення

_cache: dict = {"ts": 0.0, "value": None}
_lock = asyncio.Lock()
_bg: asyncio.Task | None = None
_next_try = 0.0


async def _refresh() -> bool:
    """Одне оновлення кешу (під lock). True — кеш свіжий, False — не вдалося."""
    global _next_try
    started = time.monotonic()
    async with _lock:
        if _cache["ts"] > started:
            return True  # поки чекали на lock, хтось інший уже оновив
        try:
            value = await asyncio.wait_for(svitlo_zp.fetch(), _FETCH_TIMEOUT)
        except Exception as e:
            log.warning("Refresh failed: %s: %s", type(e).__name__, e)
            _next_try = time.monotonic() + _RETRY_BACKOFF
            return False
        if not value or not (value[0] or value[1]):
            log.warning("Refresh: no schedule rows")
            _next_try = time.monotonic() + _RETRY_BACKOFF
            return False
        _cache["value"] = value
        _cache["ts"] = time.monotonic()
        _next_try = 0.0
        return True


def start_refresh() -> asyncio.Task | None:
    """Запустити фонове оновлення (одне на всіх). Повертає задачу (результат — bool).

    None — якщо щойно була невдала спроба і джерела ще «відпочивають».
    """
    global _bg
    if _bg is not None and not _bg.done():
        return _bg
    if time.monotonic() < _next_try:
        return None
    _bg = asyncio.create_task(_refresh())
    return _bg


def peek() -> tuple[Data | None, bool]:
    """(дані з кешу, чи_застарілі). Дані None — якщо кешу нема або він надто старий."""
    value = _cache["value"]
    if value is None:
        return None, True
    age = time.monotonic() - _cache["ts"]
    if age >= _STALE_MAX:
        return None, True
    return value, age >= _CACHE_TTL


async def fetch_schedules(force: bool = False) -> Data:
    """Дані, що чекають на мережу лише за потреби.

    force=True — завжди оновити (для планувальника), кидає виняток при невдачі.
    force=False — свіжий кеш віддає одразу; якщо оновити не вдалося, а старий кеш
    ще придатний — віддає його.
    """
    if not force:
        value, stale = peek()
        if value is not None and not stale:
            return value

    ok = await _refresh()
    if not ok:
        value, _ = peek()
        if value is not None and not force:
            log.warning("Sources failed, serving stale cache")
            return value
        raise RuntimeError("schedule sources unavailable")
    return _cache["value"]


async def close() -> None:
    """Звільнити мережеві ресурси (на shutdown)."""
    await svitlo_zp.close()


def _to_min(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


def is_off_now(intervals: list[tuple[str, str]]) -> bool:
    now = datetime.now(TZ)
    cur = now.hour * 60 + now.minute
    for a, b in intervals:
        start, end = _to_min(a), _to_min(b)
        if start <= end:              # обычный интервал, напр. 02:30-06:00
            if start <= cur < end:
                return True
        else:                          # через полночь, напр. 23:00-06:00
            if cur >= start or cur < end:
                return True
    return False


def format_schedule(schedule: Schedule, queue_ids: list[str] | None = None) -> str:
    queues = (
        {k: v for k, v in schedule.queues.items() if k in queue_ids}
        if queue_ids else schedule.queues
    )

    if not queues:
        return f"📋 <b>{schedule.title}</b>\n\n😕 Вибрані черги не знайдено в графіку."

    # «Зараз» має сенс лише для сьогоднішнього графіка
    now = datetime.now(TZ)
    is_today = (schedule.day, schedule.month) == (now.day, now.month)

    lines = []
    for q in sorted(queues):
        intervals = queues[q]
        if intervals:
            times = ", ".join(f"{a}–{b}" for a, b in intervals)
        else:
            times = "відключень не заплановано"
        if is_today:
            off = is_off_now(intervals)
            icon = "🔴" if off else "🟢"
            status = "немає світла" if off else "є світло"
            lines.append(f"{icon} Черга <b>{q}</b>: {times}\n   <i>зараз: {status}</i>")
        else:
            lines.append(f"⏰ Черга <b>{q}</b>: {times}")

    header = f"📋 <b>{schedule.title}</b>"
    if schedule.updated:
        header += f"\n🕐 Оновлено: {schedule.updated}"
    if schedule.gav_warning:
        header += "\n\n⚠️ Діють аварійні відключення — можливі вимкнення поза графіком"

    return header + "\n\n" + "\n\n".join(lines)
