import aiosqlite
from pathlib import Path

DB_PATH = Path("demo.db")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS demo_bookings (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id       INTEGER NOT NULL,
                style             TEXT,
                zone              TEXT,
                reference         TEXT,
                reference_file_id TEXT,
                allergy           TEXT,
                slot              TEXT,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migration: add column to existing databases that predate this field.
        try:
            await db.execute(
                "ALTER TABLE demo_bookings ADD COLUMN reference_file_id TEXT"
            )
        except Exception:
            pass  # Column already exists — nothing to do.
        await db.commit()


async def save_booking(telegram_id: int, data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO demo_bookings
               (telegram_id, style, zone, reference, reference_file_id, allergy, slot)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                telegram_id,
                data.get("style", "—"),
                data.get("zone", "—"),
                data.get("reference", "—"),
                data.get("reference_file_id"),
                data.get("allergy", "—"),
                data.get("slot", "—"),
            ),
        )
        await db.commit()
        return cur.lastrowid


async def get_booking(telegram_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM demo_bookings WHERE telegram_id = ? ORDER BY id DESC LIMIT 1",
            (telegram_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None
