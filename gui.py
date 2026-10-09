"""Desktop interface (Tkinter) for the task manager.

Run:  python gui.py [--db PATH]

The window only calls the service layer (auth.py, admin.py, receiver.py). Those services
re-check the user's role on every action, so hiding admin buttons from regular users is a
convenience: the permissions do not depend on it.

Times are shown and entered in your local time zone; they are stored as UTC.
"""

from __future__ import annotations

import argparse
import functools
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Optional

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

KNOWN_ERRORS = (
    InvalidCredentialsError,
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
    DuplicateUsernameError,
    ValueError,
)

REFRESH_MS = 30_000  # how often the task list reloads by itself

ERROR_COLOR = "#b91c1c"
SUCCESS_COLOR = "#15803d"
MUTED_COLOR = "#6b7280"

STATUS_LABELS = {
    TaskStatus.TODO: "To do",
    TaskStatus.ONGOING: "Ongoing",
    TaskStatus.DONE: "Done",
    TaskStatus.FAILED: "Failed",
    TaskStatus.CANCELLED: "Cancelled",
}
STATUS_COLORS = {
    TaskStatus.TODO: "#475569",
    TaskStatus.ONGOING: "#1d4ed8",
    TaskStatus.DONE: "#15803d",
    TaskStatus.FAILED: "#b91c1c",
    TaskStatus.CANCELLED: "#6b7280",
}
LABEL_TO_STATUS = {label: status for status, label in STATUS_LABELS.items()}
ALL_TASKS = "All tasks"

ROLE_CHOICES = {"Regular user": Role.USER, "Administrator": Role.ADMIN}
ACTION_LABELS = {"assign": "Assigned", "reassign": "Reassigned", "edit": "Edited"}
FIELD_LABELS = {
    "assigned_user_id": "Assigned to",
    "title": "Title",
    "description": "Description",
    "deadline": "Deadline",
}


# --- small helpers ---------------------------------------------------------

def friendly(exc: Exception) -> str:
    """A message suitable for showing to a person."""
    if isinstance(exc, DuplicateUsernameError):
        text = "that username is already taken"
    else:
        text = str(exc)
    return text[:1].upper() + text[1:]


def fmt_dt(value: datetime, seconds: bool = False) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def user_label(user: User) -> str:
    return f"{user.username} ({user.role.value})"


def audit_value(field_name: str, value: Optional[str], names: dict[str, str]) -> str:
    if value is None:
        return "(none)"
    if field_name == "assigned_user_id":
        return names.get(value, value[:8])
    if field_name == "deadline":
        return fmt_dt(datetime.fromisoformat(value))
    return clip(value, 40)


def make_tree(parent, columns: list[tuple[str, str, int]], height: int = 10) -> ttk.Treeview:
    """A table with a scrollbar. `columns` is a list of (key, heading, width)."""
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(
        frame,
        columns=[key for key, _, _ in columns],
        show="headings",
        selectmode="browse",
        height=height,
    )
    for key, heading, width in columns:
        tree.heading(key, text=heading)
        tree.column(key, width=width, anchor="w")
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    return tree


