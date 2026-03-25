"""
Recall Space — Main Application
FastAPI server with integrated background job processor.
"""

import asyncio
import os
import uuid
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

import httpx

from app.database import init_db, get_db, now_iso
from app.processor import run_processor
from app.reminders import run_reminder_checker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("recall.app")

UPLOADS_PATH = os.getenv("UPLOADS_PATH", "uploads")
AI_WORKER_URL = os.getenv("AI_WORKER_URL", "")
API_KEY = os.getenv("API_KEY", "")  # Empty = no auth required

# Text file extensions that should be read directly
TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".log",
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf",
    ".py", ".js", ".sh", ".fish", ".ts", ".css",
}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def check_auth(api_key: str | None = Depends(api_key_header), request: Request = None):
    """Require API key if API_KEY is set. Skip auth for web UI pages."""
    if not API_KEY:
        return  # No auth configured
    # Allow web UI pages without auth (they're served as HTML)
    if request and not request.url.path.startswith("/api/"):
        return
    if api_key != API_KEY:
        raise HTTPException(401, "Invalid or missing API key")


# ---------------------------------------------------------------------------
# Lifespan — init DB + start processor as background task
# ---------------------------------------------------------------------------

def ensure_dirs():
    for sub in ["screenshots", "audio", "files"]:
        os.makedirs(os.path.join(UPLOADS_PATH, sub), exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    await init_db()
    processor_task = asyncio.create_task(run_processor())
    reminder_task = asyncio.create_task(run_reminder_checker())
    log.info("Recall Space v0.2.1 ready")
    yield
    processor_task.cancel()
    reminder_task.cancel()
    try:
        await processor_task
    except asyncio.CancelledError:
        pass
    try:
        await reminder_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Recall Space", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS_PATH), name="uploads")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def detect_type(content_type: str, filename: str) -> tuple[str, str]:
    """Return (memory_type, subdirectory) based on MIME and extension."""
    ext = Path(filename).suffix.lower() if filename else ""

    if content_type and content_type.startswith("image/"):
        return "screenshot", "screenshots"
    if content_type and content_type.startswith("audio/"):
        return "voice", "audio"
    if ext in TEXT_EXTENSIONS or (content_type and content_type.startswith("text/")):
        return "file", "files"  # Type stays 'file', but we'll queue extract_text
    return "file", "files"


async def save_upload(upload: UploadFile, subdir: str) -> tuple[str, str]:
    ext = Path(upload.filename).suffix if upload.filename else ""
    unique_name = f"{uuid.uuid4().hex}{ext}"
    rel_path = os.path.join(subdir, unique_name)
    abs_path = os.path.join(UPLOADS_PATH, rel_path)

    content = await upload.read()
    with open(abs_path, "wb") as f:
        f.write(content)

    return rel_path, upload.filename or unique_name


def is_text_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in TEXT_EXTENSIONS if filename else False


async def enqueue_jobs(db, memory_id: int, memory_type: str, filename: str = ""):
    """Create processing jobs based on memory type."""
    jobs = []
    if memory_type == "screenshot":
        jobs.append("ocr")
    elif memory_type == "voice":
        jobs.append("transcribe")
    elif memory_type == "file" and is_text_file(filename):
        jobs.append("extract_text")
    jobs.append("analyze")

    for jtype in jobs:
        await db.execute(
            "INSERT INTO job_queue (memory_id, job_type) VALUES (?,?)",
            (memory_id, jtype),
        )


# ---------------------------------------------------------------------------
# API: Capture
# ---------------------------------------------------------------------------

@app.post("/api/memories", dependencies=[Depends(check_auth)])
async def create_memory(
    file: UploadFile | None = File(None),
    user_note: str | None = Form(None),
    url: str | None = Form(None),
    raw_text: str | None = Form(None),
    title: str | None = Form(None),
    collection_id: int | None = Form(None),
    transcript: str | None = Form(None),
):
    memory_type = "text"
    file_path = None
    original_filename = ""

    if file and file.filename:
        content_type = file.content_type or ""
        memory_type, subdir = detect_type(content_type, file.filename)
        file_path, original_filename = await save_upload(file, subdir)
    elif url:
        memory_type = "url"

    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO memories
               (type, title, user_note, file_path, original_filename,
                url, raw_text, transcript, collection_id, processing_status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (memory_type, title, user_note, file_path, original_filename,
             url, raw_text, transcript, collection_id, "pending"),
        )
        memory_id = cursor.lastrowid
        await enqueue_jobs(db, memory_id, memory_type, original_filename)
        await db.commit()

        row = await db.execute_fetchall("SELECT * FROM memories WHERE id=?", (memory_id,))
        return JSONResponse(
            {"status": "ok", "memory_id": memory_id, "memory": dict(row[0]) if row else {}},
            status_code=201,
        )
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# API: List / Get / Search / Update / Delete
# ---------------------------------------------------------------------------

@app.get("/api/memories", dependencies=[Depends(check_auth)])
async def list_memories(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    type: str | None = Query(None),
    collection_id: int | None = Query(None),
    q: str | None = Query(None),
):
    db = await get_db()
    try:
        if q:
            rows = await db.execute_fetchall(
                """SELECT m.* FROM memories m
                   JOIN memories_fts f ON m.id = f.rowid
                   WHERE memories_fts MATCH ?
                   ORDER BY m.created_at DESC LIMIT ? OFFSET ?""",
                (q, limit, offset),
            )
        else:
            conditions, params = [], []
            if type:
                conditions.append("type=?")
                params.append(type)
            if collection_id is not None:
                conditions.append("collection_id=?")
                params.append(collection_id)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = await db.execute_fetchall(
                f"SELECT * FROM memories {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )

        count = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM memories")
        return {"memories": [dict(r) for r in rows], "total": count[0]["cnt"]}
    finally:
        await db.close()


@app.get("/api/memories/{memory_id}", dependencies=[Depends(check_auth)])
async def get_memory(memory_id: int):
    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT * FROM memories WHERE id=?", (memory_id,))
        if not rows:
            raise HTTPException(404, "Memory not found")
        reminders = await db.execute_fetchall(
            "SELECT * FROM reminders WHERE memory_id=?", (memory_id,)
        )
        memory = dict(rows[0])
        memory["reminders"] = [dict(r) for r in reminders]
        return memory
    finally:
        await db.close()


@app.patch("/api/memories/{memory_id}", dependencies=[Depends(check_auth)])
async def patch_memory(memory_id: int, request: Request):
    body = await request.json()
    allowed = {"title", "user_note", "collection_id"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields")
    db = await get_db()
    try:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values())
        await db.execute(
            f"UPDATE memories SET {sets}, updated_at=? WHERE id=?",
            (*vals, now_iso(), memory_id),
        )
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@app.delete("/api/memories/{memory_id}", dependencies=[Depends(check_auth)])
async def delete_memory(memory_id: int):
    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT file_path FROM memories WHERE id=?", (memory_id,))
        if not rows:
            raise HTTPException(404, "Memory not found")
        fp = rows[0]["file_path"]
        if fp:
            abs_path = os.path.join(UPLOADS_PATH, fp)
            if os.path.exists(abs_path):
                os.remove(abs_path)
        await db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# API: Collections
# ---------------------------------------------------------------------------

@app.get("/api/collections", dependencies=[Depends(check_auth)])
async def list_collections():
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT c.*, COUNT(m.id) as memory_count FROM collections c
               LEFT JOIN memories m ON m.collection_id = c.id
               GROUP BY c.id ORDER BY c.created_at DESC"""
        )
        return {"collections": [dict(r) for r in rows]}
    finally:
        await db.close()


@app.post("/api/collections", dependencies=[Depends(check_auth)])
async def create_collection(request: Request):
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    db = await get_db()
    try:
        cur = await db.execute(
            "INSERT INTO collections (name, color) VALUES (?,?)",
            (name, body.get("color", "#3BAA34")),
        )
        await db.commit()
        return {"status": "ok", "id": cur.lastrowid}
    finally:
        await db.close()


@app.delete("/api/collections/{collection_id}", dependencies=[Depends(check_auth)])
async def delete_collection(collection_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM collections WHERE id=?", (collection_id,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# API: Dashboard / Action Items
# ---------------------------------------------------------------------------

@app.get("/api/dashboard", dependencies=[Depends(check_auth)])
async def get_dashboard():
    """Combined dashboard data: upcoming reminders, open action items, processing count."""
    db = await get_db()
    try:
        # Upcoming reminders (next 7 days, unsent)
        reminders = await db.execute_fetchall(
            """SELECT r.id, r.remind_at, r.title, r.memory_id, m.title as memory_title
               FROM reminders r JOIN memories m ON m.id = r.memory_id
               WHERE r.sent = 0
               ORDER BY r.remind_at ASC LIMIT 10"""
        )

        # Open action items (not done, most recent first)
        actions = await db.execute_fetchall(
            """SELECT a.id, a.text, a.done, a.memory_id, a.created_at, m.title as memory_title
               FROM action_items a JOIN memories m ON m.id = a.memory_id
               WHERE a.done = 0
               ORDER BY a.created_at DESC LIMIT 20"""
        )

        # Processing count
        processing = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM job_queue WHERE status IN ('pending','processing')"
        )

        # Recently completed (last 3)
        recent = await db.execute_fetchall(
            """SELECT id, title, ai_summary, type, created_at FROM memories
               WHERE processing_status = 'done' AND ai_summary IS NOT NULL
               ORDER BY updated_at DESC LIMIT 3"""
        )

        return {
            "reminders": [dict(r) for r in reminders],
            "actions": [dict(a) for a in actions],
            "processing_count": processing[0]["cnt"],
            "recent_completed": [dict(r) for r in recent],
        }
    finally:
        await db.close()


@app.get("/api/actions", dependencies=[Depends(check_auth)])
async def list_actions(done: bool = Query(False), limit: int = Query(50)):
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT a.*, m.title as memory_title FROM action_items a
               JOIN memories m ON m.id = a.memory_id
               WHERE a.done = ? ORDER BY a.created_at DESC LIMIT ?""",
            (1 if done else 0, limit),
        )
        return {"actions": [dict(r) for r in rows]}
    finally:
        await db.close()


@app.patch("/api/actions/{action_id}", dependencies=[Depends(check_auth)])
async def toggle_action(action_id: int, request: Request):
    body = await request.json()
    done = 1 if body.get("done") else 0
    db = await get_db()
    try:
        await db.execute("UPDATE action_items SET done=? WHERE id=?", (done, action_id))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@app.delete("/api/actions/{action_id}", dependencies=[Depends(check_auth)])
async def delete_action(action_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM action_items WHERE id=?", (action_id,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# API: Jobs / Stats / Worker status / Reminders
# ---------------------------------------------------------------------------

@app.get("/api/reminders", dependencies=[Depends(check_auth)])
async def list_reminders(
    upcoming: bool = Query(True),
    limit: int = Query(20),
):
    """List reminders. By default shows upcoming unsent ones."""
    db = await get_db()
    try:
        if upcoming:
            rows = await db.execute_fetchall(
                """SELECT r.*, m.title as memory_title
                   FROM reminders r JOIN memories m ON m.id = r.memory_id
                   WHERE r.sent = 0
                   ORDER BY r.remind_at ASC LIMIT ?""",
                (limit,),
            )
        else:
            rows = await db.execute_fetchall(
                """SELECT r.*, m.title as memory_title
                   FROM reminders r JOIN memories m ON m.id = r.memory_id
                   ORDER BY r.remind_at DESC LIMIT ?""",
                (limit,),
            )
        return {"reminders": [dict(r) for r in rows]}
    finally:
        await db.close()


@app.get("/api/jobs", dependencies=[Depends(check_auth)])
async def list_jobs(status: str | None = Query(None), limit: int = Query(20)):
    db = await get_db()
    try:
        if status:
            rows = await db.execute_fetchall(
                "SELECT * FROM job_queue WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT * FROM job_queue ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return {"jobs": [dict(r) for r in rows]}
    finally:
        await db.close()


@app.get("/api/stats", dependencies=[Depends(check_auth)])
async def get_stats():
    db = await get_db()
    try:
        total = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM memories")
        by_type = await db.execute_fetchall(
            "SELECT type, COUNT(*) as cnt FROM memories GROUP BY type"
        )
        pending = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM job_queue WHERE status='pending'"
        )
        upcoming = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM reminders WHERE sent=0"
        )
        return {
            "total_memories": total[0]["cnt"],
            "by_type": {r["type"]: r["cnt"] for r in by_type},
            "pending_jobs": pending[0]["cnt"],
            "upcoming_reminders": upcoming[0]["cnt"],
        }
    finally:
        await db.close()


@app.get("/api/worker-status")
async def worker_status():
    """Check if the AI worker (brain) is online. No auth required — used by UI."""
    if not AI_WORKER_URL:
        return {"online": False, "reason": "not configured"}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{AI_WORKER_URL}/health")
            if r.status_code == 200:
                return {"online": True, **r.json()}
    except Exception:
        pass
    return {"online": False, "reason": "unreachable"}


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/memory/{memory_id}")
async def memory_detail_page(request: Request, memory_id: int):
    return templates.TemplateResponse("detail.html", {"request": request, "memory_id": memory_id})


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.2.0"}
