# Task Manager Spec

A task manager where admins create and assign tasks, and users (receivers) work through the tasks assigned to them.

## Who uses this

- **Admin**, creates users, creates tasks, assigns them to a single user, edits tasks
- **User (receiver)**, updates the status of their own tasks, cancels tasks, checks their progress

## Authentication

A user can register with a username, password, email, and a role (admin or user). Usernames have to be unique.

A user can log in with their username and password and gets a session back that represents them.

Rules:
- Passwords get hashed before they're stored. Never store or log them in plain text.
- If login fails, the error should just say "invalid credentials", not reveal whether it was the username or the password that was wrong.
- Registering with a username that already exists should be rejected.

## Admin stuff

An admin can:
- create new user accounts
- create a task (title, description, deadline, assigned to one user)
- reassign a task to someone else
- edit a task's title, description, or deadline

Rules:
- Only an admin can do any of this. Every action checks the role first.
- A task's deadline has to be in the future when it's created.
- Every edit, assignment or reassignment gets logged (who did it, what changed, when).

## Receiver stuff

A user can:
- update the status of a task that's assigned to them
- view a summary of their tasks by status
- cancel a task

Rules:
- A user can only touch tasks assigned to them. Not anyone else's.
- Both the admin and the assigned user are allowed to cancel a task.
- Status changes follow fixed rules, see below.

## Status rules

A task can be: todo, ongoing, done, failed, cancelled.

| From | Can move to |
|---|---|
| todo | ongoing, cancelled |
| ongoing | done, cancelled |
| done | nothing, it's finished |
| failed | nothing |
| cancelled | nothing |

So a task always has to go todo -> ongoing -> done, no skipping straight to done.

Failed isn't something a user picks. It happens automatically if the deadline passes and the task still isn't done.

## Task fields

- id (unique)
- title
- description
- assignment_date (set automatically when the task is created, can't be changed afterward)
- deadline (has to be after assignment_date)
- status (todo / ongoing / done / failed / cancelled)
- assigned_user (just one person)
- picture (optional)

## Still open

Nothing left from Phase 1-2, everything above was decided as a team. If anything new comes up while building (storage choice, how pictures get handled, etc), add it here before just going with whatever's easiest.
