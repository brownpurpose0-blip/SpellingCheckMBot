import aiosqlite
from datetime import datetime, timezone

DB_PATH = "bot_data.db"

DEFAULT_LANGUAGE = "auto"  # LanguageTool auto-detects source language


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                language TEXT NOT NULL DEFAULT 'auto',
                checks_count INTEGER NOT NULL DEFAULT 0,
                issues_found INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
        await db.commit()


def _now():
    return datetime.now(timezone.utc).isoformat()


async def get_language(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT language FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else DEFAULT_LANGUAGE


async def set_language(user_id: int, language: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users (user_id, language, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET language = excluded.language, updated_at = excluded.updated_at""",
            (user_id, language, _now()),
        )
        await db.commit()


async def record_check(user_id: int, issues_count: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users (user_id, checks_count, issues_found, updated_at)
               VALUES (?, 1, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 checks_count = checks_count + 1,
                 issues_found = issues_found + excluded.issues_found,
                 updated_at = excluded.updated_at""",
            (user_id, issues_count, _now()),
        )
        await db.commit()


async def get_stats(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT checks_count, issues_found FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else {"checks_count": 0, "issues_found": 0}
