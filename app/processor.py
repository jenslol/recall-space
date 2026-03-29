"""
Recall Space — Job Processor (runs as background task inside the app).

Polls the job_queue table and dispatches work:
  - OCR and text extraction run locally (Tesseract / file read)
  - Transcription and analysis are sent to the AI worker (gaming PC)
  - Gracefully handles worker being offline (jobs stay pending)
"""

import asyncio
import json
import os
import logging

import httpx

from app.database import get_db, now_iso

log = logging.getLogger("recall.processor")

UPLOADS_PATH = os.getenv("UPLOADS_PATH", "uploads")
AI_WORKER_URL = os.getenv("AI_WORKER_URL", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))

# Text file extensions we can read directly
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".log", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".py", ".js", ".sh", ".fish"}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def mark_job(db, job_id: int, status: str, error: str = None):
    now = now_iso()
    if status == "processing":
        await db.execute(
            "UPDATE job_queue SET status=?, started_at=?, attempts=attempts+1 WHERE id=?",
            (status, now, job_id),
        )
    else:
        await db.execute(
            "UPDATE job_queue SET status=?, completed_at=?, error=? WHERE id=?",
            (status, now, error, job_id),
        )
    await db.commit()


async def update_memory(db, memory_id: int, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values())
    await db.execute(
        f"UPDATE memories SET {sets}, updated_at=? WHERE id=?",
        (*vals, now_iso(), memory_id),
    )
    await db.commit()


async def check_memory_done(db, memory_id: int):
    """Mark memory as done if all its jobs are finished."""
    rows = await db.execute_fetchall(
        "SELECT COUNT(*) as cnt FROM job_queue WHERE memory_id=? AND status IN ('pending','processing')",
        (memory_id,),
    )
    if rows[0]["cnt"] == 0:
        await update_memory(db, memory_id, processing_status="done")
        log.info(f"  Memory #{memory_id}: all processing complete ✓")


async def get_next_job(db):
    """Get next pending job. OCR/transcribe/extract_text run before analyze."""
    rows = await db.execute_fetchall("""
        SELECT j.*, m.type as memory_type FROM job_queue j
        JOIN memories m ON m.id = j.memory_id
        WHERE j.status = 'pending' AND j.attempts < 3
          AND (
            j.job_type IN ('ocr', 'transcribe', 'extract_text')
            OR (j.job_type = 'analyze' AND NOT EXISTS (
                SELECT 1 FROM job_queue j2
                WHERE j2.memory_id = j.memory_id
                  AND j2.job_type IN ('ocr', 'transcribe', 'extract_text')
                  AND j2.status IN ('pending', 'processing')
            ))
          )
        ORDER BY j.created_at ASC LIMIT 1
    """)
    return dict(rows[0]) if rows else None


# ---------------------------------------------------------------------------
# Job handlers
# ---------------------------------------------------------------------------

async def process_extract_text(db, job: dict):
    """Read content from text-based files (.txt, .md, .csv, etc.)."""
    memory_id = job["memory_id"]
    rows = await db.execute_fetchall(
        "SELECT file_path, original_filename FROM memories WHERE id=?", (memory_id,)
    )
    if not rows or not rows[0]["file_path"]:
        await mark_job(db, job["id"], "skipped")
        return

    file_path = os.path.join(UPLOADS_PATH, rows[0]["file_path"])
    if not os.path.exists(file_path):
        await mark_job(db, job["id"], "failed", "File not found")
        return

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(100_000)  # Cap at 100KB of text
        text = text.strip()

        if text:
            await update_memory(db, memory_id, raw_text=text)
            log.info(f"  Extracted {len(text)} chars from file")
        else:
            log.info(f"  File was empty")

        await mark_job(db, job["id"], "done")
    except Exception as e:
        log.error(f"  Text extraction failed: {e}")
        await mark_job(db, job["id"], "failed", str(e))


