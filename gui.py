"""
Task Manager — Tkinter GUI.

A graphical front end for the same backend modules the CLI uses
(auth, admin, receiver, access, storage). No business logic lives here:
every action calls a backend function and shows its result or its error.

Run with:
    python gui.py

Pictures: PNG/GIF previews work out of the box. JPG previews need the
optional Pillow package (pip install pillow); without it, JPGs are still
stored and attached to tasks, just not previewed.
"""

import math
import os
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk, messagebox, filedialog, simpledialog
import tkinter.font as tkfont

import storage
from auth import register, login, AuthError
from admin import (
    create_user, create_task, assign_task, edit_task, list_users, AdminError,
)
from receiver import (
    update_status, get_progress, cancel_task, list_tasks,
    get_task_history, ReceiverError,
)
from models import Status, ALLOWED_TRANSITIONS, TERMINAL_STATUSES

try:  # optional, only needed to preview JPG files
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

DATE_FMT = "%Y-%m-%d %H:%M"
BACKEND_ERRORS = (AuthError, AdminError, ReceiverError)

STATUS_LABEL = {
    "todo": "To do", "ongoing": "Ongoing", "done": "Done",
    "failed": "Failed", "cancelled": "Cancelled",
}
STATUS_COLOR = {
    "todo": "#6b7280", "ongoing": "#2563eb", "done": "#16a34a",
    "failed": "#dc2626", "cancelled": "#9ca3af",
}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def fmt_dt(iso: str) -> str:
    """ISO timestamp -> 'YYYY-MM-DD HH:MM' for display."""
    try:
        return datetime.fromisoformat(iso).strftime(DATE_FMT)
    except (TypeError, ValueError):
        return str(iso)


def load_preview(rel_path, max_w=280, max_h=190):
    """
    Return (PhotoImage, None) on success or (None, reason) on failure.
    Uses Pillow when available (handles PNG/GIF/JPG); otherwise falls back to
    Tk's built-in PNG/GIF support.
    """
    path = storage.resolve_picture_path(rel_path)
    if not os.path.isfile(path):
        return None, "Picture file not found"
    ext = os.path.splitext(path)[1].lower()
    try:
        if HAS_PIL:
            img = Image.open(path)
            img.thumbnail((max_w, max_h))
            return ImageTk.PhotoImage(img), None
        if ext in (".png", ".gif"):
            img = tk.PhotoImage(file=path)
            factor = max(1, math.ceil(img.width() / max_w),
                         math.ceil(img.height() / max_h))
            return (img.subsample(factor) if factor > 1 else img), None
        return None, "Install Pillow (pip install pillow) to preview JPG files"
    except Exception as e:  # unreadable/corrupt image
        return None, f"Could not load picture ({e.__class__.__name__})"


# --------------------------------------------------------------------------
# Dialogs
# --------------------------------------------------------------------------

class Dialog(tk.Toplevel):
    """Base modal dialog: centred over its parent, blocks until closed."""

    def __init__(self, parent, title, resizable=False):
        super().__init__(parent)
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(resizable, resizable)
        self.result = None
        self.body = ttk.Frame(self, padding=14)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.destroy())

    def show(self):
        self.update_idletasks()
        top = self.master.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - self.winfo_width()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.wait_visibility()
        self.grab_set()
        self.wait_window()
        return self.result

    def button_row(self, row, ok_text, ok_cmd):
        bar = ttk.Frame(self.body)
        bar.grid(row=row, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text=ok_text, command=ok_cmd).pack(side="right", padx=(0, 8))


