"""Recall Space — Database layer (SQLite + FTS5)."""

import aiosqlite
import os
from datetime import datetime, timezone

DATABASE_PATH = os.getenv("DATABASE_PATH", "data/recall.db")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)
    db = await get_db()

    await db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            type TEXT NOT NULL CHECK(type IN ('screenshot', 'voice', 'text', 'url', 'file')),
            title TEXT,
            user_note TEXT,
            file_path TEXT,
            original_filename TEXT,
            url TEXT,
            raw_text TEXT,
            ocr_text TEXT,
            transcript TEXT,
            ai_summary TEXT,
            ai_actions TEXT,
            ai_dates TEXT,
            ai_tags TEXT,
            processing_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(processing_status IN ('pending', 'processing', 'done', 'failed')),
            processing_error TEXT,
            collection_id INTEGER REFERENCES collections(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            color TEXT DEFAULT '#3BAA34'
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            remind_at TEXT NOT NULL,
            title TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
        );

        CREATE TABLE IF NOT EXISTS job_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            job_type TEXT NOT NULL CHECK(job_type IN ('ocr', 'transcribe', 'analyze', 'extract_text')),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'processing', 'done', 'failed', 'skipped')),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            started_at TEXT,
            completed_at TEXT,
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS action_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
        );

        CREATE INDEX IF NOT EXISTS idx_actions_done ON action_items(done);
        CREATE INDEX IF NOT EXISTS idx_actions_memory ON action_items(memory_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            title, user_note, raw_text, ocr_text, transcript, ai_summary, ai_actions,
            content=memories, content_rowid=id, tokenize='unicode61'
        );

        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, title, user_note, raw_text, ocr_text, transcript, ai_summary, ai_actions)
            VALUES (new.id, new.title, new.user_note, new.raw_text, new.ocr_text, new.transcript, new.ai_summary, new.ai_actions);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, user_note, raw_text, ocr_text, transcript, ai_summary, ai_actions)
            VALUES ('delete', old.id, old.title, old.user_note, old.raw_text, old.ocr_text, old.transcript, old.ai_summary, old.ai_actions);
            INSERT INTO memories_fts(rowid, title, user_note, raw_text, ocr_text, transcript, ai_summary, ai_actions)
            VALUES (new.id, new.title, new.user_note, new.raw_text, new.ocr_text, new.transcript, new.ai_summary, new.ai_actions);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, user_note, raw_text, ocr_text, transcript, ai_summary, ai_actions)
            VALUES ('delete', old.id, old.title, old.user_note, old.raw_text, old.ocr_text, old.transcript, old.ai_summary, old.ai_actions);
        END;

        CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
        CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(processing_status);
        CREATE INDEX IF NOT EXISTS idx_memories_collection ON memories(collection_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON job_queue(status);
        CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders(remind_at);
    """)

    await db.commit()
    await db.close()
