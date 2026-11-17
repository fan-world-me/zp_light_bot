import asyncio
import json
import os

import aiosqlite

DB_PATH = os.getenv("DB_PATH", "/data/bot.db")

# Один процес — достатньо asyncio.Lock, щоб read-modify-write по юзеру був атомарним
_user_lock = asyncio.Lock()


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id    INTEGER PRIMARY KEY,
                queues     TEXT    NOT NULL DEFAULT '[]',
                notify_all INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Старий стан (залишено для сумісності зі вже існуючими БД, більше не використовується)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedule_state (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                updated_at TEXT,
                day        INTEGER,
                month      INTEGER
            )
        """)
        await db.execute("""
            INSERT OR IGNORE INTO schedule_state (id, updated_at, day, month)
            VALUES (1, NULL, NULL, NULL)
        """)
        # Знімок графіка за календарною датою ("MM-DD") -> {черга: [[start, end], ...]}
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedule_snapshot (
                date_key TEXT PRIMARY KEY,
                data     TEXT NOT NULL
            )
        """)
        await db.commit()


def _row_to_user(row) -> dict:
    return {
        "chat_id":    row["chat_id"],
        "queues":     json.loads(row["queues"]),
        "notify_all": bool(row["notify_all"]),
    }


async def get_user(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT chat_id, queues, notify_all FROM users WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return _row_to_user(row) if row else None


async def upsert_user(chat_id: int, queues: list[str], notify_all: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (chat_id, queues, notify_all)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                queues     = excluded.queues,
                notify_all = excluded.notify_all
        """, (chat_id, json.dumps(queues), int(notify_all)))
        await db.commit()


async def get_all_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT chat_id, queues, notify_all FROM users") as cur:
            return [_row_to_user(r) for r in await cur.fetchall()]


async def delete_user(chat_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
        await db.commit()


# ── атомарні зміни юзера (щоб швидкі натискання не затирали одне одного) ──────
async def add_queue(chat_id: int, queue: str) -> dict:
    async with _user_lock:
        user = await get_user(chat_id) or {"chat_id": chat_id, "queues": [], "notify_all": False}
        if queue not in user["queues"]:
            user["queues"] = sorted([*user["queues"], queue])
            await upsert_user(chat_id, user["queues"], user["notify_all"])
        return user


async def remove_queue(chat_id: int, queue: str) -> dict:
    async with _user_lock:
        user = await get_user(chat_id) or {"chat_id": chat_id, "queues": [], "notify_all": False}
        if queue in user["queues"]:
            user["queues"] = [q for q in user["queues"] if q != queue]
            await upsert_user(chat_id, user["queues"], user["notify_all"])
        return user


async def toggle_notify_all(chat_id: int) -> dict:
    async with _user_lock:
        user = await get_user(chat_id) or {"chat_id": chat_id, "queues": [], "notify_all": False}
        user["notify_all"] = not user["notify_all"]
        await upsert_user(chat_id, user["queues"], user["notify_all"])
        return user


# ── знімки графіка ────────────────────────────────────────────────────────────
async def get_snapshots() -> dict[str, dict[str, list[list[str]]]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT date_key, data FROM schedule_snapshot") as cur:
            return {k: json.loads(v) for k, v in await cur.fetchall()}


async def save_snapshots(
    to_save: dict[str, dict[str, list[list[str]]]],
    keep_keys: set[str],
) -> None:
    """Записує знімки і видаляє всі, яких нема в keep_keys."""
    async with aiosqlite.connect(DB_PATH) as db:
        for key, data in to_save.items():
            await db.execute(
                "INSERT INTO schedule_snapshot (date_key, data) VALUES (?, ?) "
                "ON CONFLICT(date_key) DO UPDATE SET data = excluded.data",
                (key, json.dumps(data)),
            )
        keep = set(keep_keys) | set(to_save)
        placeholders = ",".join("?" * len(keep))
        await db.execute(
            f"DELETE FROM schedule_snapshot WHERE date_key NOT IN ({placeholders})",
            tuple(keep),
        )
        await db.commit()
