"""Data models (and the fixed status-transition table) for the task manager.

No storage, auth, or workflow logic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


class Role(str, Enum):
    ADMIN = "admin"
    USER = "user"


class TaskStatus(str, Enum):
    TODO = "todo"
    ONGOING = "ongoing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class User:
    username: str  # must be unique (enforced where users are stored, not here)
    password_hash: str  # hashed before it ever reaches this model; never a plain password
    email: str
    role: Role
    id: str = field(default_factory=_new_id)

    def __repr__(self) -> str:
        # Keep the hash out of logs and debug output.
        return f"User(id={self.id!r}, username={self.username!r}, role={self.role.value!r})"


@dataclass
class Task:
    title: str
    description: str
    deadline: datetime
    assigned_user_id: str  # exactly one user
    id: str = field(default_factory=_new_id)
    assignment_date: datetime = field(default_factory=_now)  # set on creation, immutable
    status: TaskStatus = TaskStatus.TODO
    picture: Optional[str] = None  # optional; representation still open (see notes)

    def __post_init__(self) -> None:
        if self.deadline <= self.assignment_date:
            raise ValueError("deadline must be after assignment_date")

    def __setattr__(self, name: str, value) -> None:
        # assignment_date can be set once (at construction) and never changed after.
        if name == "assignment_date" and "assignment_date" in self.__dict__:
            raise AttributeError("assignment_date cannot be changed after creation")
        super().__setattr__(name, value)


@dataclass
class Session:
    token_hash: str  # SHA-256 of the token handed to the client; the raw token is never stored
    user_id: str
    expires_at: datetime
    created_at: datetime = field(default_factory=_now)


class AuditAction(str, Enum):
    ASSIGN = "assign"
    REASSIGN = "reassign"
    EDIT = "edit"


@dataclass
class AuditEntry:
    """One logged change to a task: who did it, what changed, when."""

    actor_id: str
    task_id: str
    action: AuditAction
    field: str
    old_value: Optional[str]
    new_value: Optional[str]
    id: str = field(default_factory=_new_id)
    timestamp: datetime = field(default_factory=_now)


# Fixed status rules from the spec. "failed" is deliberately unreachable here:
# it is only ever set automatically when a deadline passes.
ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.TODO: frozenset({TaskStatus.ONGOING, TaskStatus.CANCELLED}),
    TaskStatus.ONGOING: frozenset({TaskStatus.DONE, TaskStatus.CANCELLED}),
    TaskStatus.DONE: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}
