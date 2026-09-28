"""
Access control module.

Implements FR-7 and NFR-2: every task-modifying (or task-viewing) action
must be checked against this access model before it proceeds.

Rule: admins can access all tasks; a regular user can only access tasks
assigned to them.
"""

from auth import Session
from models import Task


def can_access(session: Session, task: Task, action: str = "view") -> bool:
    """
    Return True if `session`'s user is allowed to perform `action` on
    `task`. `action` is currently informational (e.g. 'view', 'edit',
    'cancel') but kept as a parameter so finer-grained rules can be added
    later without changing every call site.
    """
    if session.is_admin():
        return True
    return task.assigned_user == session.username
