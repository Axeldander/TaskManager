# Decisions made while building

Everything below was decided during the build, on top of `task_manager_spec_v2.md`.

## Technology
- Language: Python
- Storage: SQLite via the built-in `sqlite3` module (no ORM); datetimes stored as ISO 8601 UTC text
- IDs: UUID strings (uuid4)
- Datetimes: timezone-aware UTC internally; local time is only used when showing or entering dates
- Interface: Tkinter desktop app (`gui.py`); the command-line interface (`cli.py`) is kept for debugging. Both use the same services and the same database

## Authentication
- Password hashing: scrypt via `hashlib` (standard library, no extra dependency)
- Sessions: random token (`secrets.token_urlsafe`), stored hashed in a `sessions` table; expires after 24 h (configurable); logout deletes it
- Expired sessions are deleted on every login and every authenticate
- Registration is open for now, including choosing the admin role (to be locked down later)

## Tasks and permissions
- Visibility: users see tasks assigned to them; admins see tasks they assigned or reassigned (plus any assigned to them); the status summary counts what the person can see
- Admins may update the status of, and cancel, any task by id; the transition rules apply to everyone
- Edited deadlines must be in the future; edits and reassignments are not blocked on finished tasks
- Overdue tasks are marked failed by a sweep that runs at the start of every task action (no background timer); `deadline <= now` counts as passed; `failed` is final even if the deadline is later extended

## Audit log
- One row per changed field (actor, task, action, field, old/new value, timestamp); task creation is logged as an "assign"; written in the same transaction as the change
- Status changes and the automatic `failed` are not audit-logged (the spec only lists edits, assignments and reassignments)

## GUI
- The task list refreshes every 30 s and after every action
- Admin-only controls are not created for regular users (the services still enforce roles on every call)

## Still open
- Pictures: the field exists on the model but nothing can set it yet; how pictures are handled is undecided
- Usernames are case-sensitive and trimmed on register but not on login
- The status change is read-then-write, with no guard against two processes acting at the same instant
