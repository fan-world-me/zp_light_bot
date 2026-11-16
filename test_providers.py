"""Sanity-check нового стека провайдерів (без токена бота).

Запуск:  python test_providers.py
"""
import asyncio
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.parser.providers import alerts_energy, oe_outage, svitlo_zp
from app.parser.schedule import fetch_schedules, format_schedule, is_off_now

INTERVAL_RE = re.compile(r"^\d{2}:\d{2}–\d{2}:\d{2}(, \d{2}:\d{2}–\d{2}:\d{2})*$")


async def main() -> None:
    # 1) Fallback-парсер alerts.energy на синтетичному тексті зі стрілкою «→»
    sample = (
        "Оновлення графіків відключень за сьогодні\n"
        "16:51ПАТ «Запоріжжяобленерго»оновлено графік на сьогодні Черга 1.1: "
        "02:30-06:00, 11:30-16:30→02:30-06:00, 11:30-16:30, 20:30-24:00"
        "Додано відключення: 20:30-24:00\n"
    )
    queues, updated = alerts_energy._parse_queues_from_log(sample)
    assert queues == {
        "1.1": [("02:30", "06:00"), ("11:30", "16:30"), ("20:30", "24:00")]
    }, f"alerts fallback parse failed: {queues}"
    assert updated == "16:51"
    print("[1] alerts.energy fallback: OK (старий графік не домішується, -> розділяє)")

    # 2) Живий svitlo.zp.ua (Google Sheets CSV)
    s_today, s_tomorrow = await svitlo_zp.fetch()
    assert s_today is not None, "svitlo returned nothing for today"
    assert set(s_today.queues) >= {"1.1", "6.2"}, f"queues missing: {sorted(s_today.queues)}"
    print(f"[2] svitlo.zp.ua live: today OK (updated={s_today.updated}, "
          f"tomorrow={'yes' if s_tomorrow else 'not yet'})")
    print("    Черга 1.1:", ", ".join(f"{a}–{b}" for a, b in s_today.queues["1.1"]) or "—")
    if s_tomorrow:
        print("    Завтра 1.1:", ", ".join(f"{a}–{b}" for a, b in s_tomorrow.queues["1.1"]) or "відключень немає")
        assert s_tomorrow.day != s_today.day

    # 3) Живий OE_OUTAGE_DATA (резерв №2)
    today, tomorrow = await oe_outage.fetch()
    assert today is not None, "OE provider returned nothing"
    joined = ", ".join(f"{a}–{b}" for a, b in today.queues["1.1"])
    assert INTERVAL_RE.match(joined), f"bad intervals: {joined}"
    print(f"[3] OE_OUTAGE_DATA live: OK (updated={today.updated})")
    print("    Черга 1.1:", joined)
    if s_today:
        assert today.queues["1.1"] == s_today.queues["1.1"], (
            "sources disagree for 1.1!\n"
            f"  svitlo: {s_today.queues['1.1']}\n"
            f"  oe:     {today.queues['1.1']}"
        )
        print("    согласовано между источниками: OK")

    # 4) Фасад — пріоритет svitlo -> OE -> alerts
    f_today, f_tomorrow = await fetch_schedules()
    assert f_today is not None
    print(f"[4] Facade: OK -> {f_today.title}; завтра: "
          f"{'є' if f_tomorrow else 'ще немає'}")

    # 5) Формат: сьогодні - з «зараз», завтра - без
    out_today = format_schedule(f_today, ["1.1"])
    assert "зараз:" in out_today
    if f_tomorrow:
        out_tom = format_schedule(f_tomorrow, ["1.1"])
        assert "зараз:" not in out_tom, "tomorrow must not show 'зараз' status"
        print("[5] Format: today з «зараз», tomorrow без — OK")
    else:
        print("[5] Format: today з «зараз» — OK (tomorrow ще не опублікований)")

    # 6) Нічні інтервали в is_off_now (23:00-06:00 через північ)
    from datetime import datetime
    from app.parser.base import TZ
    cur = datetime.now(TZ)
    cur_min = cur.hour * 60 + cur.minute
    expected = cur_min >= 23 * 60 or cur_min < 6 * 60
    assert is_off_now([("23:00", "06:00")]) == expected
    assert is_off_now([]) is False
    assert is_off_now([("00:00", "24:00")]) is True
    print("[6] is_off_now overnight: OK")

    print("\nALL CHECKS PASSED")


asyncio.run(main())
