"""Interactive command-line interface for the task manager.

Run:  python cli.py [--db PATH]

Times are shown and entered in your local time zone; they are stored as UTC.
"""

from __future__ import annotations

import argparse
import getpass
from datetime import datetime
from typing import Callable, Optional

from admin import AdminService
from auth import AuthService, InvalidCredentialsError, InvalidSessionError
from errors import InvalidTransitionError, NotFoundError, PermissionDeniedError
from models import ALLOWED_TRANSITIONS, Role, Task, TaskStatus, User
from receiver import ReceiverService
from storage import (
    AuditLogRepository,
    DuplicateUsernameError,
    SessionRepository,
    TaskRepository,
    UserRepository,
    connect,
    init_schema,
)

DEADLINE_FORMAT = "%Y-%m-%d %H:%M"
KNOWN_ERRORS = (
    InvalidCredentialsError,
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
    DuplicateUsernameError,
    ValueError,
)


# --- formatting helpers ----------------------------------------------------

def fmt_dt(value: datetime, seconds: bool = False) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S" if seconds else DEADLINE_FORMAT)


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def parse_deadline(text: str) -> datetime:
    try:
        return datetime.strptime(text.strip(), DEADLINE_FORMAT).astimezone()  # local time -> aware
    except ValueError:
        raise ValueError("deadline must look like 2026-12-31 17:30 (local time)") from None


def choose(items: list, prompt: str):
    """Ask for a number from a list. Returns the item, or None if the answer is blank."""
    raw = input(prompt).strip()
    if not raw:
        return None
    try:
        index = int(raw)
    except ValueError:
        raise ValueError("please enter a number from the list") from None
    if not 1 <= index <= len(items):
        raise ValueError("that number is not in the list")
    return items[index - 1]


def describe(exc: Exception) -> str:
    if isinstance(exc, DuplicateUsernameError):
        return "that username is already taken"
    return str(exc)


# --- the application -------------------------------------------------------