async def process_ocr(db, job: dict):
    """Extract text from images using Tesseract."""
    memory_id = job["memory_id"]
    rows = await db.execute_fetchall(
        "SELECT file_path FROM memories WHERE id=?", (memory_id,)
    )
    if not rows or not rows[0]["file_path"]:
        await mark_job(db, job["id"], "skipped")
        return

    file_path = os.path.join(UPLOADS_PATH, rows[0]["file_path"])
    if not os.path.exists(file_path):
        await mark_job(db, job["id"], "failed", "File not found")
        return

    try:
        import pytesseract
        from PIL import Image

        img = Image.open(file_path)
        text = pytesseract.image_to_string(img, lang="eng+dan+deu").strip()

        if text:
            await update_memory(db, memory_id, ocr_text=text)
            log.info(f"  OCR extracted {len(text)} chars")
        else:
            log.info(f"  OCR: no text found in image")

        await mark_job(db, job["id"], "done")
    except Exception as e:
        log.error(f"  OCR failed: {e}")
        await mark_job(db, job["id"], "failed", str(e))


async def process_transcribe(db, job: dict):
    """Send audio to AI worker for Whisper transcription."""
    memory_id = job["memory_id"]
    rows = await db.execute_fetchall(
        "SELECT file_path, transcript FROM memories WHERE id=?", (memory_id,)
    )
    if not rows:
        await mark_job(db, job["id"], "skipped")
        return

    mem = dict(rows[0])

    # Skip if transcript already provided (e.g. from on-device Whisper)
    if mem.get("transcript"):
        log.info(f"  Transcript already exists, skipping")
        await mark_job(db, job["id"], "done")
        return

    if not mem.get("file_path"):
        await mark_job(db, job["id"], "skipped")
        return

    file_path = os.path.join(UPLOADS_PATH, mem["file_path"])
    if not os.path.exists(file_path):
        await mark_job(db, job["id"], "failed", "Audio file not found")
        return

    if not AI_WORKER_URL:
        log.warning(f"  No AI_WORKER_URL configured, skipping transcription")
        await mark_job(db, job["id"], "skipped")
        return

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    f"{AI_WORKER_URL}/transcribe",
                    files={"file": (os.path.basename(file_path), f)},
                )
            resp.raise_for_status()

        data = resp.json()
        transcript = data.get("transcript", "")
        lang = data.get("language", "")
        dur = data.get("duration", 0)

        if transcript:
            await update_memory(db, memory_id, transcript=transcript)
            log.info(f"  Transcribed {dur}s ({lang}): {len(transcript)} chars")
        else:
            log.info(f"  Transcription returned empty")

        await mark_job(db, job["id"], "done")

    except (httpx.ConnectError, httpx.ConnectTimeout):
        log.warning(f"  AI worker offline — will retry later")
        # Don't count this as an attempt
        await db.execute(
            "UPDATE job_queue SET status='pending', attempts=attempts-1 WHERE id=?",
            (job["id"],),
        )
        await db.commit()
    except Exception as e:
        log.error(f"  Transcription failed: {e}")
        await mark_job(db, job["id"], "failed", str(e))