class UserDialog(Dialog):
    """
    Register a new account (admin_session=None) or, when an admin session is
    given, create a user on behalf of that admin. result = the new Session.
    """

    def __init__(self, parent, admin_session=None):
        super().__init__(parent, "New user" if admin_session else "Register")
        self.admin_session = admin_session
        b = self.body

        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.email = tk.StringVar()
        self.role = tk.StringVar(value="user")

        rows = [("Username", self.username, None), ("Password", self.password, "*"),
                ("Email", self.email, None)]
        for i, (label, var, mask) in enumerate(rows):
            ttk.Label(b, text=label).grid(row=i, column=0, sticky="w", pady=4)
            entry = ttk.Entry(b, textvariable=var, width=30, show=mask or "")
            entry.grid(row=i, column=1, pady=4, padx=(10, 0))
            if i == 0:
                entry.focus_set()
        ttk.Label(b, text="Role").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(b, textvariable=self.role, values=["user", "admin"],
                     state="readonly", width=27).grid(row=3, column=1, pady=4, padx=(10, 0))

        self.button_row(4, "Create" if admin_session else "Register", self.submit)
        self.bind("<Return>", lambda e: self.submit())

    def submit(self):
        u, p, e = self.username.get().strip(), self.password.get(), self.email.get().strip()
        if not u or not p or not e:
            messagebox.showwarning("Missing information",
                                   "Username, password and email are all required.", parent=self)
            return
        try:
            if self.admin_session:
                self.result = create_user(self.admin_session, u, p, e, self.role.get())
            else:
                self.result = register(u, p, e, self.role.get())
        except BACKEND_ERRORS as err:
            messagebox.showerror("Could not create user", str(err), parent=self)
            return
        self.destroy()


class ChoiceDialog(Dialog):
    """Pick one value from a dropdown. result = chosen string or None."""

    def __init__(self, parent, title, prompt, choices, initial=None):
        super().__init__(parent, title)
        self.var = tk.StringVar(value=initial or (choices[0] if choices else ""))
        ttk.Label(self.body, text=prompt).grid(row=0, column=0, sticky="w")
        ttk.Combobox(self.body, textvariable=self.var, values=choices,
                     state="readonly", width=30).grid(row=1, column=0, pady=8)
        self.button_row(2, "OK", self.submit)

    def submit(self):
        self.result = self.var.get() or None
        self.destroy()