def guarded(method):
    """Run a button action: expired sessions go back to login, known errors become a message."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except InvalidSessionError:
            self.app.session_expired()
        except KNOWN_ERRORS as exc:
            messagebox.showerror("That didn't work", friendly(exc), parent=self.app)
            self.reload_quietly()  # the screen may be out of date, which is often why it failed
        return None

    return wrapper


# --- deadline picker -------------------------------------------------------

class DeadlineField(ttk.Frame):
    """Year / month / day / hour / minute spin boxes (local time)."""

    # name, lowest, highest, digits, text shown after the box
    PARTS = [
        ("year", 2000, 2200, 4, " - "),
        ("month", 1, 12, 2, " - "),
        ("day", 1, 31, 2, "   at   "),
        ("hour", 0, 23, 2, " : "),
        ("minute", 0, 59, 2, ""),
    ]

    def __init__(self, parent, initial: datetime) -> None:
        super().__init__(parent)
        row = ttk.Frame(self)
        row.pack(anchor="w")
        self.vars: dict[str, tk.StringVar] = {}
        for name, low, high, digits, after in self.PARTS:
            var = tk.StringVar()
            self.vars[name] = var
            ttk.Spinbox(
                row, from_=low, to=high, width=digits + 1, wrap=True,
                textvariable=var, format=f"%0{digits}.0f",
            ).pack(side="left")
            if after:
                ttk.Label(row, text=after).pack(side="left")
        ttk.Label(
            self, text="year - month - day  at  hour : minute  (your local time)",
            foreground=MUTED_COLOR,
        ).pack(anchor="w", pady=(2, 0))
        self.set(initial)

    def set(self, value: datetime) -> None:
        for name, _, _, digits, _ in self.PARTS:
            self.vars[name].set(str(getattr(value, name)).zfill(digits))

    def get(self) -> datetime:
        try:
            parts = {name: int(self.vars[name].get()) for name in self.vars}
        except ValueError:
            raise ValueError("the deadline can only contain numbers") from None
        try:
            return datetime(**parts).astimezone()  # naive -> treated as local time
        except ValueError:
            raise ValueError("that date does not exist") from None


# --- dialogs ---------------------------------------------------------------

class FormDialog(tk.Toplevel):
    """A pop-up form. Errors appear inside it, so nothing the user typed is lost."""

    close_on_success = True

    def __init__(
        self, app: "App", title: str, submit_label: Optional[str] = "OK", cancel_label: str = "Cancel"
    ) -> None:
        super().__init__(app)
        self.app = app
        self.title(title)
        self.transient(app)
        self.resizable(False, False)
        self.result = None

        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        self.top = ttk.Frame(body)
        self.top.pack(fill="x")
        self.form = ttk.Frame(body)
        self.form.pack(fill="x")
        self.form.columnconfigure(1, weight=1)

        self.error = tk.StringVar()
        self.info = tk.StringVar()
        ttk.Label(body, textvariable=self.error, foreground=ERROR_COLOR,
                  wraplength=420, justify="left").pack(fill="x", pady=(8, 0))
        ttk.Label(body, textvariable=self.info, foreground=SUCCESS_COLOR,
                  wraplength=420, justify="left").pack(fill="x")

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text=cancel_label, command=self.destroy).pack(side="right")
        if submit_label:
            ttk.Button(buttons, text=submit_label, command=self.submit).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _event: self.destroy())

    def add_field(self, row: int, label: str, widget) -> None:
        ttk.Label(self.form, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=4)
        widget.grid(row=row, column=1, sticky="we", pady=4)

    def submit(self) -> None:
        self.error.set("")
        self.info.set("")
        try:
            self.result = self.perform()
            if not self.close_on_success:
                self.on_success()
        except InvalidSessionError:
            self.destroy()
            self.app.session_expired()
        except KNOWN_ERRORS as exc:
            self.error.set(friendly(exc))
        else:
            if self.close_on_success:
                self.destroy()

    def perform(self):
        """Do the work. Return something truthy on success, raise a known error on failure."""
        raise NotImplementedError

    def on_success(self) -> None:
        """For dialogs that stay open after a successful submit."""


class RegisterDialog(FormDialog):
    def __init__(self, app: "App") -> None:
        super().__init__(app, "Create account", "Create account")
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.confirm = tk.StringVar()
        self.email = tk.StringVar()
        first = ttk.Entry(self.form, textvariable=self.username, width=30)
        self.add_field(0, "Username", first)
        self.add_field(1, "Password", ttk.Entry(self.form, textvariable=self.password, show="•", width=30))
        self.add_field(2, "Repeat password", ttk.Entry(self.form, textvariable=self.confirm, show="•", width=30))
        self.add_field(3, "Email", ttk.Entry(self.form, textvariable=self.email, width=30))
        self.role = ttk.Combobox(self.form, state="readonly", width=27, values=list(ROLE_CHOICES))
        self.role.set("Regular user")
        self.add_field(4, "Account type", self.role)
        first.focus_set()

    def perform(self):
        if self.password.get() != self.confirm.get():
            raise ValueError("the two passwords do not match")
        username = self.username.get().strip()
        self.app.auth.register(username, self.password.get(), self.email.get(), ROLE_CHOICES[self.role.get()])
        return username


class TaskDialog(FormDialog):
    """Create a task (task=None) or edit an existing one."""

    def __init__(self, app: "App", users: list[User], task: Optional[Task] = None) -> None:
        creating = task is None
        super().__init__(
            app,
            "New task" if creating else "Edit task",
            "Create task" if creating else "Save changes",
        )
        self.users, self.task = users, task

        self.title_var = tk.StringVar(value="" if creating else task.title)
        title_entry = ttk.Entry(self.form, textvariable=self.title_var, width=46)
        self.add_field(0, "Title", title_entry)

        self.description = tk.Text(self.form, width=46, height=5, wrap="word", font="TkDefaultFont")
        if not creating:
            self.description.insert("1.0", task.description)
        self.add_field(1, "Description", self.description)

        if creating:
            initial = (datetime.now() + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
        else:
            initial = task.deadline.astimezone()
        self.deadline = DeadlineField(self.form, initial)
        self.add_field(2, "Deadline", self.deadline)

        self.assignee = None
        if creating:
            self.assignee = ttk.Combobox(
                self.form, state="readonly", width=43, values=[user_label(u) for u in users]
            )
            self.add_field(3, "Assign to", self.assignee)
        title_entry.focus_set()

    def perform(self):
        actor = self.app.actor()
        description = self.description.get("1.0", "end-1c")
        new_deadline = self.deadline.get()

        if self.task is None:
            index = self.assignee.current()
            if index < 0:
                raise ValueError("choose who the task is assigned to")
            return self.app.admin.create_task(
                actor, self.title_var.get(), description, new_deadline, self.users[index].id
            )

        # Only send the deadline if it was actually changed, so editing a finished task's
        # title doesn't trip over its (old) deadline.
        unchanged = self.task.deadline.astimezone().replace(second=0, microsecond=0)
        return self.app.admin.edit_task(
            actor,
            self.task.id,
            title=self.title_var.get(),
            description=description,
            deadline=None if new_deadline == unchanged else new_deadline,
        )


class ReassignDialog(FormDialog):
    def __init__(self, app: "App", task: Task, users: list[User], current_name: str) -> None:
        super().__init__(app, "Reassign task", "Reassign")
        self.task, self.users = task, users
        ttk.Label(
            self.form, text=f"'{task.title}' is currently assigned to {current_name}.",
            wraplength=380, justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.assignee = ttk.Combobox(
            self.form, state="readonly", width=34, values=[user_label(u) for u in users]
        )
        self.add_field(1, "Assign to", self.assignee)

    def perform(self):
        index = self.assignee.current()
        if index < 0:
            raise ValueError("choose who the task should be assigned to")
        return self.app.admin.reassign_task(self.app.actor(), self.task.id, self.users[index].id)


class UsersDialog(FormDialog):
    """List all users and add new ones."""

    close_on_success = False

    def __init__(self, app: "App", users: list[User]) -> None:
        super().__init__(app, "Users", "Add user", cancel_label="Close")
        self.tree = make_tree(
            self.top,
            [("username", "Username", 140), ("role", "Role", 80), ("email", "Email", 240)],
            height=7,
        )
        ttk.Label(self.top, text="Add a user", font=app.bold_font).pack(anchor="w", pady=(14, 0))

        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.email = tk.StringVar()
        self.add_field(0, "Username", ttk.Entry(self.form, textvariable=self.username, width=30))
        self.add_field(1, "Password", ttk.Entry(self.form, textvariable=self.password, show="•", width=30))
        self.add_field(2, "Email", ttk.Entry(self.form, textvariable=self.email, width=30))
        self.role = ttk.Combobox(self.form, state="readonly", width=27, values=list(ROLE_CHOICES))
        self.role.set("Regular user")
        self.add_field(3, "Account type", self.role)
        self.load(users)

    def load(self, users: list[User]) -> None:
        self.tree.delete(*self.tree.get_children())
        for user in users:
            self.tree.insert("", "end", iid=user.id, values=(user.username, user.role.value, user.email))

    def perform(self):
        return self.app.admin.create_user(
            self.app.actor(),
            self.username.get().strip(),
            self.password.get(),
            self.email.get(),
            ROLE_CHOICES[self.role.get()],
        )

    def on_success(self) -> None:
        self.info.set(f"User '{self.result.username}' created.")
        for var in (self.username, self.password, self.email):
            var.set("")
        self.load(self.app.admin.list_users(self.app.actor()))


class HistoryDialog(FormDialog):
    """Read-only list of the logged changes to one task."""

    def __init__(self, app: "App", task: Task, entries: list, names: dict[str, str]) -> None:
        super().__init__(app, f"History: {clip(task.title, 40)}", None, cancel_label="Close")
        if not entries:
            ttk.Label(self.top, text="No changes have been logged for this task.").pack(anchor="w")
            return
        tree = make_tree(
            self.top,
            [("when", "When", 140), ("by", "By", 100), ("action", "Action", 90), ("change", "Change", 380)],
            height=min(max(len(entries), 3), 12),
        )
        for entry in entries:
            change = (
                f"{FIELD_LABELS.get(entry.field, entry.field)}: "
                f"{audit_value(entry.field, entry.old_value, names)} → "
                f"{audit_value(entry.field, entry.new_value, names)}"
            )
            tree.insert("", "end", iid=entry.id, values=(
                fmt_dt(entry.timestamp, seconds=True),
                names.get(entry.actor_id, entry.actor_id[:8]),
                ACTION_LABELS.get(entry.action.value, entry.action.value),
                change,
            ))


# --- screens ---------------------------------------------------------------

class LoginFrame(ttk.Frame):
    def __init__(self, app: "App", notice: str = "", error: bool = False, username: str = "") -> None:
        super().__init__(app)
        self.app = app
        card = ttk.Frame(self, padding=28)
        card.place(relx=0.5, rely=0.42, anchor="center")

        ttk.Label(card, text="Task Manager", font=app.title_font).grid(row=0, column=0, columnspan=2)
        ttk.Label(card, text="Log in to see and manage your tasks.", foreground=MUTED_COLOR).grid(
            row=1, column=0, columnspan=2, pady=(2, 18)
        )

        self.username = tk.StringVar(value=username)
        self.password = tk.StringVar()
        ttk.Label(card, text="Username").grid(row=2, column=0, sticky="w", pady=4, padx=(0, 12))
        user_entry = ttk.Entry(card, textvariable=self.username, width=28)
        user_entry.grid(row=2, column=1, pady=4)
        ttk.Label(card, text="Password").grid(row=3, column=0, sticky="w", pady=4, padx=(0, 12))
        password_entry = ttk.Entry(card, textvariable=self.password, show="•", width=28)
        password_entry.grid(row=3, column=1, pady=4)
        user_entry.bind("<Return>", lambda _event: self.submit())
        password_entry.bind("<Return>", lambda _event: self.submit())

        self.message = tk.StringVar()
        self.message_label = ttk.Label(card, textvariable=self.message, wraplength=320, justify="left")
        self.message_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.show_message(notice, error)

        buttons = ttk.Frame(card)
        buttons.grid(row=5, column=0, columnspan=2, pady=(14, 0), sticky="e")
        ttk.Button(buttons, text="Create account", command=self.open_register).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Log in", command=self.submit).pack(side="left")

        (password_entry if username else user_entry).focus_set()

    def show_message(self, text: str, error: bool) -> None:
        self.message.set(text)
        self.message_label.configure(foreground=ERROR_COLOR if error else SUCCESS_COLOR)

    def submit(self) -> None:
        self.show_message("", False)
        try:
            self.app.log_in(self.username.get().strip(), self.password.get())
        except KNOWN_ERRORS as exc:
            self.show_message(friendly(exc), True)
            self.password.set("")

    def open_register(self) -> None:
        dialog = RegisterDialog(self.app)
        self.app.open_dialog(dialog)
        if dialog.result:
            self.username.set(dialog.result)
            self.show_message("Account created. You can log in now.", False)


class MainFrame(ttk.Frame):
    def __init__(self, app: "App", actor: User) -> None:
        super().__init__(app, padding=(14, 10))
        self.app = app
        self.actor = actor  # for display only; every action re-authenticates
        self.is_admin = actor.role is Role.ADMIN
        self.tasks: dict[str, Task] = {}
        self.names: dict[str, str] = {}
        self.refresh_job = None
        self.message = tk.StringVar()
        self.count_text = tk.StringVar()

        self.build_header()
        self.build_summary()
        self.build_toolbar()
        self.build_statusbar()
        self.build_body()

    # --- building the screen ----------------------------------------------

    def build_header(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x")
        ttk.Label(header, text="Task Manager", font=self.app.title_font).pack(side="left")
        ttk.Button(header, text="Log out", command=self.app.log_out).pack(side="right")
        role_text = "Administrator" if self.is_admin else "Regular user"
        ttk.Label(header, text=f"{self.actor.username}  ·  {role_text}").pack(side="right", padx=12)

    def build_summary(self) -> None:
        box = ttk.LabelFrame(self, text="Summary", padding=8)
        box.pack(fill="x", pady=(10, 0))
        self.count_vars: dict[TaskStatus, tk.StringVar] = {}
        for index, status in enumerate(TaskStatus):
            box.columnconfigure(index, weight=1)
            cell = ttk.Frame(box)
            cell.grid(row=0, column=index, sticky="we", padx=6)
            var = tk.StringVar(value="0")
            self.count_vars[status] = var
            ttk.Label(cell, textvariable=var, font=self.app.big_font,
                      foreground=STATUS_COLORS[status]).pack()
            ttk.Label(cell, text=STATUS_LABELS[status], foreground=STATUS_COLORS[status]).pack()

    def build_toolbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(10, 0))

        actions = ttk.LabelFrame(bar, text="Selected task", padding=6)
        actions.pack(side="left")
        self.btn_start = self.add_button(actions, "Start task", lambda: self.change_status(TaskStatus.ONGOING))
        self.btn_done = self.add_button(actions, "Mark as done", lambda: self.change_status(TaskStatus.DONE))
        self.btn_cancel = self.add_button(actions, "Cancel task", self.cancel_task)
        if self.is_admin:  # these widgets are simply never created for regular users
            self.btn_edit = self.add_button(actions, "Edit", self.edit_task)
            self.btn_reassign = self.add_button(actions, "Reassign", self.reassign_task)
            self.btn_history = self.add_button(actions, "History", self.show_history)
            manage = ttk.LabelFrame(bar, text="Manage", padding=6)
            manage.pack(side="left", padx=(10, 0))
            self.btn_new = self.add_button(manage, "New task", self.new_task)
            self.btn_users = self.add_button(manage, "Users", self.manage_users)

        right = ttk.Frame(bar)
        right.pack(side="right", anchor="s")
        ttk.Label(right, text="Show:").pack(side="left", padx=(0, 4))
        self.filter = ttk.Combobox(
            right, state="readonly", width=12,
            values=[ALL_TASKS] + [STATUS_LABELS[s] for s in TaskStatus],
        )
        self.filter.set(ALL_TASKS)
        self.filter.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        self.filter.pack(side="left")
        ttk.Button(right, text="Refresh", command=self.refresh).pack(side="left", padx=(8, 0))

    @staticmethod
    def add_button(parent, text: str, command) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command)
        button.pack(side="left", padx=3)
        return button

    def build_statusbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x", pady=(8, 0))
        self.message_label = ttk.Label(bar, textvariable=self.message)
        self.message_label.pack(side="left")
        ttk.Label(bar, textvariable=self.count_text, foreground=MUTED_COLOR).pack(side="right")

    def build_body(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, pady=(10, 0))

        left = ttk.Frame(paned)
        columns = [("title", "Title", 280), ("status", "Status", 90),
                   ("deadline", "Deadline", 130), ("assignee", "Assigned to", 120)]
        self.tree = make_tree(left, columns, height=14)
        if not self.is_admin:  # a regular user's tasks are all their own
            self.tree.configure(displaycolumns=("title", "status", "deadline"))
        for status in TaskStatus:
            self.tree.tag_configure(status.value, foreground=STATUS_COLORS[status])
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.on_select())

        details = ttk.LabelFrame(paned, text="Details", padding=10)
        self.detail_title = tk.StringVar()
        self.detail_status = tk.StringVar()
        self.detail_vars = {key: tk.StringVar() for key in ("Assigned to", "Assigned on", "Deadline")}
        ttk.Label(details, textvariable=self.detail_title, font=self.app.bold_font,
                  wraplength=300, justify="left").pack(anchor="w")
        self.detail_status_label = ttk.Label(details, textvariable=self.detail_status)
        self.detail_status_label.pack(anchor="w", pady=(2, 8))
        grid = ttk.Frame(details)
        grid.pack(fill="x")
        for row, (label, var) in enumerate(self.detail_vars.items()):
            ttk.Label(grid, text=f"{label}:", foreground=MUTED_COLOR).grid(row=row, column=0, sticky="nw", padx=(0, 8))
            ttk.Label(grid, textvariable=var, wraplength=220, justify="left").grid(row=row, column=1, sticky="w")
        ttk.Label(details, text="Description", foreground=MUTED_COLOR).pack(anchor="w", pady=(10, 2))
        self.detail_text = tk.Text(details, height=8, width=34, wrap="word", state="disabled", font="TkDefaultFont")
        self.detail_text.pack(fill="both", expand=True)

        paned.add(left, weight=3)
        paned.add(details, weight=2)

    # --- reading the screen -----------------------------------------------

    def selected_id(self) -> Optional[str]:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def selected_task(self) -> Optional[Task]:
        return self.tasks.get(self.selected_id())

    def on_select(self) -> None:
        task = self.selected_task()
        self.show_details(task)
        self.update_buttons(task)

    def show_details(self, task: Optional[Task]) -> None:
        if task is None:
            self.detail_title.set("Select a task to see its details.")
            self.detail_status.set("")
            for var in self.detail_vars.values():
                var.set("")
            text = ""
        else:
            self.detail_title.set(task.title)
            self.detail_status.set(STATUS_LABELS[task.status])
            self.detail_status_label.configure(foreground=STATUS_COLORS[task.status])
            self.detail_vars["Assigned to"].set(self.names.get(task.assigned_user_id, "unknown"))
            self.detail_vars["Assigned on"].set(fmt_dt(task.assignment_date))
            self.detail_vars["Deadline"].set(fmt_dt(task.deadline))
            text = task.description or "(no description)"
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", text)
        self.detail_text.configure(state="disabled")

    @staticmethod
    def enable(button, enabled: bool) -> None:
        button.state(["!disabled" if enabled else "disabled"])

    def update_buttons(self, task: Optional[Task]) -> None:
        allowed = ALLOWED_TRANSITIONS[task.status] if task else frozenset()
        self.enable(self.btn_start, TaskStatus.ONGOING in allowed)
        self.enable(self.btn_done, TaskStatus.DONE in allowed)
        self.enable(self.btn_cancel, TaskStatus.CANCELLED in allowed)
        if self.is_admin:
            for button in (self.btn_edit, self.btn_reassign, self.btn_history):
                self.enable(button, task is not None)

    def say(self, text: str) -> None:
        self.message.set(text)

    def filter_status(self) -> Optional[TaskStatus]:
        return LABEL_TO_STATUS.get(self.filter.get())

    # --- loading data ------------------------------------------------------

    def start(self) -> None:
        self.refresh()
        if self.app.screen is self:  # not sent back to the login screen meanwhile
            self.schedule_refresh()

    def schedule_refresh(self) -> None:
        self.refresh_job = self.after(REFRESH_MS, self.auto_refresh)

    def auto_refresh(self) -> None:
        self.refresh_job = None
        self.reload_quietly()  # nobody asked for this reload, so no pop-ups on errors
        if self.app.screen is self:
            self.schedule_refresh()

    def reload_quietly(self) -> None:
        """Reload the screen. Never shows a message box and never raises."""
        try:
            self.load()
        except InvalidSessionError:
            self.app.session_expired()
        except KNOWN_ERRORS:
            pass

    @guarded
    def refresh(self, select_id: Optional[str] = None) -> None:
        self.load(select_id)

    def load(self, select_id: Optional[str] = None) -> None:
        """Fetch the tasks and redraw everything. Raises on errors (see refresh/reload_quietly)."""
        actor = self.app.actor()
        tasks = self.app.receiver.list_tasks(actor)  # also marks overdue tasks as failed
        if self.is_admin:
            self.names = {u.id: u.username for u in self.app.admin.list_users(actor)}
        else:
            self.names = {actor.id: actor.username}

        counts = {status: 0 for status in TaskStatus}
        for task in tasks:
            counts[task.status] += 1
        for status, var in self.count_vars.items():
            var.set(str(counts[status]))

        keep = select_id or self.selected_id()
        wanted = self.filter_status()
        shown = [t for t in tasks if wanted is None or t.status is wanted]
        self.tasks = {t.id: t for t in tasks}

        self.tree.delete(*self.tree.get_children())
        for task in shown:
            self.tree.insert("", "end", iid=task.id, tags=(task.status.value,), values=(
                task.title,
                STATUS_LABELS[task.status],
                fmt_dt(task.deadline),
                self.names.get(task.assigned_user_id, "unknown"),
            ))
        if keep in {t.id for t in shown}:
            self.tree.selection_set(keep)
            self.tree.see(keep)

        if not tasks:
            self.count_text.set("No tasks yet")
        elif not shown:
            self.count_text.set("No tasks with this status")
        else:
            self.count_text.set(f"{len(shown)} of {len(tasks)} tasks shown")
        self.on_select()

    # --- actions for everyone ---------------------------------------------

    @guarded
    def change_status(self, new_status: TaskStatus) -> None:
        task = self.selected_task()
        if task is None:
            return
        updated = self.app.receiver.update_status(self.app.actor(), task.id, new_status)
        self.say(f"'{updated.title}' is now {STATUS_LABELS[updated.status].lower()}.")
        self.refresh(select_id=task.id)

    @guarded
    def cancel_task(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        if not messagebox.askyesno(
            "Cancel task", f"Cancel '{task.title}'?\n\nThis cannot be undone.", parent=self.app
        ):
            return
        updated = self.app.receiver.cancel(self.app.actor(), task.id)
        self.say(f"'{updated.title}' was cancelled.")
        self.refresh(select_id=task.id)

    # --- admin actions -----------------------------------------------------

    @guarded
    def new_task(self) -> None:
        users = self.app.admin.list_users(self.app.actor())
        dialog = TaskDialog(self.app, users)
        self.app.open_dialog(dialog)
        if dialog.result:
            self.filter.set(ALL_TASKS)
            self.say(f"Created '{dialog.result.title}'.")
            self.refresh(select_id=dialog.result.id)

    @guarded
    def edit_task(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        dialog = TaskDialog(self.app, [], task)
        self.app.open_dialog(dialog)
        if dialog.result:
            self.say(f"Saved changes to '{dialog.result.title}'.")
            self.refresh(select_id=task.id)

    @guarded
    def reassign_task(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        users = self.app.admin.list_users(self.app.actor())
        current = self.names.get(task.assigned_user_id, "unknown")
        dialog = ReassignDialog(self.app, task, users, current)
        self.app.open_dialog(dialog)
        if dialog.result:
            self.say(f"'{task.title}' was reassigned.")
            self.refresh(select_id=task.id)

    @guarded
    def show_history(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        actor = self.app.actor()
        entries = self.app.admin.get_audit_log(actor, task.id)
        self.app.open_dialog(HistoryDialog(self.app, task, entries, self.names))

    @guarded
    def manage_users(self) -> None:
        users = self.app.admin.list_users(self.app.actor())
        self.app.open_dialog(UsersDialog(self.app, users))
        self.refresh()

    def destroy(self) -> None:
        if self.refresh_job is not None:
            self.after_cancel(self.refresh_job)
            self.refresh_job = None
        super().destroy()


# --- the application window ------------------------------------------------

class App(tk.Tk):
    def __init__(self, auth: AuthService, admin: AdminService, receiver: ReceiverService) -> None:
        super().__init__()
        self.auth, self.admin, self.receiver = auth, admin, receiver
        self.token: Optional[str] = None
        self.screen: Optional[ttk.Frame] = None

        self.title("Task Manager")
        self.geometry("1100x680")
        self.minsize(1000, 560)
        self.setup_style()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show_login()

    def setup_style(self) -> None:
        style = ttk.Style(self)
        if self.tk.call("tk", "windowingsystem") == "x11":
            style.theme_use("clam")  # the default Linux theme looks dated
        base = tkfont.nametofont("TkDefaultFont")
        size = abs(int(base.cget("size"))) or 10
        self.bold_font = base.copy()
        self.bold_font.configure(weight="bold")
        self.title_font = base.copy()
        self.title_font.configure(size=size * 2, weight="bold")
        self.big_font = base.copy()
        self.big_font.configure(size=int(size * 1.8), weight="bold")
        style.configure("Treeview", rowheight=base.metrics("linespace") + 10)

    # --- who is logged in --------------------------------------------------

    def actor(self) -> User:
        """The logged-in user, freshly looked up. Raises InvalidSessionError if not logged in."""
        if self.token is None:
            raise InvalidSessionError()
        return self.auth.authenticate(self.token)

    def log_in(self, username: str, password: str) -> None:
        token = self.auth.login(username, password)
        self.token = token
        self.show_main(self.auth.authenticate(token))

    def log_out(self) -> None:
        if self.token is not None:
            self.auth.logout(self.token)
        self.token = None
        self.show_login("You have been logged out.")

    def session_expired(self) -> None:
        self.token = None
        self.show_login("Your session has expired. Please log in again.", error=True)

    def close(self) -> None:
        if self.token is not None:
            try:
                self.auth.logout(self.token)
            except Exception:  # closing the window must always work
                pass
        self.destroy()

    # --- screens and dialogs -----------------------------------------------

    def swap(self, frame: ttk.Frame) -> None:
        if self.screen is not None:
            self.screen.destroy()
        self.screen = frame
        frame.pack(fill="both", expand=True)

    def show_login(self, notice: str = "", error: bool = False, username: str = "") -> None:
        self.swap(LoginFrame(self, notice, error, username))

    def show_main(self, actor: User) -> None:
        frame = MainFrame(self, actor)
        self.swap(frame)
        frame.start()

    def open_dialog(self, dialog: tk.Toplevel) -> None:
        """Show a dialog centred over the window and wait until it is closed."""
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_reqheight()) // 3
        dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dialog.wait_visibility()
        dialog.grab_set()
        self.wait_window(dialog)


def build_app(db_path: str) -> App:
    conn = connect(db_path)
    init_schema(conn)
    users, tasks = UserRepository(conn), TaskRepository(conn)
    audit, sessions = AuditLogRepository(conn), SessionRepository(conn)
    auth = AuthService(users, sessions)
    return App(auth, AdminService(conn, users, tasks, audit, auth), ReceiverService(tasks))


def main() -> None:
    parser = argparse.ArgumentParser(description="Task manager desktop interface")
    parser.add_argument("--db", default="task_manager.db", help="SQLite file (default: task_manager.db)")
    build_app(parser.parse_args().db).mainloop()


if __name__ == "__main__":
    main()
