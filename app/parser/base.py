"""Спільна модель графіка відключень для всіх провайдерів."""
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Kyiv")

MONTH_NAMES = {
    1: "січня", 2: "лютого", 3: "березня", 4: "квітня",
    5: "травня", 6: "червня", 7: "липня", 8: "серпня",
    9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня",
}


@dataclass
class Schedule:
    title:       str
    day:         int | None
    month:       int | None
    updated:     str | None
    queues:      dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    gav_warning: bool = False


def title_for(day: int, month: int) -> str:
    return f"Графік відключень на {day} {MONTH_NAMES[month]}"


def minutes_to_hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