class TaskDialog(Dialog):
    """
    Create a task (task=None) or edit an existing one. Talks to the backend
    itself so that on an error the dialog stays open and nothing typed is lost.
    result = True if the task was saved.
    """

    def __init__(self, parent, session, task=None, usernames=None):
        super().__init__(parent, "Edit task" if task else "New task")
        self.session, self.task = session, task
        self.picture_ref = task.picture if task else None   # stored reference
        self.new_picture_src = None                         # freshly chosen file
        self.picture_cleared = False
        b = self.body

        ttk.Label(b, text="Title").grid(row=0, column=0, sticky="nw", pady=4)
        self.title_var = tk.StringVar(value=task.title if task else "")
        title_entry = ttk.Entry(b, textvariable=self.title_var, width=44)
        title_entry.grid(row=0, column=1, columnspan=2, pady=4, padx=(10, 0), sticky="w")
        title_entry.focus_set()

        ttk.Label(b, text="Description").grid(row=1, column=0, sticky="nw", pady=4)
        self.desc = tk.Text(b, width=44, height=5, wrap="word")
        self.desc.grid(row=1, column=1, columnspan=2, pady=4, padx=(10, 0))
        if task:
            self.desc.insert("1.0", task.description)

        ttk.Label(b, text="Assign to").grid(row=2, column=0, sticky="w", pady=4)
        if task is None:
            self.assignee = tk.StringVar(value=usernames[0] if usernames else "")
            ttk.Combobox(b, textvariable=self.assignee, values=usernames or [],
                         state="readonly", width=41).grid(row=2, column=1, columnspan=2,
                                                          pady=4, padx=(10, 0), sticky="w")
        else:
            ttk.Label(b, text=f"{task.assigned_user}  (use Reassign to change)"
                      ).grid(row=2, column=1, columnspan=2, pady=4, padx=(10, 0), sticky="w")

        ttk.Label(b, text="Deadline").grid(row=3, column=0, sticky="w", pady=4)
        default = (fmt_dt(task.deadline) if task
                   else (datetime.now() + timedelta(days=3)).strftime(DATE_FMT))
        self.deadline_text = default
        self.deadline = tk.StringVar(value=default)
        ttk.Entry(b, textvariable=self.deadline, width=20).grid(
            row=3, column=1, pady=4, padx=(10, 0), sticky="w")
        ttk.Label(b, text="format: YYYY-MM-DD HH:MM", foreground="#6b7280").grid(
            row=3, column=2, sticky="w")

        ttk.Label(b, text="Picture").grid(row=4, column=0, sticky="w", pady=4)
        self.picture_label = ttk.Label(b, text=self._picture_text(), width=34)
        self.picture_label.grid(row=4, column=1, pady=4, padx=(10, 0), sticky="w")
        pic_btns = ttk.Frame(b)
        pic_btns.grid(row=4, column=2, sticky="w")
        ttk.Button(pic_btns, text="Browse…", command=self.browse).pack(side="left")
        ttk.Button(pic_btns, text="Clear", command=self.clear_picture).pack(side="left", padx=4)

        self.button_row(5, "Save" if task else "Create", self.submit)

    def _picture_text(self):
        if self.new_picture_src:
            return os.path.basename(self.new_picture_src)
        if self.picture_ref and not self.picture_cleared:
            return os.path.basename(self.picture_ref)
        return "(none)"

    def browse(self):
        types = [("Images", "*.png *.gif *.jpg *.jpeg"), ("All files", "*.*")]
        path = filedialog.askopenfilename(parent=self, title="Choose a picture", filetypes=types)
        if path:
            self.new_picture_src, self.picture_cleared = path, False
            self.picture_label.config(text=self._picture_text())

    def clear_picture(self):
        self.new_picture_src, self.picture_cleared = None, True
        self.picture_label.config(text=self._picture_text())

    def submit(self):
        title = self.title_var.get().strip()
        description = self.desc.get("1.0", "end-1c").strip()
        if not title:
            messagebox.showwarning("Missing title", "Please enter a title.", parent=self)
            return

        deadline_text = self.deadline.get().strip()
        try:
            deadline_iso = datetime.strptime(deadline_text, DATE_FMT).isoformat()
        except ValueError:
            messagebox.showwarning("Invalid deadline",
                                   "Use the format YYYY-MM-DD HH:MM, e.g. 2026-10-15 18:00.",
                                   parent=self)
            return

        try:
            picture = self.picture_ref if not self.picture_cleared else None
            if self.new_picture_src:
                picture = storage.import_picture(self.new_picture_src)

            if self.task is None:
                create_task(self.session, title, description, deadline_iso,
                            self.assignee.get(), picture=picture)
            else:
                changes = {}
                if title != self.task.title:
                    changes["title"] = title
                if description != self.task.description:
                    changes["description"] = description
                if deadline_text != self.deadline_text:
                    changes["deadline"] = deadline_iso
                if picture != self.task.picture:
                    changes["picture"] = picture
                if changes:
                    edit_task(self.session, self.task.id, **changes)
            self.result = True
        except BACKEND_ERRORS as err:
            messagebox.showerror("Could not save task", str(err), parent=self)
            return
        except OSError as err:
            messagebox.showerror("Could not copy picture", str(err), parent=self)
            return
        self.destroy()


class HistoryDialog(Dialog):
    """Read-only audit log of a task (who did what, when)."""

    def __init__(self, parent, session, task):
        super().__init__(parent, f"History — {task.title}", resizable=True)
        cols = ("when", "who", "action", "details")
        tree = ttk.Treeview(self.body, columns=cols, show="headings", height=12)
        for col, text, width in (("when", "When", 140), ("who", "By", 90),
                                 ("action", "Action", 110), ("details", "Details", 300)):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor="w")
        tree.pack(fill="both", expand=True)
        try:
            for entry in get_task_history(session, task.id):
                tree.insert("", "end", values=(fmt_dt(entry["timestamp"]), entry["actor"],
                                               entry["action"], entry["details"]))
        except BACKEND_ERRORS as err:
            messagebox.showerror("Error", str(err), parent=self)
        ttk.Button(self.body, text="Close", command=self.destroy).pack(anchor="e", pady=(10, 0))


