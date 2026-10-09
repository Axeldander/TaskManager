# Task Manager

Admins create and assign tasks; users work through the tasks assigned to them.
See `docs/task_manager_spec_v2.md` for the spec and `docs/decisions.md` for what was decided while building.

## Run it

No installs needed (standard library only). Python 3.9 or newer; developed and tested on 3.12.

    python gui.py        # desktop window (Tkinter)
    python cli.py        # text menu, handy for debugging

Both accept `--db PATH` to choose the SQLite file (default: `task_manager.db` in the folder you run from).
They share the same database, so accounts made in one work in the other.

First run: click "Create account" and register an administrator (registration is open for now),
then use **Users** to create more accounts and **New task** to assign work.

If `import tkinter` fails on Linux: `sudo apt install python3-tk`.

## Open in PyCharm

1. File > Open... and choose this folder (`task_manager`).
2. Make sure a Python interpreter is selected (Settings > Project > Python Interpreter).
3. Right-click `gui.py` > Run 'gui'.

The database file is created in the project folder and is ignored by git.

## How it is organised

Each layer only talks to the one below it:

    gui.py / cli.py     interfaces (no business rules; they only call the services)
    admin.py            admin actions: create user/task, edit, reassign, audit log views
    receiver.py         task actions for users and admins: list, status changes, cancel, summary
    auth.py             register, login, sessions, password hashing
    storage.py          SQLite tables and repositories
    models.py           User, Task, Session, AuditEntry, status rules
    errors.py           shared error types

Roles are enforced in `admin.py` and `receiver.py`, so a regular user cannot perform admin actions
even if an interface showed them the button.
