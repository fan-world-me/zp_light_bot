"""Фасад получения графика: svitlo.zp.ua -> OE_OUTAGE_DATA -> alerts.energy.

Приоритет источников:
1. svitlo.zp.ua (Google Sheets CSV) — есть сегодня И завтра, структурированный
   raw_data_json, точное время обновления с сайта Zoe. Доступен из любого региона.
2. OE_OUTAGE_DATA (GitHub raw JSON) — погодинные статусы, резерв.
3. alerts.energy (HTML) — крайний случай, только сегодня.

Результат кешируется (fetch_schedules), чтобы кнопки бота не дергали источники при каждом нажатии;
планировщик вызывает её с force=True.
"""
import asyncio
import logging
import time
from datetime import datetime

from app.parser.base import TZ, Schedule
from app.parser.providers import alerts_energy, oe_outage, svitlo_zp

log = logging.getLogger(__name__)

# Жорсткі ліміти на джерела — щоб кнопки в боті не «думали» по хвилині
_SVITLO_TIMEOUT = 15
_OE_TIMEOUT = 10
_ALERTS_TIMEOUT = 10

# Кеш результату: кнопки не мають щоразу качати обидві Google-таблиці
_CACHE_TTL = 45      # сек — свіжий кеш віддаємо одразу
_STALE_MAX = 900     # сек — якщо всі джерела впали, віддаємо старе до 15 хв
_cache: dict = {"ts": 0.0, "value": None}
_lock = asyncio.Lock()


async def _fetch_uncached() -> tuple[Schedule | None, Schedule | None]:
    """(на сегодня, на завтра). Бросает исключение, только если все источники упали."""
    # 1) svitlo.zp.ua — самый свежий, с завтрашним днём
    try:
        result = await asyncio.wait_for(svitlo_zp.fetch(), _SVITLO_TIMEOUT)
        if result and (result[0] or result[1]):
            return result
    except Exception as e:
        log.warning("svitlo.zp.ua failed: %s: %s", type(e).__name__, e)

    # 2) OE_OUTAGE_DATA
    try:
        result = await asyncio.wait_for(oe_outage.fetch(), _OE_TIMEOUT)
    except Exception as e:
        log.warning("OE_OUTAGE_DATA failed: %s: %s", type(e).__name__, e)
        result = None

    if result and (result[0] or result[1]):
        return result

    # 3) alerts.energy
    log.info("Falling back to alerts.energy")
    today = await asyncio.wait_for(alerts_energy.fetch(), _ALERTS_TIMEOUT)
    return today, None


async def fetch_schedules(force: bool = False) -> tuple[Schedule | None, Schedule | None]:
    """Кешована обгортка. force=True — завжди йти в джерела (для планувальника)."""
    def fresh() -> bool:
        return _cache["value"] is not None and time.monotonic() - _cache["ts"] < _CACHE_TTL

    if not force and fresh():
        return _cache["value"]

    # Хтось уже оновлює кеш, а в нас є хоч якісь дані — не чекаємо, віддаємо їх одразу
    if (
        not force
        and _lock.locked()
        and _cache["value"] is not None
        and time.monotonic() - _cache["ts"] < _STALE_MAX
    ):
        return _cache["value"]

    async with _lock:
        # поки чекали на lock, хтось інший міг уже оновити кеш
        if not force and fresh():
            return _cache["value"]
        try:
            value = await _fetch_uncached()
        except Exception:
            age = time.monotonic() - _cache["ts"]
            if not force and _cache["value"] is not None and age < _STALE_MAX:
                log.warning("All sources failed, serving stale cache (%.0fs old)", age)
                return _cache["value"]
            raise
        _cache["value"] = value
        _cache["ts"] = time.monotonic()
        return value


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