class ProgressDialog(Dialog):
    """Admin overview: task counts per status, for everyone and per user."""

    def __init__(self, parent, session):
        super().__init__(parent, "Progress overview", resizable=True)
        cols = ("who", "todo", "ongoing", "done", "failed", "cancelled", "total")
        tree = ttk.Treeview(self.body, columns=cols, show="headings", height=8)
        for col in cols:
            tree.heading(col, text="User" if col == "who" else STATUS_LABEL.get(col, "Total"))
            tree.column(col, width=130 if col == "who" else 80, anchor="w" if col == "who" else "center")
        tree.pack(fill="both", expand=True)
        try:
            rows = [("All tasks", get_progress(session))]
            for u in list_users(session):
                rows.append((u["username"], get_progress(session, u["username"])))
            for name, counts in rows:
                tree.insert("", "end", values=(name, *[counts[s.value] for s in Status],
                                               sum(counts.values())))
        except BACKEND_ERRORS as err:
            messagebox.showerror("Error", str(err), parent=self)
        ttk.Button(self.body, text="Close", command=self.destroy).pack(anchor="e", pady=(10, 0))


# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------

class LoginFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        card = ttk.Frame(self, padding=30)
        card.place(relx=0.5, rely=0.45, anchor="center")

        ttk.Label(card, text="Task Manager", font=app.f_title).grid(row=0, column=0, columnspan=2, pady=(0, 4))
        ttk.Label(card, text="Sign in to continue", foreground="#6b7280").grid(
            row=1, column=0, columnspan=2, pady=(0, 18))

        self.username = tk.StringVar()
        self.password = tk.StringVar()
        ttk.Label(card, text="Username").grid(row=2, column=0, sticky="w", pady=5)
        user_entry = ttk.Entry(card, textvariable=self.username, width=28)
        user_entry.grid(row=2, column=1, pady=5, padx=(10, 0))
        ttk.Label(card, text="Password").grid(row=3, column=0, sticky="w", pady=5)
        pass_entry = ttk.Entry(card, textvariable=self.password, show="*", width=28)
        pass_entry.grid(row=3, column=1, pady=5, padx=(10, 0))

        ttk.Button(card, text="Log in", command=self.do_login).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(16, 6))
        ttk.Button(card, text="Create an account", command=self.do_register).grid(
            row=5, column=0, columnspan=2, sticky="ew")

        user_entry.bind("<Return>", lambda e: pass_entry.focus_set())
        pass_entry.bind("<Return>", lambda e: self.do_login())
        user_entry.focus_set()

    def do_login(self):
        try:
            session = login(self.username.get().strip(), self.password.get())
        except AuthError as err:
            messagebox.showerror("Login failed", str(err))
            return
        self.app.show_main(session)

    def do_register(self):
        session = UserDialog(self).show()
        if session is not None:
            self.app.show_main(session)


