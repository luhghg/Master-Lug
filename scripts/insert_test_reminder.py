"""
One-shot helper — inserts a test ApptReminder row scheduled 30 s from now.
Run: python scripts/insert_test_reminder.py <booking_id>

Usage:
  1. Make a real booking in the bot so appt_bookings has a row.
  2. Note the booking id from the master notification (ID запису: #N).
  3. python scripts/insert_test_reminder.py <N>
  4. Wait ~30 s — you should receive the reminder message in Telegram.
  5. After verifying, restore reminder_worker.py sleep to 60.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone


async def main(booking_id: int) -> None:
    import os
    from dotenv import load_dotenv
    load_dotenv()

    from app.core.database import AsyncSessionLocal
    from app.models.appointment import ApptReminder, ReminderStatus, ReminderType

    scheduled = datetime.now(timezone.utc) + timedelta(seconds=30)

    async with AsyncSessionLocal() as session:
        reminder = ApptReminder(
            booking_id=booking_id,
            reminder_type=ReminderType.HOURS_2,   # sends "Ваш сеанс через 2 години"
            status=ReminderStatus.PENDING,
            scheduled_at=scheduled,
        )
        session.add(reminder)
        try:
            await session.commit()
            print(f"✅ Test reminder inserted (id will be auto-assigned).")
            print(f"   booking_id = {booking_id}")
            print(f"   scheduled_at = {scheduled.isoformat()} (UTC)")
            print(f"   Worker will pick it up within ~10 s if the server is running.")
        except Exception as e:
            await session.rollback()
            print(f"❌ Failed to insert: {e}")
            print("   Is the booking_id correct and does it exist in appt_bookings?")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/insert_test_reminder.py <booking_id>")
        sys.exit(1)
    asyncio.run(main(int(sys.argv[1])))
