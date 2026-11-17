"""Первинне джерело даних: OE_OUTAGE_DATA (github.com/yaroslav2901/OE_OUTAGE_DATA).

Проєкт парсить zoe.com.ua кожні ~5 хв через GitHub Actions і комітить JSON з
погодинним статусом світла для кожної черги. raw.githubusercontent.com — це
глобальний CDN GitHub: доступний з будь-якого регіону (на відміну від
зое.com.ua, який віддає сторінку лише з українських IP), без ключів і лімітів.

Формат JSON (Запоріжжя):
{
  "lastUpdated": "2026-11-16T14:34:37.760Z",
  "fact": {
    "data": {
      "1794780000": {                     // ключ — північ Europe/Kyiv (Unix)
        "GPV1.1": {"1": "yes", "2": "no", "3": "second", ...},
        ...
      }
    },
    "update": "16.11.2026 16:27",         // час оновлення джерела
    "today": 1794780000
  }
}

Ключі годин 1..24 відповідають інтервалам 00:00-01:00 ... 23:00-24:00 (Kyiv).
Значення: yes | no | first (перші 30 хв) | second (другі 30 хв).
"maybe" (можливе відключення) у Запоріжжя поки не зустрічається; трактуємо
його як "світло є", щоб не лякати користувачів гіпотетичними відключеннями.
"""
import re

import httpx

from datetime import datetime, timedelta

from app.parser.base import TZ, Schedule, title_for, minutes_to_hm

URL = (
    "https://raw.githubusercontent.com/yaroslav2901/OE_OUTAGE_DATA/"
    "main/data/Zaporizhzhiaoblenergo.json"
)

# Зсув (в хвилинах) інтервалу відключення всередині години
_SEGMENTS = {
    "no":     (0, 60),   # світла немає весь час
    "first":  (0, 30),   # світла не буде перші 30 хв
    "second": (30, 60),  # світла не буде другі 30 хв
}

_UPDATE_TIME_RE = re.compile(r"(\d{2}:\d{2})")


def _hours_to_intervals(hours: dict) -> list[tuple[str, str]]:
    """{"3": "second", "4": "no", ...} -> [("02:30", "03:00"), ...] злиті інтервали."""
    intervals: list[tuple[int, int]] = []
    start: int | None = None
    end = 0
    for h in range(1, 25):
        seg = _SEGMENTS.get(hours.get(str(h)))
        if seg is None:  # "yes", "maybe" або дані немає
            if start is not None:
                intervals.append((start, end))
                start = None
            continue
        s = (h - 1) * 60 + seg[0]
        e = (h - 1) * 60 + seg[1]
        if start is None:
            start, end = s, e
        elif s == end:  # інтервали йдуть впритул — зливаємо
            end = e
        else:
            intervals.append((start, end))
            start, end = s, e
    if start is not None:
        intervals.append((start, end))
    return [(minutes_to_hm(a), minutes_to_hm(b)) for a, b in intervals]


def _pick_days(data: dict) -> tuple[int | None, int | None]:
    """Вибрати ключі "сьогодні" та "завтра" з fact.data (Unix -> північ Kyiv).

    Беремо рівно сьогоднішню та завтрашню дату. Якщо запису на сьогодні нема —
    повертаємо None, а не підставляємо найближчий майбутній день як «сьогодні».
    """
    today_date = datetime.now(TZ).date()
    tomorrow_date = today_date + timedelta(days=1)
    today: int | None = None
    tomorrow: int | None = None
    for k in sorted(data, key=lambda x: str(x)):
        try:
            ts = int(k)
        except (TypeError, ValueError):
            continue
        d = datetime.fromtimestamp(ts, TZ).date()
        if d == today_date and today is None:
            today = ts
        elif d == tomorrow_date and tomorrow is None:
            tomorrow = ts
    return today, tomorrow


def _updated_label(fact: dict, payload: dict) -> str | None:
    """Час оновлення для повідомлень ("16:27")."""
    raw = fact.get("update")
    if isinstance(raw, str) and raw.strip():
        m = _UPDATE_TIME_RE.search(raw)
        return m.group(1) if m else raw.strip()
    last = payload.get("lastUpdated")
    return last if isinstance(last, str) else None


def _schedule_for(ts: int, groups: dict, updated: str | None) -> Schedule | None:
    d = datetime.fromtimestamp(ts, TZ)
    queues: dict[str, list[tuple[str, str]]] = {}
    for name, hours in groups.items():
        if not isinstance(hours, dict):
            continue
        queues[name.removeprefix("GPV")] = _hours_to_intervals(hours)  # "GPV1.1" -> "1.1"
    if not queues:
        return None
    return Schedule(
        title=title_for(d.day, d.month),
        day=d.day,
        month=d.month,
        updated=updated,
        queues=queues,
        gav_warning=False,  # у JSON OE_OUTAGE_DATA ознаки ГАВ немає
    )


async def fetch() -> tuple[Schedule | None, Schedule | None]:
    """(графік на сьогодні, графік на завтра). None — даних немає."""
    async with httpx.AsyncClient(
        headers={"User-Agent": "zp_light_bot/2.0 (+https://github.com/fan-world-me/zp_light_bot)"},
        timeout=httpx.Timeout(8.0, connect=4.0),
    ) as client:
        resp = await client.get(URL)
        resp.raise_for_status()
        payload = resp.json()

    fact = payload.get("fact") or {}
    data = fact.get("data") or {}
    if not isinstance(data, dict) or not data:
        return None, None

    updated = _updated_label(fact, payload)
    today_ts, tomorrow_ts = _pick_days(data)
    if today_ts is None and tomorrow_ts is None:
        return None, None

    today = (
        _schedule_for(today_ts, data.get(str(today_ts)) or {}, updated)
        if today_ts is not None
        else None
    )
    tomorrow = (
        _schedule_for(tomorrow_ts, data.get(str(tomorrow_ts)) or {}, updated)
        if tomorrow_ts is not None
        else None
    )
    return today, tomorrow
