import aiosqlite
import os

DB_PATH = os.getenv("DB_PATH", "/data/bot.db")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id    INTEGER PRIMARY KEY,
                queues     TEXT    NOT NULL DEFAULT '[]',
                notify_all INTEGER NOT NULL DEFAULT 0
            )
        """)
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
        await db.commit()


async def get_user(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT chat_id, queues, notify_all FROM users WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            import json
            return {
                "chat_id":    row["chat_id"],
                "queues":     json.loads(row["queues"]),
                "notify_all": bool(row["notify_all"]),
            }


async def upsert_user(chat_id: int, queues: list[str], notify_all: bool) -> None:
    import json
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
    import json
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT chat_id, queues, notify_all FROM users") as cur:
            rows = await cur.fetchall()
            return [
                {
                    "chat_id":    r["chat_id"],
                    "queues":     json.loads(r["queues"]),
                    "notify_all": bool(r["notify_all"]),
                }
                for r in rows
            ]


async def get_schedule_state() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT updated_at, day, month FROM schedule_state WHERE id = 1"
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {"updated_at": None, "day": None, "month": None}


async def set_schedule_state(updated_at: str, day: int, month: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE schedule_state SET updated_at = ?, day = ?, month = ? WHERE id = 1
        """, (updated_at, day, month))
        await db.commit()
