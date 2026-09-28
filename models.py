"""
Data models for the Task Manager system.

Defines the core data structures used throughout the application:
- Status: the allowed lifecycle states of a Task
- User: a registered account (admin or regular user)
- Task: a unit of work assigned from an admin to a single user
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class Status(str, Enum):
    TODO = "todo"
    ONGOING = "ongoing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Role(str, Enum):
    ADMIN = "admin"
    USER = "user"


# Terminal statuses cannot transition to any other status.
TERMINAL_STATUSES = {Status.DONE, Status.FAILED, Status.CANCELLED}

# Explicit allowed transition graph (old_status -> set of allowed new_status).
# This encodes the open design decision from the spec sheet (Section 9):
# tasks move forward todo -> ongoing -> done, any active task can be
# cancelled, and any active task can be marked failed (typically because
# its deadline has passed).
ALLOWED_TRANSITIONS = {
    Status.TODO: {Status.ONGOING, Status.CANCELLED, Status.FAILED},
    Status.ONGOING: {Status.DONE, Status.CANCELLED, Status.FAILED},
    Status.DONE: set(),
    Status.FAILED: set(),
    Status.CANCELLED: set(),
}


@dataclass
class User:
    id: str
    username: str
    password_hash: str
    salt: str
    email: str
    role: Role

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "User":
        d = dict(d)
        d["role"] = Role(d["role"])
        return User(**d)


@dataclass
class Task:
    id: str
    title: str
    description: str
    assignment_date: str  # ISO timestamp, set once at creation, immutable
    deadline: str  # ISO timestamp
    status: Status
    assigned_user: str  # username of the single assignee
    created_by: str  # username of the admin who created it
    picture: Optional[str] = None  # path or reference to an image
    history: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Task":
        d = dict(d)
        d["status"] = Status(d["status"])
        return Task(**d)

    def log(self, action: str, actor: str, details: str = ""):
        """Append an entry to the task's audit history (NFR-3)."""
        self.history.append({
            "action": action,
            "actor": actor,
            "details": details,
            "timestamp": datetime.now().isoformat(),
        })
