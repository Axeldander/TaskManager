"""Task actions shared by users and admins: list, update status, cancel, summary.

The `actor` is the User returned by AuthService.authenticate(token).

- A user sees and acts on the tasks assigned to them.
- An admin sees the tasks they have assigned (plus any assigned to them) and may act
  on any task by id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from errors import InvalidTransitionError, NotFoundError, PermissionDeniedError
from models import ALLOWED_TRANSITIONS, Role, Task, TaskStatus, User
from storage import TaskRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReceiverService:
    def __init__(
        self, tasks: TaskRepository, clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self._tasks = tasks
        self._clock = clock

    def list_tasks(self, actor: User) -> list[Task]:
        """The tasks this actor can see, soonest deadline first."""
        self._tasks.fail_overdue(self._clock())
        visible = {t.id: t for t in self._tasks.list_for_user(actor.id)}
        if actor.role is Role.ADMIN:
            visible.update({t.id: t for t in self._tasks.list_assigned_by(actor.id)})
        return sorted(visible.values(), key=lambda t: t.deadline)

    def update_status(self, actor: User, task_id: str, new_status: TaskStatus | str) -> Task:
        """Move a task to a new status, following the fixed rules."""
        self._tasks.fail_overdue(self._clock())
        task = self._get_task(task_id)
        self._require_can_act(actor, task)
        return self._transition(task, TaskStatus(new_status))

    def cancel(self, actor: User, task_id: str) -> Task:
        """Cancel a task. Allowed for the assigned user and for any admin."""
        self._tasks.fail_overdue(self._clock())
        task = self._get_task(task_id)
        self._require_can_act(actor, task)
        return self._transition(task, TaskStatus.CANCELLED)

    def summary(self, actor: User) -> dict[TaskStatus, int]:
        """Count the tasks the actor can see by status (all statuses present, zero if none)."""
        counts = {status: 0 for status in TaskStatus}
        for task in self.list_tasks(actor):
            counts[task.status] += 1
        return counts

    def _get_task(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise NotFoundError(f"task not found: {task_id}")
        return task

    @staticmethod
    def _require_can_act(actor: User, task: Task) -> None:
        if actor.role is not Role.ADMIN and task.assigned_user_id != actor.id:
            raise PermissionDeniedError("task is not assigned to you")

    def _transition(self, task: Task, new_status: TaskStatus) -> Task:
        if new_status not in ALLOWED_TRANSITIONS[task.status]:
            raise InvalidTransitionError(
                f"cannot move task from {task.status.value} to {new_status.value}"
            )
        task.status = new_status
        self._tasks.update(task)
        return task