class TaskListFrame(ttk.Frame):
    """
    Shared layout for admin and user screens: header, toolbar, progress strip,
    task table (left) and task details with picture (right).
    Subclasses provide the toolbar buttons and the table columns.
    """

    columns = ()  # list of (id, heading, width, getter(task) -> str)

    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app, self.session = app, app.session
        self.tasks = {}
        self._photo = None  # keep a reference so Tk doesn't garbage-collect the image

        # header
        header = ttk.Frame(self)
        header.pack(fill="x")
        who = f"{self.session.username}  ·  {self.session.role.value}"
        ttk.Label(header, text="Task Manager", font=app.f_title).pack(side="left")
        ttk.Button(header, text="Log out", command=app.show_login).pack(side="right")
        ttk.Label(header, text=who, foreground="#6b7280").pack(side="right", padx=12)

        # toolbar
        self.toolbar = ttk.Frame(self)
        self.toolbar.pack(fill="x", pady=(10, 6))
        self.build_toolbar(self.toolbar)
        ttk.Button(self.toolbar, text="Refresh", command=self.refresh).pack(side="right")
        self.filter_var = tk.StringVar(value="All")
        flt = ttk.Combobox(self.toolbar, textvariable=self.filter_var, state="readonly", width=11,
                           values=["All"] + [STATUS_LABEL[s.value] for s in Status])
        flt.pack(side="right", padx=(0, 8))
        flt.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(self.toolbar, text="Show:").pack(side="right", padx=(0, 4))

        # progress strip
        strip = ttk.Frame(self)
        strip.pack(fill="x", pady=(0, 8))
        self.count_labels = {}
        for s in Status:
            lbl = ttk.Label(strip, text="", foreground=STATUS_COLOR[s.value], font=app.f_bold)
            lbl.pack(side="left", padx=(0, 16))
            self.count_labels[s.value] = lbl
        self.pct_label = ttk.Label(strip, text="")
        self.pct_label.pack(side="right")
        self.bar = ttk.Progressbar(strip, length=160, maximum=100)
        self.bar.pack(side="right", padx=8)

        # body: table + details
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        ids = [c[0] for c in self.columns]
        self.tree = ttk.Treeview(left, columns=ids, show="headings", selectmode="browse")
        for cid, heading, width, _ in self.columns:
            self.tree.heading(cid, text=heading)
            self.tree.column(cid, width=width, anchor="w")
        for status, color in STATUS_COLOR.items():
            self.tree.tag_configure(status, foreground=color)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_details())

        right = ttk.LabelFrame(body, text="Details", padding=10, width=310)
        right.pack(side="left", fill="y", padx=(10, 0))
        right.pack_propagate(False)
        self.d_title = ttk.Label(right, text="", font=app.f_bold, wraplength=280, justify="left")
        self.d_title.pack(anchor="w")
        self.d_info = ttk.Label(right, text="", justify="left", foreground="#374151")
        self.d_info.pack(anchor="w", pady=(6, 6))
        self.d_desc = tk.Text(right, width=34, height=6, wrap="word", state="disabled",
                              relief="flat", background=self.app.cget("background"))
        self.d_desc.pack(fill="x")
        self.d_pic = ttk.Label(right, text="", foreground="#6b7280", wraplength=280)
        self.d_pic.pack(pady=8)
        self.d_hist = ttk.Button(right, text="View history", command=self.show_history)
        self.d_hist.pack(anchor="w")

        # status bar
        self.msg = ttk.Label(self, text="", anchor="w")
        self.msg.pack(fill="x", pady=(8, 0))

        self.refresh()

    # ---- hooks for subclasses ------------------------------------------
    def build_toolbar(self, bar):
        raise NotImplementedError

    def on_select(self, task):
        """Called whenever the selected task changes (task may be None)."""

    # ---- feedback -------------------------------------------------------
    def set_msg(self, text, error=False):
        self.msg.config(text=text, foreground="#dc2626" if error else "#16a34a")

    # ---- data -----------------------------------------------------------
    def refresh(self, select_id=None):
        keep = select_id or (self.tree.selection()[0] if self.tree.selection() else None)
        try:
            tasks = list_tasks(self.session)
            counts = get_progress(self.session)
        except BACKEND_ERRORS as err:
            messagebox.showerror("Error", str(err))
            return

        # active tasks first (soonest deadline first), finished ones after
        tasks.sort(key=lambda t: (t.status in TERMINAL_STATUSES, t.deadline))
        self.tasks = {t.id: t for t in tasks}

        wanted = self.filter_var.get()
        self.tree.delete(*self.tree.get_children())
        for t in tasks:
            if wanted != "All" and STATUS_LABEL[t.status.value] != wanted:
                continue
            self.tree.insert("", "end", iid=t.id, tags=(t.status.value,),
                             values=[getter(t) for *_, getter in self.columns])
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
            self.tree.see(keep)

        for s in Status:
            self.count_labels[s.value].config(text=f"{STATUS_LABEL[s.value]}: {counts[s.value]}")
        counted = sum(counts.values()) - counts["cancelled"]
        pct = round(100 * counts["done"] / counted) if counted else 0
        self.bar["value"] = pct
        self.pct_label.config(text=f"{pct}% done")
        self.update_details()

    def selected_task(self, quiet=False):
        sel = self.tree.selection()
        if not sel:
            if not quiet:
                messagebox.showinfo("Select a task", "Please select a task in the list first.")
            return None
        return self.tasks.get(sel[0])

    def call(self, fn, *args, ok=None, **kwargs):
        """Run a backend function; show an error box on failure, refresh on success."""
        try:
            result = fn(*args, **kwargs)
        except BACKEND_ERRORS as err:
            messagebox.showerror("Not allowed", str(err))
            self.set_msg(str(err), error=True)
            return False
        if ok:
            self.set_msg(ok)
        self.refresh()
        return True

    # ---- details panel --------------------------------------------------
    def update_details(self):
        task = self.selected_task(quiet=True)
        self.d_desc.config(state="normal")
        self.d_desc.delete("1.0", "end")
        self._photo = None
        self.d_pic.config(image="", text="")
        if task is None:
            self.d_title.config(text="Select a task to see details")
            self.d_info.config(text="")
            self.d_desc.config(state="disabled")
            self.d_hist.state(["disabled"])
            self.on_select(None)
            return

        self.d_title.config(text=task.title)
        self.d_info.config(text=(
            f"Status: {STATUS_LABEL[task.status.value]}\n"
            f"Assigned to: {task.assigned_user}\n"
            f"Created by: {task.created_by}\n"
            f"Assigned on: {fmt_dt(task.assignment_date)}\n"
            f"Deadline: {fmt_dt(task.deadline)}"))
        self.d_desc.insert("1.0", task.description or "(no description)")
        self.d_desc.config(state="disabled")
        self.d_hist.state(["!disabled"])

        if task.picture:
            photo, problem = load_preview(task.picture)
            if photo:
                self._photo = photo
                self.d_pic.config(image=photo)
            else:
                self.d_pic.config(text=problem)
        self.on_select(task)

    def show_history(self):
        task = self.selected_task()
        if task:
            HistoryDialog(self, self.session, task).show()

    def cancel_selected(self):
        task = self.selected_task()
        if not task:
            return
        if not messagebox.askyesno("Cancel task", f"Cancel “{task.title}”?"):
            return
        reason = simpledialog.askstring("Reason", "Reason for cancelling (optional):", parent=self)
        self.call(cancel_task, self.session, task.id, reason or None,
                  ok=f"Cancelled “{task.title}”.")


