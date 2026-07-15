"""
database.py  —  Drop this file into laptop_backend/
SQLite database for Zoro 2026. Zero extra dependencies (uses stdlib sqlite3).
"""
import sqlite3
import json
import uuid
import datetime
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path("data/zoro.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS attendance_sessions (
            id          TEXT PRIMARY KEY,
            date        TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attendance_records (
            id          TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            name        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'present',
            time        TEXT,
            confidence  REAL,
            FOREIGN KEY (session_id) REFERENCES attendance_sessions(id)
        );

        CREATE TABLE IF NOT EXISTS syllabus_files (
            id          TEXT PRIMARY KEY,
            filename    TEXT NOT NULL,
            subject     TEXT DEFAULT '',
            size_kb     REAL DEFAULT 0,
            uploaded_at TEXT NOT NULL,
            status      TEXT DEFAULT 'ready',
            chunks      INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS speeches (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            trigger_phrase  TEXT DEFAULT '',
            content         TEXT NOT NULL,
            voice           TEXT DEFAULT 'aura-2-thalia-en',
            created_at      TEXT NOT NULL,
            last_triggered  TEXT
        );

        CREATE TABLE IF NOT EXISTS transcript_sessions (
            id          TEXT PRIMARY KEY,
            date        TEXT NOT NULL,
            start_time  TEXT,
            end_time    TEXT,
            topics      TEXT DEFAULT '[]',
            student_name TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS transcript_messages (
            id          TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            timestamp   TEXT,
            FOREIGN KEY (session_id) REFERENCES transcript_sessions(id)
        );
        """)
        for table, column, definition in [
            ("transcript_sessions", "student_name", "TEXT DEFAULT ''"),
        ]:
            columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


# ── Attendance ────────────────────────────────────────────────────────────────

def get_or_create_attendance_session(date: str = None) -> str:
    date = date or datetime.date.today().isoformat()
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM attendance_sessions WHERE date = ?", (date,)
        ).fetchone()
        if row:
            return row["id"]
        session_id = str(uuid.uuid4())[:8]
        conn.execute(
            "INSERT INTO attendance_sessions (id, date, created_at) VALUES (?, ?, ?)",
            (session_id, date, datetime.datetime.now().isoformat())
        )
        return session_id


def save_attendance_record(name: str, status: str = "present",
                            confidence: float = None, date: str = None):
    session_id = get_or_create_attendance_session(date)
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM attendance_records WHERE session_id=? AND name=?",
            (session_id, name)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO attendance_records (id, session_id, name, status, time, confidence) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4())[:8], session_id, name, status,
                 datetime.datetime.now().strftime("%H:%M:%S"), confidence)
            )


def get_attendance_sessions():
    with get_db() as conn:
        sessions = conn.execute(
            "SELECT * FROM attendance_sessions ORDER BY date DESC"
        ).fetchall()
        result = []
        for s in sessions:
            records = conn.execute(
                "SELECT * FROM attendance_records WHERE session_id=?", (s["id"],)
            ).fetchall()
            present = sum(1 for r in records if r["status"] == "present")
            result.append({
                "id": s["id"],
                "date": s["date"],
                "total_students": len(records),
                "present": present,
                "absent": len(records) - present,
                "filename": f"attendance_{s['date']}.csv",
            })
        return result


def get_attendance_records(session_id: str):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM attendance_records WHERE session_id=?", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Syllabus ──────────────────────────────────────────────────────────────────

def save_syllabus(filename: str, subject: str = "", size_kb: float = 0) -> dict:
    file_id = str(uuid.uuid4())[:8]
    with get_db() as conn:
        conn.execute(
            "INSERT INTO syllabus_files (id, filename, subject, size_kb, uploaded_at, status) VALUES (?,?,?,?,?,?)",
            (file_id, filename, subject, size_kb,
             datetime.date.today().isoformat(), "ready")
        )
    return {"id": file_id, "filename": filename, "subject": subject,
            "size_kb": size_kb, "uploaded_at": datetime.date.today().isoformat(),
            "status": "ready", "chunks": 0}


def get_syllabus_list():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM syllabus_files ORDER BY uploaded_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_syllabus(file_id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM syllabus_files WHERE id=?", (file_id,))


# ── Speeches ──────────────────────────────────────────────────────────────────

def save_speech(name: str, content: str, trigger_phrase: str = "",
                voice: str = "aura-2-thalia-en") -> dict:
    speech_id = str(uuid.uuid4())[:8]
    with get_db() as conn:
        conn.execute(
            "INSERT INTO speeches (id, name, trigger_phrase, content, voice, created_at) VALUES (?,?,?,?,?,?)",
            (speech_id, name, trigger_phrase, content, voice,
             datetime.date.today().isoformat())
        )
    return {"id": speech_id, "name": name, "trigger_phrase": trigger_phrase,
            "content": content, "voice": voice,
            "created_at": datetime.date.today().isoformat()}


def get_speeches():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM speeches ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def update_speech_triggered(speech_id: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE speeches SET last_triggered=? WHERE id=?",
            (datetime.date.today().isoformat(), speech_id)
        )


def delete_speech(speech_id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM speeches WHERE id=?", (speech_id,))


def update_speech(speech_id: str, name: str, content: str, trigger_phrase: str = "",
                  voice: str = "aura-2-thalia-en") -> dict | None:
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM speeches WHERE id=?", (speech_id,)).fetchone()
        if not existing:
            return None
        conn.execute(
            "UPDATE speeches SET name=?, trigger_phrase=?, content=?, voice=? WHERE id=?",
            (name, trigger_phrase, content, voice, speech_id),
        )
    return get_speech_by_id(speech_id)


def get_speech_by_id(speech_id: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM speeches WHERE id=?", (speech_id,)
        ).fetchone()
        return dict(row) if row else None


# ── Transcripts ───────────────────────────────────────────────────────────────

def save_transcript_session(messages: list, topics: list = None, student_name: str = "") -> str:
    session_id = str(uuid.uuid4())[:8]
    now = datetime.datetime.now()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO transcript_sessions (id, date, start_time, topics, student_name) VALUES (?,?,?,?,?)",
            (session_id, now.date().isoformat(), now.strftime("%H:%M"),
             json.dumps(topics or []), student_name or "")
        )
        for msg in messages:
            conn.execute(
                "INSERT INTO transcript_messages (id, session_id, role, content, timestamp) VALUES (?,?,?,?,?)",
                (str(uuid.uuid4())[:8], session_id,
                 msg.get("role", "user"), msg.get("content", ""),
                 msg.get("timestamp", now.isoformat()))
            )
    return session_id


def get_transcript_sessions():
    with get_db() as conn:
        sessions = conn.execute(
            "SELECT * FROM transcript_sessions ORDER BY date DESC, start_time DESC"
        ).fetchall()
        result = []
        for s in sessions:
            count = conn.execute(
                "SELECT COUNT(*) as c FROM transcript_messages WHERE session_id=?",
                (s["id"],)
            ).fetchone()["c"]
            result.append({
                "id": s["id"],
                "date": s["date"],
                "start_time": s["start_time"],
                "end_time": s["end_time"],
                "message_count": count,
                "topics": json.loads(s["topics"] or "[]"),
                "student_name": s["student_name"] or "Unknown student",
            })
        return result


def get_transcript_messages(session_id: str):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM transcript_messages WHERE session_id=? ORDER BY timestamp",
            (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]
