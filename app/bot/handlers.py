import re
import logging
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from app.db.database import (
    add_queue,
    get_user,
    remove_queue,
    toggle_notify_all,
    upsert_user,
)
from app.parser.schedule import fetch_schedules, format_schedule
from app.bot.keyboards import main_menu, settings_menu, queue_picker

router = Router()
log = logging.getLogger(__name__)


async def _answer(cb: CallbackQuery, text: str = "") -> None:
    """cb.answer() that silently ignores expired query errors."""
    try:
        await cb.answer(text)
    except TelegramBadRequest:
        pass


async def _show(
    cb: CallbackQuery,
    text: str,
    reply_markup=None,
    disable_preview: bool = False,
) -> None:
    """Редагує повідомлення, на якому натиснули кнопку, замість надсилання нового.

    Нове повідомлення надсилається лише якщо редагувати не можна:
    оригінал старший за 48 год (InaccessibleMessage), видалений, або
    Telegram віддав іншу помилку. Якщо текст не змінився — просто мовчимо.
    """
    msg = cb.message

    if msg is not None and hasattr(msg, "edit_text"):
        try:
            await msg.edit_text(
                text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_preview,
            )
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            log.info("edit_text failed (%s), sending new message", e)
        except Exception as e:
            log.warning("edit_text unexpected error: %s", e)

    try:
        chat_id = msg.chat.id if msg is not None else cb.from_user.id
        await cb.bot.send_message(
            chat_id,
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=disable_preview,
        )
    except Exception as e:
        log.error("fallback send_message failed: %s", e)


ABOUT_TEXT = (
    "🤖 <b>ZP Light Bot</b>\n\n"
    "Бот стежить за графіком відключень світла у Запорізькій області "
    "та надсилає сповіщення щойно графік оновлюється.\n\n"
    "👨‍💻 <b>Автор:</b> fan-world-me\n"
    '🌐 <a href="https://fan-world-me.github.io/">Сайт автора</a>\n'
    '💻 <a href="https://github.com/fan-world-me">GitHub профіль</a>\n'
    '📦 <a href="https://github.com/fan-world-me/zp_light_bot">Репозиторій бота</a>\n\n'
    'Дані: <a href="https://svitlo.zp.ua/">svitlo.zp.ua</a> (графіки '
    '<a href="https://www.zoe.com.ua/outage/">Запоріжжяобленерго</a>), резерв — '
    '<a href="https://github.com/yaroslav2901/OE_OUTAGE_DATA">OE_OUTAGE_DATA</a>\n\n'
    "📄 Ліцензія: GPL-3.0"
)

DONATE_TEXT = (
    "💛 <b>Підтримати автора</b>\n\n"
    "Якщо бот корисний — можна підтримати розробку:\n\n"
    "💳 <b>Monobank:</b> <code>4441 1110 0821 0555</code>\n\n"
    "Дякую! 🙏"
)


def escape_html(text: str) -> str:
    return re.sub(r"[<>&\"']", lambda m: {
        "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;"
    }[m.group()], text)


async def _get_or_create(chat_id: int) -> dict:
    user = await get_user(chat_id)
    if not user:
        await upsert_user(chat_id, [], False)
        user = {"chat_id": chat_id, "queues": [], "notify_all": False}
    return user


