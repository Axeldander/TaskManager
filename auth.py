"""Authentication: registration, login, and token-based sessions stored in SQLite.

Passwords are hashed with scrypt (standard library) before storage and are never
stored or logged in plain text.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable

from models import Role, Session, User
from storage import DuplicateUsernameError, SessionRepository, UserRepository

# scrypt parameters (~16 MB of memory per hash)
_N, _R, _P = 2**14, 8, 1
_SALT_BYTES = 16


class InvalidCredentialsError(Exception):
    """Login failed. The message never says whether username or password was wrong."""

    def __init__(self) -> None:
        super().__init__("invalid credentials")


class InvalidSessionError(Exception):
    """The session token is unknown or expired."""


# --- password hashing ------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return "$".join(
        ["scrypt", str(_N), str(_R), str(_P),
         base64.b64encode(salt).decode(), base64.b64encode(digest).decode()]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_b64, digest_b64 = stored.split("$")
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- service ---------------------------------------------------------------

class AuthService:
    def __init__(
        self,
        users: UserRepository,
        sessions: SessionRepository,
        session_ttl: timedelta = timedelta(hours=24),
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._ttl = session_ttl
        self._clock = clock
        # Used so a login for an unknown username takes about as long as a real one.
        self._dummy_hash = hash_password("dummy-password")

    def register(self, username: str, password: str, email: str, role: Role | str) -> User:
        if not username.strip() or not password or not email.strip():
            raise ValueError("username, password and email are required")
        user = User(
            username=username.strip(),
            password_hash=hash_password(password),
            email=email.strip(),
            role=Role(role),
        )
        self._users.add(user)  # raises DuplicateUsernameError if the username is taken
        return user

    def login(self, username: str, password: str) -> str:
        """Return a new session token, or raise InvalidCredentialsError."""
        self._sessions.delete_expired(self._clock())
        user = self._users.get_by_username(username)
        stored = user.password_hash if user else self._dummy_hash
        password_ok = verify_password(password, stored)
        if user is None or not password_ok:
            raise InvalidCredentialsError()

        token = secrets.token_urlsafe(32)
        now = self._clock()
        self._sessions.add(
            Session(
                token_hash=_hash_token(token),
                user_id=user.id,
                created_at=now,
                expires_at=now + self._ttl,
            )
        )
        return token

    def authenticate(self, token: str) -> User:
        """Return the user a session token represents, or raise InvalidSessionError."""
        self._sessions.delete_expired(self._clock())  # stale rows are removed here
        session = self._sessions.get(_hash_token(token))
        if session is None or session.expires_at <= self._clock():
            raise InvalidSessionError()
        user = self._users.get_by_id(session.user_id)
        if user is None:
            raise InvalidSessionError()
        return user

    def logout(self, token: str) -> None:
        self._sessions.delete(_hash_token(token))
