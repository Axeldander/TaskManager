# Task Manager — v0.1 Implementation

Implements the functions specified in `task_manager_spec.md` /
`requirements_and_system_specification.md`.

## Structure

| File | Purpose |
|---|---|
| `models.py` | `User`, `Task`, `Status` data structures + allowed status transitions |
| `storage.py` | JSON-file persistence (only module that touches `data.json`) |
| `auth.py` | `register()`, `login()` — FR-1, FR-2 |
| `access.py` | `can_access()` — FR-7 |
| `admin.py` | `create_user()`, `create_task()`, `assign_task()`, `edit_task()` — FR-3 to FR-6 |
| `receiver.py` | `update_status()`, `get_progress()`, `cancel_task()` — FR-8 to FR-10 |
| `main.py` | Scripted demo exercising every function end-to-end |
| `cli.py` | Interactive text menu |
| `gui.py` | **Tkinter graphical interface** (login, admin screen, user screen) |

## Running it

```bash
python gui.py     # graphical interface (recommended)
python cli.py     # interactive text menu
python main.py    # scripted demo (resets data.json)
```

The GUI needs nothing beyond standard Python (Tkinter ships with it). Optional:
`pip install pillow` to preview JPG pictures (PNG/GIF preview works without it).

### GUI overview

- **Log in / Create an account** on the first screen.
- **Admin screen:** task table, New task, Edit, Reassign, Cancel task, Progress
  overview (per user), New user, details panel with picture and History.
- **User screen:** your tasks, a status dropdown that only offers the moves the
  rules allow, Cancel task, progress bar, details panel with picture and History.
- Pictures are copied into an `images/` folder next to the code, so a task keeps
  its picture even if the original file moves.

The demo script:

This resets `data.json` and runs through: registering an admin and two
users, creating a task, updating its status, editing it, marking it done,
attempting an invalid transition (rejected), attempting a cross-user
access violation (rejected), and printing a progress summary.

## Design decisions made to unblock implementation

These were left as open questions in the spec sheet. I picked reasonable
defaults so the code could be written.

1. **Status transitions:** `todo → ongoing → done`; any active task can go
   to `cancelled` or `failed`; `done`/`failed`/`cancelled` are terminal.
   Defined in `models.ALLOWED_TRANSITIONS` — edit that dict to change the
   rules.
2. **Failed trigger:** computed lazily — whenever a task is read (status
   update, progress check), if its deadline has passed and it's not
   already terminal, it flips to `failed` automatically. See
   `receiver._apply_lazy_failure()`.
3. **Cancellation rights:** both the assigned user and admin can cancel.
   Enforced in `receiver.cancel_task()` via the same transition graph.
4. **Storage:** local JSON file (`data.json`), created automatically on
   first run. Swap `storage.py` for a different backend (e.g. SQLite)
   without touching any other module if needed.
5. **Reassignment behavior:** reassigning a task resets its status to
   `todo` (see `admin.assign_task()`) — change this if your team decides
   in-progress work should be preserved across reassignment.

## Not yet implemented (out of scope for this pass)

- Restricting who may register as `admin` (currently anyone can — see spec audit)
- Admins can still change task status through the backend (spec says only the assignee)
- Automated test suite (recommended next step — see below)

## TODO

1. Confirm or override the design decisions above as a team, and update
   this README + the spec sheet's Section 9 accordingly.
2. Write unit tests against the acceptance criteria in the spec sheet
   (e.g. `pytest`), one test per checklist item.
3. Decide on a real user-facing interface (CLI menu vs. simple web
   frontend with Flask) and build it on top of these modules — none of
   the business logic above needs to change.