# ── /start ──────────────────────────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    await _get_or_create(msg.chat.id)
    name = escape_html(msg.from_user.first_name or "")
    await msg.answer(
        f"👋 Привіт, {name}!\n\n"
        "Я слідкую за графіком відключень світла у Запорізькій області "
        "і надішлю повідомлення щойно графік оновиться.\n\n"
        "Спочатку обери свою чергу 👇",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


# ── /menu ──────────────────────────────────────────────────────────────────────────────────
@router.message(Command("menu"))
async def cmd_menu(msg: Message) -> None:
    await msg.answer("Головне меню:", reply_markup=main_menu(), parse_mode="HTML")


# ── fallback text ──────────────────────────────────────────────────────────────────────────────────
@router.message()
async def fallback(msg: Message) -> None:
    await msg.answer("Скористайся меню 👇", reply_markup=main_menu(), parse_mode="HTML")


# ── callbacks ──────────────────────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery) -> None:
    await _answer(cb)
    await _show(cb, "Головне меню:", main_menu())


@router.callback_query(F.data == "status_my")
async def cb_status_my(cb: CallbackQuery) -> None:
    await _answer(cb)
    user = await _get_or_create(cb.message.chat.id)

    if not user["queues"]:
        await _show(
            cb,
            "⚠️ Ти ще не додав жодної черги.\n"
            "Перейди в <b>Налаштування → Додати чергу</b>.",
            main_menu(),
        )
        return

    try:
        today, _ = await fetch_schedules()
    except Exception as e:
        log.error("status_my fetch error: %s", e, exc_info=True)
        await _show(cb, "❌ Не вдалося отримати графік. Спробуй пізніше.", main_menu())
        return

    if not today:
        await _show(cb, "😕 Графік на сьогодні не знайдено.", main_menu())
        return

    await _show(cb, format_schedule(today, user["queues"]), main_menu())


@router.callback_query(F.data == "status_all")
async def cb_status_all(cb: CallbackQuery) -> None:
    await _answer(cb)
    try:
        today, _ = await fetch_schedules()
    except Exception:
        log.error("status_all fetch error", exc_info=True)
        await _show(cb, "❌ Не вдалося отримати графік. Спробуй пізніше.", main_menu())
        return

    if not today:
        await _show(cb, "😕 Графік на сьогодні не знайдено.", main_menu())
        return

    await _show(cb, format_schedule(today, None), main_menu())


# ── на завтра ──────────────────────────────────────────────────────────────────────────────────
async def _send_tomorrow(cb: CallbackQuery, queue_ids: list[str] | None) -> None:
    try:
        _, tomorrow = await fetch_schedules()
    except Exception:
        log.error("status_tomorrow fetch error", exc_info=True)
        await _show(cb, "❌ Не вдалося отримати графік. Спробуй пізніше.", main_menu())
        return

    if not tomorrow:
        await _show(
            cb,
            "🌙 Графік на завтра ще не опублікований.\n"
            "Оператор зазвичай публікує його ввечері.",
            main_menu(),
        )
        return

    await _show(cb, format_schedule(tomorrow, queue_ids), main_menu())


@router.callback_query(F.data == "status_tomorrow")
async def cb_status_tomorrow(cb: CallbackQuery) -> None:
    await _answer(cb)
    user = await _get_or_create(cb.message.chat.id)
    if not user["queues"]:
        await _show(
            cb,
            "⚠️ Ти ще не додав жодної черги.\n"
            "Перейди в <b>Налаштування → Додати чергу</b>.",
            main_menu(),
        )
        return
    await _send_tomorrow(cb, user["queues"])


@router.callback_query(F.data == "settings")
async def cb_settings(cb: CallbackQuery) -> None:
    await _answer(cb)
    user = await _get_or_create(cb.message.chat.id)
    queues_str = ", ".join(user["queues"]) if user["queues"] else "<i>не вибрано</i>"
    notify_str = "всі черги" if user["notify_all"] else "тільки мої"
    await _show(
        cb,
        f"⚙️ <b>Налаштування</b>\n\n"
        f"Твої черги: <b>{queues_str}</b>\n"
        f"Сповіщення: <b>{notify_str}</b>",
        settings_menu(user["notify_all"]),
    )


@router.callback_query(F.data == "add_queue")
async def cb_add_queue(cb: CallbackQuery) -> None:
    await _answer(cb)
    user = await _get_or_create(cb.message.chat.id)
    await _show(
        cb,
        "Обери черги, які хочеш додати (можна декілька):",
        queue_picker(user["queues"], "pick_add"),
    )


@router.callback_query(F.data.startswith("pick_add:"))
async def cb_pick_add(cb: CallbackQuery) -> None:
    await _answer(cb)
    q    = cb.data.split(":")[1]
    user = await add_queue(cb.message.chat.id, q)

    queues_str = ", ".join(user["queues"])
    await _show(
        cb,
        f"✅ Черга <b>{q}</b> додана.\nТвої черги: <b>{queues_str}</b>\n\nДодай ще або повернись назад:",
        queue_picker(user["queues"], "pick_add"),
    )


@router.callback_query(F.data == "remove_queue")
async def cb_remove_queue(cb: CallbackQuery) -> None:
    await _answer(cb)
    user = await _get_or_create(cb.message.chat.id)

    if not user["queues"]:
        await _show(cb, "У тебе немає доданих черг.", settings_menu(user["notify_all"]))
        return

    await _show(
        cb,
        "Обери чергу, яку хочеш видалити:",
        queue_picker(user["queues"], "pick_remove", only_selected=True),
    )


@router.callback_query(F.data.startswith("pick_remove:"))
async def cb_pick_remove(cb: CallbackQuery) -> None:
    await _answer(cb)
    q    = cb.data.split(":")[1]
    user = await remove_queue(cb.message.chat.id, q)

    if not user["queues"]:
        await _show(
            cb,
            f"✅ Черга <b>{q}</b> видалена. Список черг порожній.",
            settings_menu(user["notify_all"]),
        )
    else:
        queues_str = ", ".join(user["queues"])
        await _show(
            cb,
            f"✅ Черга <b>{q}</b> видалена.\nТвої черги: <b>{queues_str}</b>",
            queue_picker(user["queues"], "pick_remove", only_selected=True),
        )


@router.callback_query(F.data == "toggle_notify")
async def cb_toggle_notify(cb: CallbackQuery) -> None:
    await _answer(cb)
    user = await toggle_notify_all(cb.message.chat.id)
    mode = "всі черги" if user["notify_all"] else "тільки мої"
    await _show(cb, f"🔔 Сповіщення: <b>{mode}</b>", settings_menu(user["notify_all"]))


@router.callback_query(F.data == "about")
async def cb_about(cb: CallbackQuery) -> None:
    await _answer(cb)
    await _show(cb, ABOUT_TEXT, main_menu(), disable_preview=True)


@router.callback_query(F.data == "donate")
async def cb_donate(cb: CallbackQuery) -> None:
    await _answer(cb)
    await _show(cb, DONATE_TEXT, main_menu())
