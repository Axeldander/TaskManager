"""
Receiver interface module.

Implements FR-8 (update status), FR-9 (progress tracker), and FR-10
(cancel task). Also implements the lazy "failed" check (Section 9 open
question: failed status is computed on read, based on whether the
deadline has passed, rather than via a background job).
"""

from collections import Counter
from datetime import datetime
from typing import Dict, Optional

from auth import Session
from access import can_access
from models import Task, Status, ALLOWED_TRANSITIONS, TERMINAL_STATUSES
import storage


class ReceiverError(Exception):
    pass


def _apply_lazy_failure(task: Task) -> Task:
    """
    If a task's deadline has passed and it is not already in a terminal
    state, mark it as FAILED. This is checked whenever a task is read,
    rather than via a background scheduler, keeping the system simple.
    """
    if task.status not in TERMINAL_STATUSES:
        deadline_dt = datetime.fromisoformat(task.deadline)
        if datetime.now() > deadline_dt:
            task.status = Status.FAILED
            task.log("auto-failed", "system", "deadline passed")
    return task


def _get_task_checked(session: Session, task_id: str) -> Task:
    tasks = storage.load_tasks()
    task = tasks.get(task_id)
    if task is None:
        raise ReceiverError("task not found")
    task = _apply_lazy_failure(task)
    if not can_access(session, task):
        raise ReceiverError("access denied")
    return task, tasks


def update_status(session: Session, task_id: str, new_status: str) -> Task:
    """
    FR-8: Update a task's status. Only the assigned user (or an admin)
    may do this, and only along the allowed transition graph
    (models.ALLOWED_TRANSITIONS).
    """
    task, tasks = _get_task_checked(session, task_id)

    try:
        new_status_enum = Status(new_status)
    except ValueError:
        raise ReceiverError(f"invalid status: {new_status}")

    if new_status_enum not in ALLOWED_TRANSITIONS.get(task.status, set()):
        raise ReceiverError(
            f"cannot transition from {task.status.value} "
            f"to {new_status_enum.value}"
        )

    old_status = task.status
    task.status = new_status_enum
    task.log("status_changed", session.username,
              f"{old_status.value} -> {new_status_enum.value}")

    tasks[task_id] = task
    storage.save_tasks(tasks)
    return task


def get_progress(session: Session, username: Optional[str] = None) -> Dict[str, int]:
    """
    FR-9: Return a count of tasks by status for a user.

    Regular users always get their own progress. Admins may optionally
    pass `username` to view another user's progress; if omitted, an
    admin gets an aggregate across all tasks.
    """
    tasks = storage.load_tasks()
    tasks = {tid: _apply_lazy_failure(t) for tid, t in tasks.items()}
    storage.save_tasks(tasks)  # persist any lazy-failure updates

    target_user = username if session.is_admin() and username else session.username
    if not session.is_admin() and username and username != session.username:
        raise ReceiverError("access denied")

    if session.is_admin() and username is None:
        relevant = tasks.values()
    else:
        relevant = [t for t in tasks.values() if t.assigned_user == target_user]

    counts = Counter(t.status.value for t in relevant)
    # Ensure every status key is present, even if zero.
    return {s.value: counts.get(s.value, 0) for s in Status}


def get_task_history(session: Session, task_id: str) -> list:
    """Return a task's audit log (oldest first). Same access rules as viewing the task."""
    task, _ = _get_task_checked(session, task_id)
    return list(task.history)


def list_tasks(session: Session):
    """
    Return every task this session is allowed to see (respecting the
    access model): all tasks for an admin, only assigned tasks for a
    regular user. Applies the lazy-failure check on each task read.
    """
    tasks = storage.load_tasks()
    tasks = {tid: _apply_lazy_failure(t) for tid, t in tasks.items()}
    storage.save_tasks(tasks)
    return [t for t in tasks.values() if can_access(session, t)]


def cancel_task(session: Session, task_id: str, reason: Optional[str] = None) -> Task:
    """
    FR-10: Cancel a task. Both the assigned user and an admin may cancel
    (documented design decision — restrict to admin-only here if your
    team decides otherwise).
    """
    task, tasks = _get_task_checked(session, task_id)

    if task.status not in ALLOWED_TRANSITIONS or \
            Status.CANCELLED not in ALLOWED_TRANSITIONS[task.status]:
        raise ReceiverError(f"cannot cancel a task in status {task.status.value}")

    task.status = Status.CANCELLED
    task.log("cancelled", session.username, reason or "")

    tasks[task_id] = task
    storage.save_tasks(tasks)
    return task