class AdminFrame(TaskListFrame):
    columns = (
        ("title", "Title", 200, lambda t: t.title),
        ("to", "Assigned to", 100, lambda t: t.assigned_user),
        ("status", "Status", 80, lambda t: STATUS_LABEL[t.status.value]),
        ("deadline", "Deadline", 125, lambda t: fmt_dt(t.deadline)),
        ("assigned", "Assigned on", 125, lambda t: fmt_dt(t.assignment_date)),
    )

    def build_toolbar(self, bar):
        for text, cmd in (("New task", self.new_task), ("Edit", self.edit_selected),
                          ("Reassign", self.reassign_selected),
                          ("Cancel task", self.cancel_selected),
                          ("Progress", self.show_progress), ("New user", self.new_user)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 6))

    def usernames(self):
        return [u["username"] for u in list_users(self.session)]

    def new_task(self):
        if TaskDialog(self, self.session, usernames=self.usernames()).show():
            self.set_msg("Task created.")
            self.refresh()

    def edit_selected(self):
        task = self.selected_task()
        if task and TaskDialog(self, self.session, task=task).show():
            self.set_msg(f"Updated “{task.title}”.")
            self.refresh(select_id=task.id)

    def reassign_selected(self):
        task = self.selected_task()
        if not task:
            return
        names = self.usernames()
        choice = ChoiceDialog(self, "Reassign task", f"Assign “{task.title}” to:",
                              names, initial=task.assigned_user).show()
        if not choice or choice == task.assigned_user:
            return
        if not messagebox.askyesno("Reassign", "Reassigning resets the task's status to “To do”. Continue?"):
            return
        self.call(assign_task, self.session, task.id, choice,
                  ok=f"Reassigned “{task.title}” to {choice}.")

    def show_progress(self):
        ProgressDialog(self, self.session).show()

    def new_user(self):
        created = UserDialog(self, admin_session=self.session).show()
        if created is not None:
            self.set_msg(f"User “{created.username}” created.")


