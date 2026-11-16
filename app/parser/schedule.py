import re
import httpx
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

URL = "https://alerts.energy/zaporizka-oblast"
TZ  = ZoneInfo("Europe/Kiev")

TIME_RE  = re.compile(r"(\d{2}:\d{2})[–\-](\d{2}:\d{2})")

# Matches update log entries like:
# "16:51ПАТ «Запоріжжяобленерго»оновлено графік на сьогодні Черга 1.1: 02:30-06:00, ..."
# We want the LAST entry per queue (most recent), so we collect all and keep last.
ENTRY_RE = re.compile(
    r"(\d{2}:\d{2})"           # time of update e.g. "16:51"
    r"ПАТ\s*«Запоріжжяобленерго»"
    r"оновлено графік на сьогодні\s+"
    r"Черга\s+(\d\.\d):\s*"    # queue id
    r"([^\n]+?)"               # old schedule (before the new one)
    r"(\d{2}:\d{2}[–\-\d:,\s]+)"  # new schedule (after old)
    r"(?:Додано|Скасовано|$)",
    re.I,
)

# Simpler per-queue extractor from the update log
QUEUE_ENTRY_RE = re.compile(
    r"Черга\s+(\d\.\d):\s*([\d:,\s\-–]+)"
)


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


def _parse_queues_from_log(text: str) -> tuple[dict[str, list[tuple[str, str]]], str | None]:
    """
    Parse the update log section of alerts.energy.
    The log has entries like:
      16:51ПАТ «Запоріжжяобленерго»оновлено графік на сьогодні Черга 1.1: OLD_SCHEDULEnew_schedule...
    The NEW schedule is the second occurrence of times after the queue id.
    We keep only the last (most recent) entry per queue.
    """
    # Find the update log section
    log_start = text.find("Оновлення графіків відключень за сьогодні")
    if log_start == -1:
        return {}, None
    log_text = text[log_start:]

    # Split by update timestamp pattern "HH:MM ПАТ «Запоріжжяобленерго»"
    block_re = re.compile(r"(\d{2}:\d{2})ПАТ\s*«Запоріжжяобленерго»оновлено графік на сьогодні\s+")
    blocks = block_re.split(log_text)

    # blocks: [prefix, time1, body1, time2, body2, ...]
    queues: dict[str, list[tuple[str, str]]] = {}
    last_time: str | None = None

    i = 1
    while i + 1 < len(blocks):
        upd_time = blocks[i]
        body     = blocks[i + 1]
        i += 2

        # Find "Черга X.X: ..." in body
        m = re.match(r"Черга\s+(\d\.\d):\s*(.+)", body)
        if not m:
            continue

        q        = m.group(1)
        rest     = m.group(2)

        # The body contains: OLD_SCHEDULE NEW_SCHEDULE Додано/Скасовано...
        # Find all time intervals — the LAST full set before "Додано" or "Скасовано"
        # New schedule is the second group of intervals
        all_intervals = list(re.finditer(r"(\d{2}:\d{2})[–\-](\d{2}:\d{2})", rest))

        if not all_intervals:
            continue

        # Find split point — after first "sequence" ends (gap > 10 chars between matches)
        # The new schedule starts after the old schedule text ends
        # Heuristic: find comma-separated time groups; second group = new schedule
        # Actually alerts.energy format: "OLD_SCHEDULEnew_scheduleДодано..."
        # OLD ends where times stop being adjacent; NEW starts immediately after
        # Simpler: just take all intervals and use them as the new schedule
        # (the new schedule is always the last listed set)

        # Find "Додано" or "Скасовано" boundary
        boundary = len(rest)
        for kw in ["Додано", "Скасовано", "скасовано", "додано"]:
            pos = rest.find(kw)
            if pos != -1 and pos < boundary:
                boundary = pos

        schedule_text = rest[:boundary]
        intervals = TIME_RE.findall(schedule_text)

        if not intervals:
            continue

        # For each queue keep only the most recent update (last block wins)
        queues[q] = [(a, b) for a, b in intervals]
        last_time = upd_time

    return queues, last_time


async def fetch_schedules() -> tuple["Schedule | None", "Schedule | None"]:
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

    now     = datetime.now(TZ)
    today_d = now.day
    today_m = now.month

    queues, updated = _parse_queues_from_log(text)

    if not queues:
        return None, None

    gav_warning = bool(re.search(r"аварійні відключення", text, re.I))

    MONTH_NAMES = {
        1: "січня", 2: "лютого", 3: "березня", 4: "квітня",
        5: "травня", 6: "червня", 7: "липня", 8: "серпня",
        9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня",
    }

    today_sched = Schedule(
        title=f"Графік відключень на {today_d} {MONTH_NAMES[today_m]}",
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
        return f"📋 <b>{schedule.title}</b>\n\n😕 Вибрані черги не знайдено в графіку."

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
