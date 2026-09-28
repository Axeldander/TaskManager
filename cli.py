"""
Task Manager — Interactive CLI.

Unlike main.py (a scripted demo), this lets you actually type in your own
inputs: register, log in, and use either the admin menu or the receiver
menu depending on your role.

Run with:
    python3 cli.py
"""

from auth import register, login, AuthError, Session
from admin import create_user, create_task, assign_task, edit_task, AdminError
from receiver import (
    update_status, get_progress, cancel_task, list_tasks, ReceiverError,
)


def pause():
    input("\nPress Enter to continue...")


def prompt_deadline() -> str:
    """Ask for a deadline as a simple number of days from now."""
    from datetime import datetime, timedelta
    days = input("Deadline — how many days from now? (e.g. 3): ").strip()
    try:
        days_int = int(days)
    except ValueError:
        days_int = 3
        print("Invalid number, defaulting to 3 days.")
    return (datetime.now() + timedelta(days=days_int)).isoformat()


def show_tasks(tasks):
    """Display tasks as a numbered list (1, 2, 3...) rather than raw IDs."""
    if not tasks:
        print("(no tasks to show)")
        return
    for i, t in enumerate(tasks, start=1):
        print(f"  {i}. {t.title!r} — status={t.status.value}, "
              f"assigned_to={t.assigned_user}, deadline={t.deadline}")


def select_task(session, action_label="select"):
    """
    Show the accessible tasks as a numbered list and let the user pick
    one by number. Returns the underlying task_id (or None if the list
    is empty or the choice was invalid).
    """
    tasks = list_tasks(session)
    if not tasks:
        print("(no tasks to show)")
        return None

    show_tasks(tasks)
    choice = input(f"Enter the number of the task to {action_label}: ").strip()
    try:
        index = int(choice) - 1
        if index < 0 or index >= len(tasks):
            raise ValueError
    except ValueError:
        print("Invalid selection.")
        return None

    return tasks[index].id


def welcome_menu():
    while True:
        print("\n=== Task Manager ===")
        print("1. Register")
        print("2. Login")
        print("3. Exit")
        choice = input("> ").strip()

        if choice == "1":
            username = input("Username: ").strip()
            password = input("Password: ").strip()
            email = input("Email: ").strip()
            role = input("Role (admin/user): ").strip().lower()
            try:
                session = register(username, password, email, role)
                print(f"Registered! You're logged in as {session.username} "
                      f"({session.role.value}).")
                return session
            except AuthError as e:
                print(f"Error: {e}")

        elif choice == "2":
            username = input("Username: ").strip()
            password = input("Password: ").strip()
            try:
                session = login(username, password)
                print(f"Logged in as {session.username} ({session.role.value}).")
                return session
            except AuthError as e:
                print(f"Error: {e}")

        elif choice == "3":
            return None
        else:
            print("Invalid choice.")


def admin_menu(session: Session):
    while True:
        print(f"\n=== Admin Menu ({session.username}) ===")
        print("1. Create user")
        print("2. Create task")
        print("3. Assign / reassign task")
        print("4. Edit task")
        print("5. View all tasks")
        print("6. View progress (all / one user)")
        print("7. Logout")
        choice = input("> ").strip()

        try:
            if choice == "1":
                username = input("New username: ").strip()
                password = input("Password: ").strip()
                email = input("Email: ").strip()
                role = input("Role (admin/user): ").strip().lower()
                create_user(session, username, password, email, role)
                print("User created.")

            elif choice == "2":
                title = input("Title: ").strip()
                description = input("Description: ").strip()
                assigned_user = input("Assign to (username): ").strip()
                deadline = prompt_deadline()
                task = create_task(session, title, description, deadline, assigned_user)
                print(f"Task created: {task.id}")

            elif choice == "3":
                task_id = select_task(session, action_label="reassign")
                if task_id is not None:
                    user_id = input("New assignee (username): ").strip()
                    task = assign_task(session, task_id, user_id)
                    print(f"{task.title!r} reassigned to {user_id}.")

            elif choice == "4":
                task_id = select_task(session, action_label="edit")
                if task_id is not None:
                    field = input("Field to edit (title/description/deadline): ").strip()
                    if field == "deadline":
                        value = prompt_deadline()
                    else:
                        value = input("New value: ").strip()
                    task = edit_task(session, task_id, **{field: value})
                    print(f"{task.title!r} updated.")

            elif choice == "5":
                show_tasks(list_tasks(session))

            elif choice == "6":
                username = input("View progress for username (blank = all): ").strip()
                print(get_progress(session, username or None))

            elif choice == "7":
                return
            else:
                print("Invalid choice.")

        except (AdminError, ReceiverError) as e:
            print(f"Error: {e}")

        pause()


def receiver_menu(session: Session):
    while True:
        print(f"\n=== My Tasks ({session.username}) ===")
        print("1. View my tasks")
        print("2. Update task status")
        print("3. View my progress")
        print("4. Cancel a task")
        print("5. Logout")
        choice = input("> ").strip()

        try:
            if choice == "1":
                show_tasks(list_tasks(session))

            elif choice == "2":
                task_id = select_task(session, action_label="update")
                if task_id is not None:
                    new_status = input(
                        "New status (todo/ongoing/done/failed/cancelled): "
                    ).strip().lower()
                    task = update_status(session, task_id, new_status)
                    print(f"{task.title!r} is now {task.status.value}.")

            elif choice == "3":
                print(get_progress(session))

            elif choice == "4":
                task_id = select_task(session, action_label="cancel")
                if task_id is not None:
                    reason = input("Reason (optional): ").strip()
                    task = cancel_task(session, task_id, reason or None)
                    print(f"{task.title!r} cancelled.")

            elif choice == "5":
                return
            else:
                print("Invalid choice.")

        except ReceiverError as e:
            print(f"Error: {e}")

        pause()


def main():
    print("Welcome! Your data is saved in data.json and persists between runs.")
    while True:
        session = welcome_menu()
        if session is None:
            print("Goodbye!")
            return

        if session.is_admin():
            admin_menu(session)
        else:
            receiver_menu(session)


if __name__ == "__main__":
    main()
