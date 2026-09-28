"""
Authentication module.

Implements FR-1 (Register) and FR-2 (Login), and satisfies NFR-1
(passwords must never be stored or logged in plaintext).
"""

import hashlib
import os
import re
from dataclasses import dataclass

from models import User, Role
import storage

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """Raised for any registration/login failure."""
    pass


@dataclass
class Session:
    """Represents an authenticated user for the duration of an action."""
    username: str
    role: Role

    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100_000
    ).hex()


def _validate_email(email: str) -> bool:
    return bool(EMAIL_PATTERN.match(email))


def register(username: str, password: str, email: str, role: str) -> Session:
    """
    FR-1: Create a new user account.

    Raises AuthError if the username already exists, the email is
    invalid, or the role is not 'admin'/'user'.
    """
    if role not in (Role.ADMIN.value, Role.USER.value):
        raise AuthError(f"invalid role: {role}")
    if not _validate_email(email):
        raise AuthError("invalid email format")

    users = storage.load_users()
    if username in users:
        raise AuthError("username already exists")

    salt = os.urandom(16).hex()
    password_hash = _hash_password(password, salt)

    user = User(
        id=storage.new_id(),
        username=username,
        password_hash=password_hash,
        salt=salt,
        email=email,
        role=Role(role),
    )
    users[username] = user
    storage.save_users(users)

    return Session(username=username, role=user.role)


def login(username: str, password: str) -> Session:
    """
    FR-2: Authenticate a user and return a Session.

    Raises AuthError with a generic message on any failure, without
    revealing whether the username or the password was wrong.
    """
    users = storage.load_users()
    user = users.get(username)
    if user is None:
        raise AuthError("invalid credentials")

    if _hash_password(password, user.salt) != user.password_hash:
        raise AuthError("invalid credentials")

    return Session(username=username, role=user.role)
