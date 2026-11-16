import re
import httpx
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

URL = "https://www.zoe.com.ua/outage/"
TZ  = ZoneInfo("Europe/Kiev")

MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4,
    "травня": 5, "червня": 6, "липня": 7, "серпня": 8,
    "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
}

QUEUE_RE = re.compile(r"^\s*(\d\.\d):\s*(.+)$", re.M)
TIME_RE  = re.compile(r"(\d{2}:\d{2})\s*[–\-]\s*(\d{2}:\d{2})")
DATE_RE  = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS) + ")", re.I)
UPD_RE   = re.compile(r"оновлено\s+(\d{2}:\d{2})", re.I)


@dataclass
class Schedule:
    title:      str
    day:        int | None
    month:      int | None
    updated:    str | None
    queues:     dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    gav_warning: bool = False


def _to_min(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _parse_article(art) -> Schedule | None:
    content = art.select_one("div.content") or art
    text = content.get_text("\n")

    queues: dict[str, list[tuple[str, str]]] = {}
    for q, rest in QUEUE_RE.findall(text):
        times = TIME_RE.findall(rest)
        if times and q not in queues:
            queues[q] = [(a, b) for a, b in times]

    if not queues:
        return None

    h2 = art.find("h2")
    title = h2.get_text(" ", strip=True) if h2 else ""
    blob  = title + "\n" + text

    d = DATE_RE.search(blob)
    u = UPD_RE.search(blob)

    return Schedule(
        title=title,
        day=int(d[1]) if d else None,
        month=MONTHS[d[2].lower()] if d else None,
        updated=u[1] if u else None,
        queues=queues,
        gav_warning="ГАВ" in text,
    )


async def fetch_schedules() -> tuple[Schedule | None, Schedule | None]:
    """Return (today_schedule, tomorrow_schedule). Either can be None."""
    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (compatible; zp-light-bot/1.0)"},
        follow_redirects=True,
        timeout=15,
    ) as client:
        resp = await client.get(URL)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    now       = datetime.now(TZ)
    today_d   = now.day
    today_m   = now.month
    from datetime import timedelta
    tomorrow  = now + timedelta(days=1)
    tomorrow_d = tomorrow.day
    tomorrow_m = tomorrow.month

    today_sched:    Schedule | None = None
    tomorrow_sched: Schedule | None = None

    for art in soup.select("article"):
        parsed = _parse_article(art)
        if not parsed:
            continue
        if today_sched is None and parsed.day == today_d and parsed.month == today_m:
            today_sched = parsed
        if tomorrow_sched is None and parsed.day == tomorrow_d and parsed.month == tomorrow_m:
            tomorrow_sched = parsed
        if today_sched and tomorrow_sched:
            break

    return today_sched, tomorrow_sched


def is_off_now(intervals: list[tuple[str, str]]) -> bool:
    now = datetime.now(TZ)
    cur = now.hour * 60 + now.minute
    return any(_to_min(a) <= cur < _to_min(b) for a, b in intervals)


def format_schedule(schedule: Schedule, queue_ids: list[str] | None = None) -> str:
    queues = (
        {k: v for k, v in schedule.queues.items() if k in queue_ids}
        if queue_ids else schedule.queues
    )

    lines = []
    for q in sorted(queues):
        intervals = queues[q]
        off    = is_off_now(intervals)
        icon   = "🔴" if off else "🟢"
        status = "немає світла" if off else "є світло"
        times  = ", ".join(f"{a}–{b}" for a, b in intervals)
        lines.append(f"{icon} Черга <b>{q}</b>: {times}\n   <i>зараз: {status}</i>")

    header = f"📋 <b>{schedule.title}</b>"
    if schedule.updated:
        header += f"\n🕐 Оновлено: {schedule.updated}"
    if schedule.gav_warning:
        header += "\n\n⚠️ Діють ГАВ/СГАВ — можливі відключення поза графіком"

    return header + "\n\n" + "\n\n".join(lines)
