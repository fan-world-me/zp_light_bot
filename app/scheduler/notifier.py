import asyncio
import logging
from aiogram import Bot

from app.db.database import get_all_users, get_schedule_state, set_schedule_state
from app.parser.schedule import fetch_schedules, format_schedule
from app.bot.keyboards import main_menu

log = logging.getLogger(__name__)

SEND_DELAY = 0.035
_FETCH_RETRIES = 3
_FETCH_RETRY_DELAY = 10  # seconds between retries


async def _fetch_with_retry():
    last_exc = None
    for attempt in range(1, _FETCH_RETRIES + 1):
        try:
            return await fetch_schedules()
        except Exception as e:
            last_exc = e
            log.warning("Scheduler: fetch attempt %d/%d failed: %s: %s",
                        attempt, _FETCH_RETRIES, type(e).__name__, e)
            if attempt < _FETCH_RETRIES:
                await asyncio.sleep(_FETCH_RETRY_DELAY)
    log.error("Scheduler: all %d fetch attempts failed", _FETCH_RETRIES, exc_info=last_exc)
    return None


async def _send_safe(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=main_menu(),
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.error("send_message failed for %s: %s", chat_id, e)


async def check_and_notify(bot: Bot) -> None:
    log.info("Scheduler: fetching schedules...")

    result = await _fetch_with_retry()
    if result is None:
        return
    today, tomorrow = result

    if not today:
        log.warning("Scheduler: no today schedule found")
        return

    # Check if schedule actually changed
    state = await get_schedule_state()
    new_key = f"{today.day}-{today.month}-{today.updated}"
    old_key = f"{state['day']}-{state['month']}-{state['updated_at']}"

    if new_key == old_key:
        log.info("Scheduler: no update (key=%s)", new_key)
        return

    log.info("Scheduler: schedule updated — %s (was %s)", new_key, old_key)
    await set_schedule_state(today.updated, today.day, today.month)

    # Collect all queue IDs from today's schedule
    all_queue_ids = list(today.queues.keys())

    users = await get_all_users()
    log.info("Scheduler: notifying %d users", len(users))

    for user in users:
        if not user["queues"] and not user["notify_all"]:
            continue

        queue_ids = None if user["notify_all"] else user["queues"]

        # Skip users whose queues don't appear in today's schedule
        if queue_ids is not None:
            matched = [q for q in queue_ids if q in today.queues]
            if not matched:
                continue
            queue_ids = matched

        text = format_schedule(today, queue_ids)
        await _send_safe(bot, user["chat_id"], text)
        await asyncio.sleep(SEND_DELAY)

    log.info("Scheduler: done")