class App:
    def __init__(self, auth: AuthService, admin: AdminService, receiver: ReceiverService) -> None:
        self.auth = auth
        self.admin = admin
        self.receiver = receiver
        self.token: Optional[str] = None

    def run(self) -> None:
        print("Task Manager")
        try:
            while True:
                if self.token is None:
                    if not self.logged_out_menu():
                        break
                else:
                    self.logged_in_menu()
        except (EOFError, KeyboardInterrupt):
            print()
        print("Goodbye.")

    # --- menus -------------------------------------------------------------

    def logged_out_menu(self) -> bool:
        print("\n1) Log in\n2) Register\n0) Quit")
        choice = input("> ").strip()
        if choice == "1":
            self.attempt(self.log_in)
        elif choice == "2":
            self.attempt(self.register)
        elif choice == "0":
            return False
        else:
            print("Please choose one of the numbers above.")
        return True

    def logged_in_menu(self) -> None:
        try:
            actor = self.auth.authenticate(self.token)
        except InvalidSessionError:
            self.expire_session()
            return

        items = self.menu_items(actor)
        print(f"\n--- {actor.username} ({actor.role.value}) ---")
        for number, (label, _) in enumerate(items, 1):
            print(f"{number}) {label}")
        print("0) Log out")

        choice = input("> ").strip()
        if choice == "0":
            self.auth.logout(self.token)
            self.token = None
            print("Logged out.")
        elif choice.isdigit() and 1 <= int(choice) <= len(items):
            self.attempt(items[int(choice) - 1][1], actor)
        else:
            print("Please choose one of the numbers above.")

    def menu_items(self, actor: User) -> list[tuple[str, Callable[[User], None]]]:
        is_admin = actor.role is Role.ADMIN
        items = [
            ("Tasks I assigned or was assigned" if is_admin else "My tasks", self.show_tasks),
            ("Status summary", self.show_summary),
            ("Update a task's status", self.change_status),
            ("Cancel a task", self.cancel_task),
        ]
        if is_admin:
            items += [
                ("Create a task", self.create_task),
                ("Edit a task", self.edit_task),
                ("Reassign a task", self.reassign_task),
                ("Create a user", self.create_user),
                ("List users", self.list_users),
                ("View a task's audit log", self.show_audit_log),
            ]
        return items

    def attempt(self, action: Callable, *args) -> None:
        try:
            action(*args)
        except InvalidSessionError:
            self.expire_session()
        except KNOWN_ERRORS as exc:
            print(f"Error: {describe(exc)}")

    def expire_session(self) -> None:
        print("Your session has expired. Please log in again.")
        self.token = None

    # --- logged-out actions ------------------------------------------------

    def log_in(self) -> None:
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")
        self.token = self.auth.login(username, password)
        actor = self.auth.authenticate(self.token)
        print(f"Logged in as {actor.username} ({actor.role.value}).")

    def register(self) -> None:
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")
        email = input("Email: ").strip()
        role = input("Role (admin/user): ").strip().lower()
        self.auth.register(username, password, email, role)
        print("Account created. You can log in now.")

    # --- shared actions ----------------------------------------------------

    def names(self, actor: User) -> dict[str, str]:
        """user id -> username, for display."""
        if actor.role is Role.ADMIN:
            return {u.id: u.username for u in self.admin.list_users(actor)}
        return {actor.id: actor.username}

    def print_tasks(self, tasks: list[Task], names: dict[str, str]) -> None:
        print(f"{'#':>3}  {'Title':<28} {'Status':<10} {'Deadline':<16} Assigned to")
        for number, task in enumerate(tasks, 1):
            assignee = names.get(task.assigned_user_id, task.assigned_user_id[:8])
            print(
                f"{number:>3}  {clip(task.title, 28):<28} {task.status.value:<10} "
                f"{fmt_dt(task.deadline):<16} {assignee}"
            )

    def pick_task(self, actor: User) -> Optional[Task]:
        tasks = self.receiver.list_tasks(actor)
        if not tasks:
            print("No tasks.")
            return None
        self.print_tasks(tasks, self.names(actor))
        return choose(tasks, "Task number (blank to go back): ")

    def show_tasks(self, actor: User) -> None:
        tasks = self.receiver.list_tasks(actor)
        if not tasks:
            print("No tasks.")
            return
        names = self.names(actor)
        self.print_tasks(tasks, names)
        task = choose(tasks, "Number for details (blank to go back): ")
        if task:
            print(f"\nTitle:        {task.title}")
            print(f"Description:  {task.description or '(none)'}")
            print(f"Status:       {task.status.value}")
            print(f"Assigned to:  {names.get(task.assigned_user_id, task.assigned_user_id)}")
            print(f"Assigned on:  {fmt_dt(task.assignment_date)}")
            print(f"Deadline:     {fmt_dt(task.deadline)}")
            print(f"Id:           {task.id}")

    def show_summary(self, actor: User) -> None:
        counts = self.receiver.summary(actor)
        print("Tasks by status:")
        for status in TaskStatus:
            print(f"  {status.value:<10} {counts[status]}")

    def change_status(self, actor: User) -> None:
        task = self.pick_task(actor)
        if task is None:
            return
        options = [s for s in TaskStatus if s in ALLOWED_TRANSITIONS[task.status]]
        if not options:
            print(f"This task is {task.status.value}; its status can no longer change.")
            return
        print(f"'{task.title}' is {task.status.value}. Move it to:")
        for number, status in enumerate(options, 1):
            print(f"  {number}) {status.value}")
        new_status = choose(options, "Choice (blank to go back): ")
        if new_status is None:
            return
        updated = self.receiver.update_status(actor, task.id, new_status)
        print(f"'{updated.title}' is now {updated.status.value}.")

    def cancel_task(self, actor: User) -> None:
        task = self.pick_task(actor)
        if task is None:
            return
        if input(f"Cancel '{task.title}'? This cannot be undone. [y/N] ").strip().lower() != "y":
            print("Not cancelled.")
            return
        updated = self.receiver.cancel(actor, task.id)
        print(f"'{updated.title}' is now {updated.status.value}.")

    # --- admin actions -----------------------------------------------------

    def pick_user(self, actor: User, prompt: str) -> Optional[User]:
        users = self.admin.list_users(actor)
        for number, user in enumerate(users, 1):
            print(f"  {number}) {user.username} ({user.role.value})")
        return choose(users, prompt)

    def create_task(self, actor: User) -> None:
        title = input("Title: ")
        description = input("Description: ")
        deadline = parse_deadline(input("Deadline (YYYY-MM-DD HH:MM, local time): "))
        print("Assign to:")
        assignee = self.pick_user(actor, "User number (blank to cancel): ")
        if assignee is None:
            print("Cancelled.")
            return
        task = self.admin.create_task(actor, title, description, deadline, assignee.id)
        print(f"Created '{task.title}', assigned to {assignee.username}.")

    def edit_task(self, actor: User) -> None:
        task = self.pick_task(actor)
        if task is None:
            return
        print("Leave a field blank to keep its current value.")
        title = input(f"Title [{task.title}]: ").strip()
        description = input(f"Description [{clip(task.description, 40)}]: ")
        deadline_text = input(f"Deadline [{fmt_dt(task.deadline)}]: ").strip()
        self.admin.edit_task(
            actor,
            task.id,
            title=title or None,
            description=description or None,
            deadline=parse_deadline(deadline_text) if deadline_text else None,
        )
        print("Task updated.")

    def reassign_task(self, actor: User) -> None:
        task = self.pick_task(actor)
        if task is None:
            return
        print("Reassign to:")
        assignee = self.pick_user(actor, "User number (blank to cancel): ")
        if assignee is None:
            return
        self.admin.reassign_task(actor, task.id, assignee.id)
        print(f"'{task.title}' is assigned to {assignee.username}.")

    def create_user(self, actor: User) -> None:
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")
        email = input("Email: ").strip()
        role = input("Role (admin/user): ").strip().lower()
        self.admin.create_user(actor, username, password, email, role)
        print(f"User '{username}' created.")

    def list_users(self, actor: User) -> None:
        for user in self.admin.list_users(actor):
            print(f"  {user.username:<16} {user.role.value:<6} {user.email}")

    def show_audit_log(self, actor: User) -> None:
        task = self.pick_task(actor)
        if task is None:
            return
        names = self.names(actor)
        entries = self.admin.get_audit_log(actor, task.id)
        if not entries:
            print("No changes logged for this task.")
        for e in entries:
            who = names.get(e.actor_id, e.actor_id[:8])
            print(
                f"{fmt_dt(e.timestamp, seconds=True)}  {who:<12} {e.action.value:<9} "
                f"{e.field}: {self.audit_value(e.field, e.old_value, names)} -> "
                f"{self.audit_value(e.field, e.new_value, names)}"
            )

    @staticmethod
    def audit_value(field_name: str, value: Optional[str], names: dict[str, str]) -> str:
        if value is None:
            return "(none)"
        if field_name == "assigned_user_id":
            return names.get(value, value[:8])
        if field_name == "deadline":
            return fmt_dt(datetime.fromisoformat(value))
        return clip(value, 40)


def build_app(db_path: str) -> App:
    conn = connect(db_path)
    init_schema(conn)
    users, tasks = UserRepository(conn), TaskRepository(conn)
    audit, sessions = AuditLogRepository(conn), SessionRepository(conn)
    auth = AuthService(users, sessions)
    return App(auth, AdminService(conn, users, tasks, audit, auth), ReceiverService(tasks))


def main() -> None:
    parser = argparse.ArgumentParser(description="Task manager command-line interface")
    parser.add_argument("--db", default="task_manager.db", help="SQLite file (default: task_manager.db)")
    build_app(parser.parse_args().db).run()


if __name__ == "__main__":
    main()
