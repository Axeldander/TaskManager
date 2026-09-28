"""
Storage layer for the Task Manager system.

Uses a single local JSON file as persistence. This satisfies the system
constraint (Section 5 of the spec sheet) of not requiring an external
database for a course-scoped project, while still persisting data between
runs.

This module is intentionally the ONLY place that reads/writes the data
file, so storage could later be swapped for SQLite or another backend
without changing any other module.
"""

import json
import os
import shutil
import uuid
from typing import Dict

from models import User, Task

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data.json")
IMAGES_DIR = os.path.join(BASE_DIR, "images")


def _empty_data() -> Dict:
    return {"users": {}, "tasks": {}}


def load_data() -> Dict:
    if not os.path.exists(DATA_FILE):
        return _empty_data()
    with open(DATA_FILE, "r") as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError:
            return _empty_data()
    return raw


def save_data(data: Dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_users() -> Dict[str, User]:
    raw = load_data().get("users", {})
    return {uname: User.from_dict(u) for uname, u in raw.items()}


def load_tasks() -> Dict[str, Task]:
    raw = load_data().get("tasks", {})
    return {tid: Task.from_dict(t) for tid, t in raw.items()}


def save_users(users: Dict[str, User]):
    data = load_data()
    data["users"] = {uname: u.to_dict() for uname, u in users.items()}
    save_data(data)


def save_tasks(tasks: Dict[str, Task]):
    data = load_data()
    data["tasks"] = {tid: t.to_dict() for tid, t in tasks.items()}
    save_data(data)


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def reset():
    """Wipe all stored data. Useful for tests/demos."""
    save_data(_empty_data())


def resolve_picture_path(path: str) -> str:
    """Turn a stored picture reference into a real filesystem path.
    Relative paths (e.g. 'images/ab12cd34.png') are relative to the app folder."""
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


def import_picture(src: str) -> str:
    """
    Copy an image into the app's images/ folder and return the relative
    reference to store on the task. Copying means the task keeps its
    picture even if the original file is moved or deleted.
    """
    ext = os.path.splitext(src)[1].lower()
    os.makedirs(IMAGES_DIR, exist_ok=True)
    name = f"{new_id()}{ext}"
    shutil.copy2(src, os.path.join(IMAGES_DIR, name))
    return f"images/{name}"
