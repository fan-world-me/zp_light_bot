from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

ALL_QUEUES = ["1.1", "1.2", "2.1", "2.2", "3.1", "3.2",
              "4.1", "4.2", "5.1", "5.2", "6.1", "6.2"]


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Мій статус",   callback_data="status_my"),
            InlineKeyboardButton(text="🗓 Всі черги",    callback_data="status_all"),
        ],
        [InlineKeyboardButton(text="🌅 На завтра",      callback_data="status_tomorrow")],
        [InlineKeyboardButton(text="⚙️ Налаштування",   callback_data="settings")],
        [InlineKeyboardButton(text="ℹ️ Про бота",       callback_data="about")],
        [InlineKeyboardButton(text="💛 Підтримати автора", callback_data="donate")],
    ])


def settings_menu(notify_all: bool) -> InlineKeyboardMarkup:
    notify_label = (
        "🔔 Сповіщення: всі черги ✅"
        if notify_all else
        "🔔 Сповіщення: тільки мої ✅"
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Додати чергу",   callback_data="add_queue")],
        [InlineKeyboardButton(text="➖ Видалити чергу", callback_data="remove_queue")],
        [InlineKeyboardButton(text=notify_label,        callback_data="toggle_notify")],
        [InlineKeyboardButton(text="« Назад",           callback_data="menu")],
    ])


def queue_picker(selected: list[str], action: str) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(ALL_QUEUES), 4):
        chunk = ALL_QUEUES[i:i + 4]
        rows.append([
            InlineKeyboardButton(
                text=f"✅ {q}" if q in selected else q,
                callback_data=f"{action}:{q}",
            )
            for q in chunk
        ])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
