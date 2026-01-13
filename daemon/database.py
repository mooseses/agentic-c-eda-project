import sqlite3
import threading
import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager

DEFAULT_DB_PATH = "/var/lib/agentic-c-eda/agentic-c-eda.db"
_local = threading.local()

class Database:
    

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_directory()
        self._init_schema()
        self._fix_permissions()

    def _ensure_directory(self):
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(db_dir, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        except PermissionError:
            pass

    def _fix_permissions(self):
        try:
            db_path = Path(self.db_path)
            for suffix in ['', '-shm', '-wal']:
                file_path = db_path.parent / (db_path.name + suffix)
                if file_path.exists():
                    os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | 
                             stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH)
        except PermissionError:
            pass

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(_local, 'conn') or _local.conn is None:
            _local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0
            )
            _local.conn.row_factory = sqlite3.Row
            _local.conn.execute("PRAGMA journal_mode=WAL")
            _local.conn.execute("PRAGMA synchronous=NORMAL")
        return _local.conn

    @contextmanager
    def _cursor(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    def _init_schema(self):
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    source_ip TEXT,
                    port INTEGER,
                    raw_event TEXT NOT NULL,
                    batch_id INTEGER
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_batch ON events(batch_id)")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    batch_id INTEGER NOT NULL,
                    event_count INTEGER NOT NULL,
                    verdict TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    reason TEXT,
                    threat_ips TEXT
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_decisions_timestamp ON decisions(timestamp)")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS flags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_ids TEXT,
                    severity TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    suggested_actions TEXT,
                    status TEXT DEFAULT 'pending'
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_flags_status ON flags(status)")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT
                )
            """)

    # =========================================================================
    # EVENT OPERATIONS
    # =========================================================================

    def insert_event(self, event_type: str, raw_event: str, 
                     source_ip: str = None, port: int = None,
                     batch_id: int = None) -> int:
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO events (timestamp, event_type, source_ip, port, raw_event, batch_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (datetime.now().isoformat(), event_type, source_ip, port, raw_event, batch_id))
            return cur.lastrowid

    def get_events(self, limit: int = 100, offset: int = 0,
                   since: datetime = None) -> list[dict]:
        with self._cursor() as cur:
            if since:
                cur.execute("""
                    SELECT * FROM events 
                    WHERE timestamp > ? 
                    ORDER BY id DESC LIMIT ? OFFSET ?
                """, (since.isoformat(), limit, offset))
            else:
                cur.execute("""
                    SELECT * FROM events 
                    ORDER BY id DESC LIMIT ? OFFSET ?
                """, (limit, offset))
            return [dict(row) for row in cur.fetchall()]

    def get_latest_event_id(self) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT MAX(id) FROM events")
            result = cur.fetchone()[0]
            return result if result else 0

    def insert_decision(self, batch_id: int, event_count: int,
                        verdict: str, confidence: float,
                        reason: str, threat_ips: list[str]) -> int:
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO decisions (timestamp, batch_id, event_count, verdict, confidence, reason, threat_ips)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                batch_id,
                event_count,
                verdict,
                confidence,
                reason,
                json.dumps(threat_ips)
            ))
            return cur.lastrowid

    def get_decisions(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._cursor() as cur:
            cur.execute("""
                SELECT * FROM decisions 
                ORDER BY id DESC LIMIT ? OFFSET ?
            """, (limit, offset))
            rows = [dict(row) for row in cur.fetchall()]
            for row in rows:
                row['threat_ips'] = json.loads(row['threat_ips']) if row['threat_ips'] else []
            return rows

    def get_latest_decision_id(self) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT MAX(id) FROM decisions")
            result = cur.fetchone()[0]
            return result if result else 0

    def get_config(self, key: str, default: str = None) -> str:
        with self._cursor() as cur:
            cur.execute("SELECT value FROM config WHERE key = ?", (key,))
            row = cur.fetchone()
            return row['value'] if row else default

    def set_config(self, key: str, value: str):
        with self._cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO config (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, value, datetime.now().isoformat()))

    def get_all_config(self) -> dict:
        with self._cursor() as cur:
            cur.execute("SELECT key, value FROM config")
            return {row['key']: row['value'] for row in cur.fetchall()}

    def get_stats(self) -> dict:
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM events")
            total_events = cur.fetchone()[0]

            hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
            cur.execute("SELECT COUNT(*) FROM events WHERE timestamp > ?", (hour_ago,))
            events_last_hour = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM decisions")
            total_decisions = cur.fetchone()[0]

            today = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
            cur.execute("""
                SELECT COUNT(*) FROM decisions 
                WHERE timestamp > ? AND verdict = 'BLOCK'
            """, (today,))
            blocks_today = cur.fetchone()[0]

            return {
                "total_events": total_events,
                "events_last_hour": events_last_hour,
                "total_decisions": total_decisions,
                "blocks_today": blocks_today
            }

    def cleanup_old_records(self, days: int = 7):
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._cursor() as cur:
            cur.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
            cur.execute("DELETE FROM decisions WHERE timestamp < ?", (cutoff,))

    def purge_all_events(self) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM events")
            count = cur.fetchone()[0]
            cur.execute("DELETE FROM events")
            return count

    def purge_all_decisions(self) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM decisions")
            count = cur.fetchone()[0]
            cur.execute("DELETE FROM decisions")
            return count

    def insert_flag(self, event_ids: list, severity: str, summary: str, 
                    suggested_actions: list = None) -> int:
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO flags (timestamp, event_ids, severity, summary, suggested_actions, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, (
                datetime.now().isoformat(),
                json.dumps(event_ids),
                severity,
                summary,
                json.dumps(suggested_actions or [])
            ))
            return cur.lastrowid

    def get_flags(self, status: str = None, limit: int = 50) -> list[dict]:
        with self._cursor() as cur:
            if status:
                cur.execute("""
                    SELECT * FROM flags WHERE status = ? 
                    ORDER BY id DESC LIMIT ?
                """, (status, limit))
            else:
                cur.execute("SELECT * FROM flags ORDER BY id DESC LIMIT ?", (limit,))
            rows = [dict(row) for row in cur.fetchall()]
            for row in rows:
                row['event_ids'] = json.loads(row['event_ids']) if row['event_ids'] else []
                row['suggested_actions'] = json.loads(row['suggested_actions']) if row['suggested_actions'] else []
            return rows

    def update_flag_status(self, flag_id: int, status: str):
        with self._cursor() as cur:
            cur.execute("UPDATE flags SET status = ? WHERE id = ?", (status, flag_id))

    def insert_chat_message(self, role: str, content: str, metadata: dict = None) -> int:
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO chat_messages (timestamp, role, content, metadata)
                VALUES (?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                role,
                content,
                json.dumps(metadata) if metadata else None
            ))
            return cur.lastrowid

    def get_chat_messages(self, limit: int = 50) -> list[dict]:
        with self._cursor() as cur:
            cur.execute("""
                SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?
            """, (limit,))
            rows = [dict(row) for row in cur.fetchall()]
            for row in rows:
                row['metadata'] = json.loads(row['metadata']) if row['metadata'] else {}
            return list(reversed(rows))

    def clear_chat_messages(self):
        with self._cursor() as cur:
            cur.execute("DELETE FROM chat_messages")

_db_instance = None
_db_lock = threading.Lock()

def get_db(db_path: str = None) -> Database:
    global _db_instance
    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                _db_instance = Database(db_path or DEFAULT_DB_PATH)
    return _db_instance