class UserFrame(TaskListFrame):
    columns = (
        ("title", "Title", 210, lambda t: t.title),
        ("status", "Status", 80, lambda t: STATUS_LABEL[t.status.value]),
        ("deadline", "Deadline", 125, lambda t: fmt_dt(t.deadline)),
        ("assigned", "Assigned on", 125, lambda t: fmt_dt(t.assignment_date)),
        ("from", "From", 90, lambda t: t.created_by),
    )

    def build_toolbar(self, bar):
        ttk.Label(bar, text="Set status:").pack(side="left")
        self.status_var = tk.StringVar()
        self.status_box = ttk.Combobox(bar, textvariable=self.status_var, state="disabled", width=12)
        self.status_box.pack(side="left", padx=6)
        self.update_btn = ttk.Button(bar, text="Update", command=self.update_selected)
        self.update_btn.pack(side="left", padx=(0, 14))
        ttk.Button(bar, text="Cancel task", command=self.cancel_selected).pack(side="left")

    def on_select(self, task):
        """Offer only the statuses this task may legally move to next."""
        nxt = []
        if task is not None:
            nxt = [STATUS_LABEL[s.value] for s in Status
                   if s in ALLOWED_TRANSITIONS[task.status] and s != Status.CANCELLED]
        self.status_box.config(values=nxt, state="readonly" if nxt else "disabled")
        self.status_var.set(nxt[0] if nxt else "")
        self.update_btn.state(["!disabled"] if nxt else ["disabled"])

    def update_selected(self):
        task = self.selected_task()
        if not task or not self.status_var.get():
            return
        new = {v: k for k, v in STATUS_LABEL.items()}[self.status_var.get()]
        self.call(update_status, self.session, task.id, new,
                  ok=f"“{task.title}” is now {STATUS_LABEL[new].lower()}.")


# --------------------------------------------------------------------------
# Application window
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Task Manager")
        self.geometry("1040x640")
        self.minsize(940, 560)

        base = tkfont.nametofont("TkDefaultFont")
        self.f_bold = base.copy()
        self.f_bold.configure(weight="bold")
        self.f_title = base.copy()
        self.f_title.configure(weight="bold", size=base.cget("size") + 6)
        ttk.Style(self).configure("Treeview", rowheight=26)

        self.session = None
        self._frame = None
        self.show_login()

    def _swap(self, frame):
        if self._frame is not None:
            self._frame.destroy()
        self._frame = frame
        frame.pack(fill="both", expand=True)

    def show_login(self):
        self.session = None
        self._swap(LoginFrame(self, self))

    def show_main(self, session):
        self.session = session
        self._swap((AdminFrame if session.is_admin() else UserFrame)(self, self))


if __name__ == "__main__":
    App().mainloop()
