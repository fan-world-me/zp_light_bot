import asyncio
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
from app.parser.schedule import (
    fetch_schedules,
    format_schedule,
    peek,
    start_refresh,
)
from app.bot.keyboards import main_menu, settings_menu, queue_picker

router = Router()
log = logging.getLogger(__name__)

# Скільки максимум чекаємо фонового оновлення, щоб надіслати свіжіші дані
_REFRESH_WAIT = 25

_ERR_FETCH = "❌ Не вдалося отримати графік. Спробуй пізніше."
_EMPTY_TODAY = "😕 Графік на сьогодні не знайдено."
_EMPTY_TOMORROW = (
    "🌙 Графік на завтра ще не опублікований.\n"
    "Оператор зазвичай публікує його ввечері."
)


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
    '<a href="https://www.zoe.com.ua/outage/">Запоріжжяобленерго</a>)\n\n'
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


# ── графік: одразу з кешу, свіжіше — окремим повідомленням ─────────────────────────────────
def _render(value, idx: int, queue_ids: list[str] | None, empty_text: str) -> str:
    """idx: 0 — сьогодні, 1 — завтра."""
    sched = value[idx]
    return format_schedule(sched, queue_ids) if sched else empty_text


async def _send_update(cb: CallbackQuery, text: str) -> None:
    """Нове повідомлення (а не редагування): користувач міг уже піти в інше меню."""
    try:
        msg = cb.message
        chat_id = msg.chat.id if msg is not None else cb.from_user.id
        await cb.bot.send_message(
            chat_id,
            "🔄 <b>Дані оновилися</b>\n\n" + text,
            reply_markup=main_menu(),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.warning("update message failed: %s", e)


async def _serve_schedule(
    cb: CallbackQuery,
    idx: int,
    queue_ids: list[str] | None,
    empty_text: str,
) -> None:
    """Миттєво показує те, що є в кеші; оновлення йде паралельно у фоні.

    Якщо після оновлення текст змінився — надсилає ще одне повідомлення.
    Мережу чекаємо лише на холодному старті, коли кешу ще нема.
    """
    value, stale = peek()

    if value is None:
        try:
            value = await fetch_schedules()
        except Exception:
            log.error("schedule fetch error", exc_info=True)
            await _show(cb, _ERR_FETCH, main_menu())
            return
        stale = False

    task = start_refresh() if stale else None  # стартує паралельно з відповіддю
    text = _render(value, idx, queue_ids, empty_text)
    await _show(cb, text, main_menu())

    if task is None:
        return
    try:
        ok = await asyncio.wait_for(asyncio.shield(task), _REFRESH_WAIT)
    except Exception:
        return
    if not ok:
        return
    new_value, _ = peek()
    if new_value is None:
        return
    new_text = _render(new_value, idx, queue_ids, empty_text)
    if new_text != text:
        await _send_update(cb, new_text)


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

    await _serve_schedule(cb, 0, user["queues"], _EMPTY_TODAY)


@router.callback_query(F.data == "status_all")
async def cb_status_all(cb: CallbackQuery) -> None:
    await _answer(cb)
    await _serve_schedule(cb, 0, None, _EMPTY_TODAY)


# ── на завтра ──────────────────────────────────────────────────────────────────────────────────
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
    await _serve_schedule(cb, 1, user["queues"], _EMPTY_TOMORROW)


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
