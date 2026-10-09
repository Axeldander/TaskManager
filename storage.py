"""SQLite persistence for the task manager. Storage only: no business rules.

Rules like role checks, status transitions and password hashing live in later layers.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from typing import Optional

from models import AuditAction, AuditEntry, Role, Session, Task, TaskStatus, User

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    email         TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'user'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    description      TEXT NOT NULL,
    assignment_date  TEXT NOT NULL,
    deadline         TEXT NOT NULL,
    status           TEXT NOT NULL
        CHECK (status IN ('todo', 'ongoing', 'done', 'failed', 'cancelled')),
    assigned_user_id TEXT NOT NULL REFERENCES users(id),
    picture          TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_assigned_user ON tasks(assigned_user_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id        TEXT PRIMARY KEY,
    actor_id  TEXT NOT NULL REFERENCES users(id),
    task_id   TEXT NOT NULL REFERENCES tasks(id),
    action    TEXT NOT NULL CHECK (action IN ('assign', 'reassign', 'edit')),
    field     TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_log(task_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
"""


class DuplicateUsernameError(Exception):
    """Raised when saving a user whose username already exists."""


def connect(path: str = "task_manager.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # off by default in SQLite
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _tx(conn: sqlite3.Connection, commit: bool):
    """Commit/rollback around a statement, or do nothing if a caller owns the transaction."""
    return conn if commit else nullcontext()


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Group several repository calls (made with commit=False) into one atomic unit."""
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


# --- datetime helpers: stored as ISO 8601 UTC text -------------------------

def _dt_to_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _text_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


# --- users -----------------------------------------------------------------

class UserRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, user: User) -> None:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO users (id, username, password_hash, email, role) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user.id, user.username, user.password_hash, user.email, user.role.value),
                )
        except sqlite3.IntegrityError as exc:
            if "users.username" in str(exc):
                raise DuplicateUsernameError(user.username) from exc
            raise

    def get_by_id(self, user_id: str) -> Optional[User]:
        row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._to_user(row) if row else None

    def get_by_username(self, username: str) -> Optional[User]:
        row = self._conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return self._to_user(row) if row else None

    def list_all(self) -> list[User]:
        rows = self._conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [self._to_user(r) for r in rows]

    def list_all(self) -> list[User]:
        rows = self._conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [self._to_user(r) for r in rows]

    @staticmethod
    def _to_user(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            email=row["email"],
            role=Role(row["role"]),
        )


# --- tasks -----------------------------------------------------------------

class TaskRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, task: Task, *, commit: bool = True) -> None:
        with _tx(self._conn, commit):
            self._conn.execute(
                "INSERT INTO tasks (id, title, description, assignment_date, deadline, "
                "status, assigned_user_id, picture) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task.id,
                    task.title,
                    task.description,
                    _dt_to_text(task.assignment_date),
                    _dt_to_text(task.deadline),
                    task.status.value,
                    task.assigned_user_id,
                    task.picture,
                ),
            )

    def update(self, task: Task, *, commit: bool = True) -> None:
        """Persist changes to an existing task. id and assignment_date are never rewritten."""
        with _tx(self._conn, commit):
            cur = self._conn.execute(
                "UPDATE tasks SET title = ?, description = ?, deadline = ?, status = ?, "
                "assigned_user_id = ?, picture = ? WHERE id = ?",
                (
                    task.title,
                    task.description,
                    _dt_to_text(task.deadline),
                    task.status.value,
                    task.assigned_user_id,
                    task.picture,
                    task.id,
                ),
            )
        if cur.rowcount == 0:
            raise KeyError(f"task not found: {task.id}")

    def get(self, task_id: str) -> Optional[Task]:
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._to_task(row) if row else None

    def fail_overdue(self, now: datetime, *, commit: bool = True) -> int:
        """Mark every todo/ongoing task whose deadline has passed as failed.

        Returns how many tasks were changed. Safe to call repeatedly.
        """
        with _tx(self._conn, commit):
            cur = self._conn.execute(
                "UPDATE tasks SET status = ? WHERE status IN (?, ?) AND deadline <= ?",
                (
                    TaskStatus.FAILED.value,
                    TaskStatus.TODO.value,
                    TaskStatus.ONGOING.value,
                    _dt_to_text(now),
                ),
            )
        return cur.rowcount

    def list_for_user(self, user_id: str) -> list[Task]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE assigned_user_id = ? ORDER BY deadline", (user_id,)
        ).fetchall()
        return [self._to_task(r) for r in rows]

    def list_assigned_by(self, admin_id: str) -> list[Task]:
        """Tasks whose most recent assignment (initial or reassignment) was made by this admin."""
        rows = self._conn.execute(
            "SELECT t.* FROM tasks t WHERE ("
            "  SELECT a.actor_id FROM audit_log a"
            "  WHERE a.task_id = t.id AND a.action IN ('assign', 'reassign')"
            "  ORDER BY a.timestamp DESC, a.rowid DESC LIMIT 1"
            ") = ? ORDER BY t.deadline",
            (admin_id,),
        ).fetchall()
        return [self._to_task(r) for r in rows]

    def list_assigned_by(self, admin_id: str) -> list[Task]:
        """Tasks this admin has assigned or reassigned (found through the audit log)."""
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE id IN ("
            "  SELECT task_id FROM audit_log WHERE actor_id = ? AND action IN ('assign', 'reassign')"
            ") ORDER BY deadline",
            (admin_id,),
        ).fetchall()
        return [self._to_task(r) for r in rows]

    @staticmethod
    def _to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            assignment_date=_text_to_dt(row["assignment_date"]),
            deadline=_text_to_dt(row["deadline"]),
            status=TaskStatus(row["status"]),
            assigned_user_id=row["assigned_user_id"],
            picture=row["picture"],
        )


# --- sessions --------------------------------------------------------------

class SessionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, session: Session) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    session.token_hash,
                    session.user_id,
                    _dt_to_text(session.created_at),
                    _dt_to_text(session.expires_at),
                ),
            )

    def get(self, token_hash: str) -> Optional[Session]:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if not row:
            return None
        return Session(
            token_hash=row["token_hash"],
            user_id=row["user_id"],
            created_at=_text_to_dt(row["created_at"]),
            expires_at=_text_to_dt(row["expires_at"]),
        )

    def delete(self, token_hash: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_expired(self, now: datetime) -> int:
        """Remove every session that has expired. Returns how many rows were deleted."""
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE expires_at <= ?", (_dt_to_text(now),)
            )
        return cur.rowcount


# --- audit log -------------------------------------------------------------

class AuditLogRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, entry: AuditEntry, *, commit: bool = True) -> None:
        with _tx(self._conn, commit):
            self._conn.execute(
                "INSERT INTO audit_log (id, actor_id, task_id, action, field, old_value, "
                "new_value, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.id,
                    entry.actor_id,
                    entry.task_id,
                    entry.action.value,
                    entry.field,
                    entry.old_value,
                    entry.new_value,
                    _dt_to_text(entry.timestamp),
                ),
            )

    def list_for_task(self, task_id: str) -> list[AuditEntry]:
        rows = self._conn.execute(
            "SELECT * FROM audit_log WHERE task_id = ? ORDER BY timestamp, rowid", (task_id,)
        ).fetchall()
        return [
            AuditEntry(
                id=r["id"],
                actor_id=r["actor_id"],
                task_id=r["task_id"],
                action=AuditAction(r["action"]),
                field=r["field"],
                old_value=r["old_value"],
                new_value=r["new_value"],
                timestamp=_text_to_dt(r["timestamp"]),
            )
            for r in rows
        ]
