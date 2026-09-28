"""
Admin interface module.

Implements FR-3 (create user), FR-4 (create task), FR-5 (assign/reassign
task), and FR-6 (edit task). Every function here requires an admin
Session and will refuse to run otherwise (NFR-2).
"""

import os
from datetime import datetime
from typing import Optional, List, Dict

from auth import Session, AuthError, register
from models import Task, Status
import storage


class AdminError(Exception):
    pass


ALLOWED_PICTURE_EXTS = (".png", ".gif", ".jpg", ".jpeg")


def _require_admin(session: Session):
    if not session.is_admin():
        raise AdminError("only an admin may perform this action")


def _validate_picture(picture: Optional[str]) -> Optional[str]:
    """A picture must be a png/gif/jpg file that exists. None/'' means no picture."""
    if picture in (None, ""):
        return None
    if os.path.splitext(picture)[1].lower() not in ALLOWED_PICTURE_EXTS:
        raise AdminError("picture must be a .png, .gif, .jpg or .jpeg file")
    if not os.path.isfile(storage.resolve_picture_path(picture)):
        raise AdminError(f"picture file not found: {picture}")
    return picture


def list_users(admin_session: Session) -> List[Dict[str, str]]:
    """Admin-only: list all users (used e.g. for the 'assign to' dropdown)."""
    _require_admin(admin_session)
    users = storage.load_users().values()
    return sorted(
        ({"username": u.username, "email": u.email, "role": u.role.value}
         for u in users),
        key=lambda d: d["username"].lower(),
    )


def create_user(admin_session: Session, username: str, password: str,
                 email: str, role: str) -> Session:
    """FR-3: Admin creates a new user account (reuses register()'s rules)."""
    _require_admin(admin_session)
    try:
        return register(username, password, email, role)
    except AuthError as e:
        raise AdminError(str(e))


def create_task(admin_session: Session, title: str, description: str,
                 deadline: str, assigned_user: str,
                 picture: Optional[str] = None) -> Task:
    """
    FR-4: Create a new task.

    `deadline` must be an ISO-format datetime string and must be in the
    future. `assigned_user` must be an existing username.
    """
    _require_admin(admin_session)

    users = storage.load_users()
    if assigned_user not in users:
        raise AdminError(f"no such user: {assigned_user}")
    picture = _validate_picture(picture)

    try:
        deadline_dt = datetime.fromisoformat(deadline)
    except ValueError:
        raise AdminError("deadline must be an ISO-format datetime string")

    if deadline_dt <= datetime.now():
        raise AdminError("deadline must be in the future")

    tasks = storage.load_tasks()
    task = Task(
        id=storage.new_id(),
        title=title,
        description=description,
        assignment_date=datetime.now().isoformat(),
        deadline=deadline,
        status=Status.TODO,
        assigned_user=assigned_user,
        created_by=admin_session.username,
        picture=picture,
    )
    task.log("created", admin_session.username,
              f"assigned to {assigned_user}")
    tasks[task.id] = task
    storage.save_tasks(tasks)
    return task


def assign_task(admin_session: Session, task_id: str, user_id: str) -> Task:
    """
    FR-5: Assign or reassign a task to a single user.

    Logs the previous and new assignee. Reassigning resets status to
    TODO (documented design decision — change here if your team decides
    progress should be preserved instead).
    """
    _require_admin(admin_session)

    users = storage.load_users()
    if user_id not in users:
        raise AdminError(f"no such user: {user_id}")

    tasks = storage.load_tasks()
    task = tasks.get(task_id)
    if task is None:
        raise AdminError("task not found")

    previous = task.assigned_user
    task.assigned_user = user_id
    task.status = Status.TODO
    task.log("reassigned", admin_session.username,
              f"from {previous} to {user_id}")

    tasks[task_id] = task
    storage.save_tasks(tasks)
    return task


def edit_task(admin_session: Session, task_id: str, **fields) -> Task:
    """
    FR-6: Edit a task's title, description, deadline, or picture.

    Status and assignment_date cannot be changed through this function —
    use assign_task() for reassignment and update_status() (receiver.py)
    for status changes.
    """
    _require_admin(admin_session)

    disallowed = {"status", "assignment_date", "id",
                  "assigned_user", "created_by", "history"}
    bad_fields = set(fields) & disallowed
    if bad_fields:
        raise AdminError(f"cannot edit fields: {sorted(bad_fields)}")

    tasks = storage.load_tasks()
    task = tasks.get(task_id)
    if task is None:
        raise AdminError("task not found")

    if "picture" in fields:
        fields["picture"] = _validate_picture(fields["picture"])

    if "deadline" in fields:
        try:
            deadline_dt = datetime.fromisoformat(fields["deadline"])
        except ValueError:
            raise AdminError("deadline must be an ISO-format datetime string")
        if deadline_dt <= datetime.now():
            raise AdminError("deadline must be in the future")

    changed = []
    for key, value in fields.items():
        setattr(task, key, value)
        changed.append(key)

    task.log("edited", admin_session.username,
              f"changed fields: {changed}")

    tasks[task_id] = task
    storage.save_tasks(tasks)
    return task
