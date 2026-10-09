"""Admin actions. Every method checks the acting user's role before doing anything.

The `actor` is the User returned by AuthService.authenticate(token).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional

from auth import AuthService
from errors import NotFoundError, PermissionDeniedError
from models import AuditAction, AuditEntry, Role, Task, User
from storage import (
    AuditLogRepository,
    TaskRepository,
    UserRepository,
    transaction,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_admin(actor: User) -> None:
    if actor.role is not Role.ADMIN:
        raise PermissionDeniedError("admin role required")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


def _dt_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class AdminService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        users: UserRepository,
        tasks: TaskRepository,
        audit: AuditLogRepository,
        auth: AuthService,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._conn = conn
        self._users = users
        self._tasks = tasks
        self._audit = audit
        self._auth = auth
        self._clock = clock

    # --- users -------------------------------------------------------------

    def create_user(
        self, actor: User, username: str, password: str, email: str, role: Role | str
    ) -> User:
        _require_admin(actor)
        return self._auth.register(username, password, email, role)

    def list_users(self, actor: User) -> list[User]:
        _require_admin(actor)
        return self._users.list_all()

    # --- tasks -------------------------------------------------------------

    def create_task(
        self,
        actor: User,
        title: str,
        description: str,
        deadline: datetime,
        assigned_user_id: str,
    ) -> Task:
        _require_admin(actor)
        if not title.strip():
            raise ValueError("title is required")
        _require_aware(deadline, "deadline")
        now = self._clock()
        if deadline <= now:
            raise ValueError("deadline must be in the future")
        self._require_user_exists(assigned_user_id)

        task = Task(
            title=title.strip(),
            description=description,
            deadline=deadline,
            assigned_user_id=assigned_user_id,
            assignment_date=now,
        )
        with transaction(self._conn):
            self._tasks.add(task, commit=False)
            self._log(actor, task, AuditAction.ASSIGN, "assigned_user_id", None, assigned_user_id)
        return task

    def reassign_task(self, actor: User, task_id: str, new_user_id: str) -> Task:
        _require_admin(actor)
        self._tasks.fail_overdue(self._clock())
        task = self._get_task(task_id)
        self._require_user_exists(new_user_id)
        if new_user_id == task.assigned_user_id:
            return task  # nothing changes, nothing to log

        old_user_id = task.assigned_user_id
        task.assigned_user_id = new_user_id
        with transaction(self._conn):
            self._tasks.update(task, commit=False)
            self._log(actor, task, AuditAction.REASSIGN, "assigned_user_id", old_user_id, new_user_id)
        return task

    def edit_task(
        self,
        actor: User,
        task_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        deadline: Optional[datetime] = None,
    ) -> Task:
        """Edit title, description and/or deadline. Fields left as None are unchanged."""
        _require_admin(actor)
        self._tasks.fail_overdue(self._clock())
        task = self._get_task(task_id)

        if title is not None and not title.strip():
            raise ValueError("title cannot be empty")
        if deadline is not None:
            _require_aware(deadline, "deadline")
            if deadline <= self._clock():
                raise ValueError("deadline must be in the future")
            if deadline <= task.assignment_date:
                raise ValueError("deadline must be after assignment_date")

        # (field, old text, new text, new value) for each field that actually changes
        changes = []
        if title is not None and title.strip() != task.title:
            changes.append(("title", task.title, title.strip(), title.strip()))
        if description is not None and description != task.description:
            changes.append(("description", task.description, description, description))
        if deadline is not None and deadline != task.deadline:
            changes.append(("deadline", _dt_text(task.deadline), _dt_text(deadline), deadline))
        if not changes:
            return task

        for name, _old, _new, value in changes:
            setattr(task, name, value)
        with transaction(self._conn):
            self._tasks.update(task, commit=False)
            for name, old, new, _value in changes:
                self._log(actor, task, AuditAction.EDIT, name, old, new)
        return task

    def get_audit_log(self, actor: User, task_id: str) -> list[AuditEntry]:
        """The recorded changes to one task, oldest first."""
        _require_admin(actor)
        self._get_task(task_id)
        return self._audit.list_for_task(task_id)

    # --- reading -----------------------------------------------------------

    def list_users(self, actor: User) -> list[User]:
        _require_admin(actor)
        return self._users.list_all()

    def get_audit_log(self, actor: User, task_id: str) -> list[AuditEntry]:
        _require_admin(actor)
        self._get_task(task_id)  # raises NotFoundError for an unknown task
        return self._audit.list_for_task(task_id)

    # --- helpers -----------------------------------------------------------

    def _get_task(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise NotFoundError(f"task not found: {task_id}")
        return task

    def _require_user_exists(self, user_id: str) -> None:
        if self._users.get_by_id(user_id) is None:
            raise NotFoundError(f"user not found: {user_id}")

    def _log(
        self,
        actor: User,
        task: Task,
        action: AuditAction,
        field_name: str,
        old: Optional[str],
        new: Optional[str],
    ) -> None:
        self._audit.add(
            AuditEntry(
                actor_id=actor.id,
                task_id=task.id,
                action=action,
                field=field_name,
                old_value=old,
                new_value=new,
                timestamp=self._clock(),
            ),
            commit=False,
        )
