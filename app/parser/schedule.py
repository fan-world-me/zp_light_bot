"""Фасад получения графика: svitlo.zp.ua -> OE_OUTAGE_DATA -> alerts.energy.

Приоритет источников:
1. svitlo.zp.ua (Google Sheets CSV) — есть сегодня И завтра, структурированный
   raw_data_json, точное время обновления с сайта Zoe. Доступен из любого региона.
2. OE_OUTAGE_DATA (GitHub raw JSON) — погодинные статусы, резерв.
3. alerts.energy (HTML) — крайний случай, только сегодня.

Интерфейс fetch_schedules() не изменился, поэтому notifier.py правок не требует.
"""
import logging
from datetime import datetime

from app.parser.base import TZ, Schedule
from app.parser.providers import alerts_energy, oe_outage, svitlo_zp

log = logging.getLogger(__name__)


async def fetch_schedules() -> tuple[Schedule | None, Schedule | None]:
    """(на сегодня, на завтра). Бросает исключение, только если все источники упали."""
    # 1) svitlo.zp.ua — самый свежий, с завтрашним днём
    try:
        result = await svitlo_zp.fetch()
        if result and (result[0] or result[1]):
            return result
    except Exception as e:
        log.warning("svitlo.zp.ua failed: %s: %s", type(e).__name__, e)

    # 2) OE_OUTAGE_DATA
    try:
        result = await oe_outage.fetch()
    except Exception as e:
        log.warning("OE_OUTAGE_DATA failed: %s: %s", type(e).__name__, e)
        result = None

    if result and (result[0] or result[1]):
        return result

    # 3) alerts.energy
    log.info("Falling back to alerts.energy")
    today = await alerts_energy.fetch()
    return today, None


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
