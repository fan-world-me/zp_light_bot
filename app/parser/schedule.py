import re
import httpx
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

URL = "https://alerts.energy/zaporizka-oblast"
TZ  = ZoneInfo("Europe/Kiev")

MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4,
    "травня": 5, "червня": 6, "липня": 7, "серпня": 8,
    "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
}

# Matches lines like "Черга 1.1: 02:30-06:00, 11:30-16:30"
QUEUE_RE = re.compile(r"Черга\s+(\d\.\d):\s*(.+)")
TIME_RE  = re.compile(r"(\d{2}:\d{2})[–\-](\d{2}:\d{2})")
DATE_RE  = re.compile(
    r"(\d{1,2})[.\s]+(" + "|".join(MONTHS) + r")",
    re.I | re.U,
)
UPD_RE   = re.compile(r"(\d{2}:\d{2})\s*ПАТ\s*«Запоріжжяобленерго»\s*оновлено", re.I)


@dataclass
class Schedule:
    title:       str
    day:         int | None
    month:       int | None
    updated:     str | None
    queues:      dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    gav_warning: bool = False


def _to_min(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


async def fetch_schedules() -> tuple["Schedule | None", "Schedule | None"]:
    """Return (today_schedule, tomorrow_schedule). Either can be None."""
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

    now        = datetime.now(TZ)
    today_d    = now.day
    today_m    = now.month
    tomorrow   = now + timedelta(days=1)
    tomorrow_d = tomorrow.day
    tomorrow_m = tomorrow.month

    # Parse queues from "Черга X.X: HH:MM-HH:MM, ..." lines
    queues: dict[str, list[tuple[str, str]]] = {}
    for m in QUEUE_RE.finditer(text):
        q    = m.group(1)
        rest = m.group(2)
        times = TIME_RE.findall(rest)
        if times and q not in queues:
            queues[q] = [(a, b) for a, b in times]

    if not queues:
        return None, None

    # Last updated time from the update log
    upd_match = UPD_RE.search(text)
    updated   = upd_match.group(1) if upd_match else None

    gav_warning = bool(re.search(r"ГАВ|СГАВ|аварійні відключення", text, re.I))

    today_sched = Schedule(
        title=f"Графік відключень на {today_d} {'листопада' if today_m == 11 else today_m}",
        day=today_d,
        month=today_m,
        updated=updated,
        queues=queues,
        gav_warning=gav_warning,
    )

    return today_sched, None


def is_off_now(intervals: list[tuple[str, str]]) -> bool:
    now = datetime.now(TZ)
    cur = now.hour * 60 + now.minute
    return any(_to_min(a) <= cur < _to_min(b) for a, b in intervals)


def format_schedule(schedule: "Schedule", queue_ids: list[str] | None = None) -> str:
    queues = (
        {k: v for k, v in schedule.queues.items() if k in queue_ids}
        if queue_ids else schedule.queues
    )

    if not queues:
        return f"📋 <b>{schedule.title}</b>\n\n😕 Черги не знайдено в графіку."

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
        header += "\n\n⚠️ Діють аварійні відключення — можливі вимкнення поза графіком"

    return header + "\n\n" + "\n\n".join(lines)
