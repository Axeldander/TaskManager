"""
Task Manager — CLI demo.

This is a minimal command-line interface to exercise every function in
the system end to end. It is not the final UI (your project may add a
proper CLI menu, GUI, or web frontend) — it exists to prove the modules
work together correctly and to serve as a live example for your report.
"""

from datetime import datetime, timedelta

import storage
from auth import register, login, AuthError
from admin import create_task, assign_task, edit_task, AdminError
from receiver import update_status, get_progress, cancel_task, ReceiverError


def demo():
    storage.reset()  # start fresh each run of this demo

    print("=== Registering users ===")
    admin_session = register("alice", "adminpass123", "alice@example.com", "admin")
    register("bob", "userpass123", "bob@example.com", "user")
    bob_session = login("bob", "userpass123")
    print(f"Admin session: {admin_session}")
    print(f"Bob's session:  {bob_session}")

    print("\n=== Admin creates a task for bob ===")
    deadline = (datetime.now() + timedelta(days=3)).isoformat()
    task = create_task(
        admin_session,
        title="Write project report",
        description="Draft sections 1-3 of the SDD report",
        deadline=deadline,
        assigned_user="bob",
    )
    print(f"Created task {task.id}: {task.title} (status={task.status.value})")

    print("\n=== Bob updates status ===")
    task = update_status(bob_session, task.id, "ongoing")
    print(f"Task {task.id} status is now {task.status.value}")

    print("\n=== Bob checks progress ===")
    print(get_progress(bob_session))

    print("\n=== Admin edits the task ===")
    task = edit_task(admin_session, task.id, description="Draft sections 1-4 instead")
    print(f"Task {task.id} description updated: {task.description}")

    print("\n=== Bob marks it done ===")
    task = update_status(bob_session, task.id, "done")
    print(f"Task {task.id} status is now {task.status.value}")

    print("\n=== Attempting an invalid transition (done -> todo) ===")
    try:
        update_status(bob_session, task.id, "todo")
    except ReceiverError as e:
        print(f"Rejected as expected: {e}")

    print("\n=== Access control check: a second user cannot touch bob's task ===")
    register("carol", "carolpass123", "carol@example.com", "user")
    carol_session = login("carol", "carolpass123")
    try:
        update_status(carol_session, task.id, "cancelled")
    except ReceiverError as e:
        print(f"Rejected as expected: {e}")

    print("\n=== Final progress summary for bob ===")
    print(get_progress(bob_session))


if __name__ == "__main__":
    demo()
