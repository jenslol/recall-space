"""
Recall Space — Reminder Notifier
Background task that checks for due reminders and sends push notifications via ntfy.
"""

import asyncio
import os
import logging
from datetime import datetime, timezone

import httpx

from app.database import get_db, now_iso

log = logging.getLogger("recall.reminders")

NTFY_URL = os.getenv("NTFY_URL", "")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "recall-space")
CHECK_INTERVAL = 60  # seconds


async def send_notification(title: str, body: str, url: str = ""):
    """Send a push notification via ntfy."""
    if not NTFY_URL:
        log.debug("ntfy not configured, skipping notification")
        return False

    target = f"{NTFY_URL}/{NTFY_TOPIC}"

    headers = {
        "Title": title,
        "Priority": "high",
        "Tags": "brain,memo",
    }
    if url:
        headers["Click"] = url
        headers["Actions"] = f"view, Open Memory, {url}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(target, content=body, headers=headers)
            resp.raise_for_status()
        log.info(f"  Notification sent: {title}")
        return True
    except Exception as e:
        log.error(f"  Failed to send notification: {e}")
        return False


async def run_reminder_checker():
    """Background loop: check for due reminders every 60 seconds."""
    log.info("Reminder checker started")
    log.info(f"  ntfy: {NTFY_URL}/{NTFY_TOPIC}" if NTFY_URL else "  ntfy: not configured")

    while True:
        try:
            db = await get_db()
            try:
                now = now_iso()

                # Find unsent reminders that are due
                rows = await db.execute_fetchall(
                    """SELECT r.*, m.title as memory_title, m.ai_summary, m.id as mid
                       FROM reminders r
                       JOIN memories m ON m.id = r.memory_id
                       WHERE r.sent = 0 AND r.remind_at <= ?
                       ORDER BY r.remind_at ASC
                       LIMIT 10""",
                    (now,),
                )

                for row in rows:
                    r = dict(row)
                    reminder_title = r["title"] or "Reminder"
                    memory_title = r["memory_title"] or ""
                    summary = r["ai_summary"] or ""

                    # Build notification body
                    body_parts = []
                    if memory_title and memory_title != reminder_title:
                        body_parts.append(f"From: {memory_title}")
                    if summary:
                        body_parts.append(summary[:200])
                    body = "\n".join(body_parts) if body_parts else reminder_title

                    # Send push notification
                    base_url = os.getenv("BASE_URL", "")
                    memory_url = f"{base_url}/memory/{r['mid']}" if base_url else ""

                    sent = await send_notification(
                        title=f"{reminder_title}",
                        body=body,
                        url=memory_url,
                    )

                    # Mark as sent regardless (don't spam on ntfy failure)
                    await db.execute(
                        "UPDATE reminders SET sent = 1 WHERE id = ?", (r["id"],)
                    )
                    await db.commit()

                    if not sent:
                        log.warning(f"  Reminder #{r['id']} marked sent but notification failed")

            finally:
                await db.close()

        except Exception as e:
            log.error(f"Reminder checker error: {e}", exc_info=True)

        await asyncio.sleep(CHECK_INTERVAL)