async def process_analyze(db, job: dict):
    """Send text content to AI worker for LLM analysis."""
    memory_id = job["memory_id"]
    rows = await db.execute_fetchall("SELECT * FROM memories WHERE id=?", (memory_id,))
    if not rows:
        await mark_job(db, job["id"], "skipped")
        return

    mem = dict(rows[0])

    # Gather all available text
    text_parts = []
    for field in ("user_note", "raw_text", "ocr_text", "transcript"):
        if mem.get(field):
            text_parts.append(mem[field])
    if mem.get("url"):
        text_parts.append(f"URL: {mem['url']}")

    if not text_parts:
        log.info(f"  No text content to analyze, marking done")
        await mark_job(db, job["id"], "done")
        await check_memory_done(db, memory_id)
        return

    if not AI_WORKER_URL:
        log.warning(f"  No AI_WORKER_URL configured, skipping analysis")
        await mark_job(db, job["id"], "skipped")
        return

    combined = "\n\n".join(text_parts)

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{AI_WORKER_URL}/analyze",
                data={
                    "text": combined,
                    "user_note": mem.get("user_note") or "",
                    "content_type": mem.get("type") or "",
                },
            )
            resp.raise_for_status()

        data = resp.json()
        updates = {}
        if data.get("summary"):
            updates["ai_summary"] = data["summary"]
        if data.get("actions"):
            updates["ai_actions"] = json.dumps(data["actions"])
        if data.get("dates"):
            updates["ai_dates"] = json.dumps(data["dates"])
        if data.get("tags"):
            updates["ai_tags"] = json.dumps(data["tags"])

        if updates:
            await update_memory(db, memory_id, **updates)

        # Auto-create reminders from extracted dates
        if data.get("dates"):
            await create_reminders(db, memory_id, data["dates"])

        # Populate action_items table
        if data.get("actions"):
            await create_action_items(db, memory_id, data["actions"])

        log.info(f"  Analysis: {len(data.get('actions',[]))} actions, {len(data.get('dates',[]))} dates")
        await mark_job(db, job["id"], "done")

    except (httpx.ConnectError, httpx.ConnectTimeout):
        log.warning(f"  AI worker offline — will retry later")
        await db.execute(
            "UPDATE job_queue SET status='pending', attempts=attempts-1 WHERE id=?",
            (job["id"],),
        )
        await db.commit()
    except Exception as e:
        log.error(f"  Analysis failed: {e}")
        await mark_job(db, job["id"], "failed", str(e))


# ---------------------------------------------------------------------------
# Reminders & Action Items
# ---------------------------------------------------------------------------

async def create_reminders(db, memory_id: int, dates: list):
    from dateutil import parser as dateparser

    for date_str in dates:
        try:
            parts = date_str.split(" - ", 1)
            date_part = parts[0].strip()
            title = parts[1].strip() if len(parts) > 1 else date_str

            parsed = dateparser.parse(date_part)
            if parsed:
                await db.execute(
                    "INSERT INTO reminders (memory_id, remind_at, title) VALUES (?,?,?)",
                    (memory_id, parsed.isoformat(), title),
                )
                log.info(f"  Reminder: {title} @ {parsed.isoformat()}")
        except Exception as e:
            log.debug(f"  Could not parse date '{date_str}': {e}")

    await db.commit()


async def create_action_items(db, memory_id: int, actions: list):
    for action_text in actions:
        text = action_text.strip()
        if text:
            await db.execute(
                "INSERT INTO action_items (memory_id, text) VALUES (?,?)",
                (memory_id, text),
            )
    await db.commit()
    log.info(f"  Created {len(actions)} action items")


# ---------------------------------------------------------------------------
# Main loop (called as background task from app lifespan)
# ---------------------------------------------------------------------------

JOB_HANDLERS = {
    "extract_text": process_extract_text,
    "ocr": process_ocr,
    "transcribe": process_transcribe,
    "analyze": process_analyze,
}


async def run_processor():
    """Background processing loop. Runs inside the main FastAPI process."""
    log.info("Job processor started")
    log.info(f"  AI Worker: {AI_WORKER_URL or '(not configured)'}")
    log.info(f"  Poll interval: {POLL_INTERVAL}s")

    while True:
        had_job = False
        try:
            db = await get_db()
            try:
                job = await get_next_job(db)
                if job:
                    had_job = True
                    jtype = job["job_type"]
                    mid = job["memory_id"]
                    log.info(f"Job #{job['id']}: {jtype} for memory #{mid}")

                    await mark_job(db, job["id"], "processing")
                    await update_memory(db, mid, processing_status="processing")

                    handler = JOB_HANDLERS.get(jtype)
                    if handler:
                        await handler(db, job)
                    else:
                        await mark_job(db, job["id"], "failed", f"Unknown: {jtype}")

                    await check_memory_done(db, mid)
            finally:
                await db.close()

        except Exception as e:
            log.error(f"Processor error: {e}", exc_info=True)

        # Process quickly if there's a backlog, otherwise wait
        await asyncio.sleep(1 if had_job else POLL_INTERVAL)
