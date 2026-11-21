"""Сховище бота: Turso (libSQL, SQLite у хмарі) через HTTP-драйвер turso_serverless.

Налаштування — лише зі змінних оточення (TURSO_DATABASE_URL, TURSO_AUTH_TOKEN).

Драйвер синхронний і ходить у мережу через urllib, тому:
  * усі виклики виконуються в потоці (asyncio.to_thread);
  * є таймаут сокета (без нього завислий запит блокував би потік назавжди);
  * connection працює в autocommit (isolation_level=None): один SQL-запит = один HTTP-запит;
  * користувачі кешуються в пам'яті (write-through), щоб кнопки не чекали на мережу.
"""
import asyncio
import json
import logging
import os
import socket
import time

import turso_serverless

log = logging.getLogger(__name__)

# urllib усередині turso_serverless викликається без timeout — задаємо глобальний
socket.setdefaulttimeout(15)

# Один процес — достатньо asyncio.Lock, щоб read-modify-write по юзеру був атомарним
_user_lock = asyncio.Lock()

# Кеш користувачів: chat_id -> {"chat_id", "queues", "notify_all"}
_users: dict[int, dict] = {}


def _connect():
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if not url or not token:
        raise RuntimeError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be configured")
    return turso_serverless.connect(url, auth_token=token, isolation_level=None)


def _run(fn):
    """fn(db) у синхронному потоці; одна повторна спроба при збої мережі."""
    last: Exception | None = None
    for attempt in (1, 2):
        db = _connect()  # відсутні змінні оточення — одразу помилка, без повторів
        try:
            return fn(db)
        except Exception as e:
            last = e
            log.warning("Turso query failed (attempt %d): %s: %s", attempt, type(e).__name__, e)
            if attempt == 1:
                time.sleep(0.3)
        finally:
            try:
                db.close()
            except Exception:
                pass
    raise last  # type: ignore[misc]


async def _db(fn):
    return await asyncio.to_thread(_run, fn)


async def init_db() -> None:
    def _init(db):
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id    INTEGER PRIMARY KEY,
                queues     TEXT    NOT NULL DEFAULT '[]',
                notify_all INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Старий стан (залишено для сумісності зі вже існуючими БД, більше не використовується)
        db.execute("""
            CREATE TABLE IF NOT EXISTS schedule_state (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                updated_at TEXT,
                day        INTEGER,
                month      INTEGER
            )
        """)
        db.execute("""
            INSERT OR IGNORE INTO schedule_state (id, updated_at, day, month)
            VALUES (1, NULL, NULL, NULL)
        """)
        # Знімок графіка за календарною датою ("MM-DD") -> {черга: [[start, end], ...]}
        db.execute("""
            CREATE TABLE IF NOT EXISTS schedule_snapshot (
                date_key TEXT PRIMARY KEY,
                data     TEXT NOT NULL
            )
        """)
    await _db(_init)


def _row_to_user(row) -> dict:
    chat_id, queues, notify_all = row
    return {
        "chat_id":    chat_id,
        "queues":     json.loads(queues),
        "notify_all": bool(notify_all),
    }


def _copy(user: dict) -> dict:
    """Копія, щоб виклики не псували кеш, змінюючи словник."""
    return {
        "chat_id":    user["chat_id"],
        "queues":     list(user["queues"]),
        "notify_all": user["notify_all"],
    }


async def get_user(chat_id: int) -> dict | None:
    cached = _users.get(chat_id)
    if cached is not None:
        return _copy(cached)
    row = await _db(lambda db: db.execute(
        "SELECT chat_id, queues, notify_all FROM users WHERE chat_id = ?", (chat_id,)
    ).fetchone())
    if not row:
        return None
    # setdefault: якщо за час запиту хтось уже записав новіше значення — не затираємо його
    return _copy(_users.setdefault(chat_id, _row_to_user(row)))


async def upsert_user(chat_id: int, queues: list[str], notify_all: bool) -> None:
    def _upsert(db):
        db.execute("""
            INSERT INTO users (chat_id, queues, notify_all)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                queues     = excluded.queues,
                notify_all = excluded.notify_all
        """, (chat_id, json.dumps(queues), int(notify_all)))
    await _db(_upsert)
    _users[chat_id] = {"chat_id": chat_id, "queues": list(queues), "notify_all": bool(notify_all)}


async def get_all_users() -> list[dict]:
    rows = await _db(lambda db: db.execute(
        "SELECT chat_id, queues, notify_all FROM users"
    ).fetchall())
    users = [_row_to_user(r) for r in rows]
    for u in users:
        _users.setdefault(u["chat_id"], u)
    return users


async def delete_user(chat_id: int) -> None:
    await _db(lambda db: db.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,)))
    _users.pop(chat_id, None)


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
    rows = await _db(lambda db: db.execute(
        "SELECT date_key, data FROM schedule_snapshot"
    ).fetchall())
    return {k: json.loads(v) for k, v in rows}


async def save_snapshots(
    to_save: dict[str, dict[str, list[list[str]]]],
    keep_keys: set[str],
) -> None:
    """Записує знімки і видаляє всі, яких нема в keep_keys (в одній транзакції)."""
    def _save(db):
        db.execute("BEGIN")
        try:
            for key, data in to_save.items():
                db.execute(
                    "INSERT INTO schedule_snapshot (date_key, data) VALUES (?, ?) "
                    "ON CONFLICT(date_key) DO UPDATE SET data = excluded.data",
                    (key, json.dumps(data)),
                )
            keep = set(keep_keys) | set(to_save)
            if keep:
                placeholders = ",".join("?" * len(keep))
                db.execute(
                    f"DELETE FROM schedule_snapshot WHERE date_key NOT IN ({placeholders})",
                    tuple(keep),
                )
            db.execute("COMMIT")
        except Exception:
            try:
                db.execute("ROLLBACK")
            except Exception:
                pass
            raise
    await _db(_save)
