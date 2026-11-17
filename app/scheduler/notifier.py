import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from app.bot.keyboards import main_menu
from app.db.database import (
    delete_user,
    get_all_users,
    get_snapshots,
    save_snapshots,
)
from app.parser.base import TZ, Schedule
from app.parser.schedule import fetch_schedules, format_schedule

log = logging.getLogger(__name__)

SEND_DELAY = 0.05  # ~20 msg/s — із запасом до ліміту Telegram у 30 msg/s
_FETCH_RETRIES = 3
_FETCH_RETRY_DELAY = 5  # seconds between retries


async def _fetch_with_retry():
    last_exc = None
    for attempt in range(1, _FETCH_RETRIES + 1):
        try:
            return await fetch_schedules(force=True)
        except Exception as e:
            last_exc = e
            log.warning("Scheduler: fetch attempt %d/%d failed: %s: %s",
                        attempt, _FETCH_RETRIES, type(e).__name__, e)
            if attempt < _FETCH_RETRIES:
                await asyncio.sleep(_FETCH_RETRY_DELAY)
    log.error("Scheduler: all %d fetch attempts failed", _FETCH_RETRIES, exc_info=last_exc)
    return None


async def _send_safe(bot: Bot, chat_id: int, text: str) -> None:
    for attempt in range(2):
        try:
            await bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
                reply_markup=main_menu(),
                disable_web_page_preview=True,
            )
            return
        except TelegramRetryAfter as e:
            wait = int(getattr(e, "retry_after", 5)) + 1
            log.warning("Flood control for %s: waiting %ss", chat_id, wait)
            if attempt == 0:
                await asyncio.sleep(wait)
            else:
                return
        except TelegramForbiddenError:
            # Юзер заблокував бота / видалив чат — більше не пишемо йому
            log.info("User %s blocked the bot, removing", chat_id)
            await delete_user(chat_id)
            return
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower():
                log.info("Chat %s not found, removing", chat_id)
                await delete_user(chat_id)
            else:
                log.error("send_message failed for %s: %s", chat_id, e)
            return
        except Exception as e:
            log.error("send_message failed for %s: %s", chat_id, e)
            return


def date_key(schedule: Schedule) -> str:
    return f"{schedule.month:02d}-{schedule.day:02d}"


def normalize(schedule: Schedule) -> dict[str, list[list[str]]]:
    return {q: [[a, b] for a, b in iv] for q, iv in schedule.queues.items()}


def diff_queues(old: dict | None, new: dict[str, list[list[str]]]) -> set[str]:
    """Черги, чий розклад відрізняється від збереженого (або яких раніше не було)."""
    if old is None:
        return set(new)
    return {q for q, iv in new.items() if old.get(q) != iv}


async def check_and_notify(bot: Bot) -> None:
    log.info("Scheduler: fetching schedules...")

    result = await _fetch_with_retry()
    if result is None:
        return
    today, tomorrow = result

    schedules = [s for s in (today, tomorrow) if s is not None]
    if not schedules:
        log.warning("Scheduler: no schedules found")
        return

    now = datetime.now(TZ)
    keep = {(now + timedelta(days=d)).strftime("%m-%d") for d in (-1, 0, 1)}

    snapshots = await get_snapshots()
    first_run = not snapshots  # свіжа БД / перший запуск після оновлення — тільки запам'ятовуємо

    to_save: dict[str, dict] = {}
    to_notify: list[tuple[Schedule, set[str]]] = []

    for sched in schedules:
        key = date_key(sched)
        new = normalize(sched)
        old = snapshots.get(key)
        changed = diff_queues(old, new)
        # Збираємо знімок: нові дані поверх старих (резервні джерела можуть віддати неповний набір черг)
        merged = {**(old or {}), **new}
        if merged != old:
            to_save[key] = merged
        if changed and not first_run:
            to_notify.append((sched, changed))

    if to_save or keep:
        await save_snapshots(to_save, keep)

    if first_run:
        log.info("Scheduler: first run, snapshots seeded (%s)", ", ".join(sorted(to_save)))
        return

    if not to_notify:
        log.info("Scheduler: no changes")
        return

    users = await get_all_users()
    log.info("Scheduler: %d schedule(s) changed, %d users", len(to_notify), len(users))

    for sched, changed in to_notify:
        log.info("Scheduler: %s changed for queues %s", sched.title, sorted(changed))
        for user in users:
            if user["notify_all"]:
                queue_ids = None
            else:
                mine = [q for q in user["queues"] if q in sched.queues]
                # Сповіщаємо, лише якщо змінилась хоча б одна з його черг
                if not any(q in changed for q in mine):
                    continue
                queue_ids = mine

            await _send_safe(bot, user["chat_id"], format_schedule(sched, queue_ids))
            await asyncio.sleep(SEND_DELAY)

    log.info("Scheduler: done")
