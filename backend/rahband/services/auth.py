from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock


class AuthStore:
    def __init__(self, path: str | Path, session_hours: int = 12) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_hours = session_hours
        self._lock = Lock()
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at)")
            connection.execute("PRAGMA optimize")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _password_hash(password: str, salt: bytes | None = None) -> str:
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        actual_salt = salt or secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode("utf-8"), salt=actual_salt, n=2**14, r=8, p=1, dklen=32)
        return f"scrypt$16384$8$1${actual_salt.hex()}${digest.hex()}"

    @classmethod
    def _verify_password(cls, password: str, stored: str) -> bool:
        try:
            algorithm, n, r, p, salt_hex, expected = stored.split("$")
            if algorithm != "scrypt":
                return False
            digest = hashlib.scrypt(
                password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                n=int(n), r=int(r), p=int(p), dklen=32,
            ).hex()
            return hmac.compare_digest(digest, expected)
        except (ValueError, TypeError):
            return False

    def upsert_user(self, username: str, password: str) -> int:
        normalized = username.strip()
        if not 3 <= len(normalized) <= 64 or not all(char.isalnum() or char in "._-" for char in normalized):
            raise ValueError("username must be 3-64 letters, numbers, dot, dash or underscore")
        password_hash = self._password_hash(password)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO users(username,password_hash,enabled,created_at) VALUES(?,?,1,?) "
                "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, enabled=1",
                (normalized, password_hash, now),
            )
            row = connection.execute("SELECT id FROM users WHERE username=?", (normalized,)).fetchone()
            connection.execute("DELETE FROM sessions WHERE user_id=?", (row[0],))
        return int(row[0])

    def authenticate(self, username: str, password: str) -> tuple[str, dict[str, object]] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,username,password_hash FROM users WHERE username=? AND enabled=1", (username.strip(),),
            ).fetchone()
        if row is None or not self._verify_password(password, row[2]):
            return None
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=self.session_hours)
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at<=?", (now.isoformat(),))
            connection.execute(
                "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
                (token_hash, row[0], expires.isoformat(), now.isoformat()),
            )
        return token, {"id": int(row[0]), "username": str(row[1]), "expires_at": expires.isoformat()}

    def user_for_session(self, token: str | None) -> dict[str, object] | None:
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT users.id,users.username,sessions.expires_at FROM sessions "
                "JOIN users ON users.id=sessions.user_id "
                "WHERE sessions.token_hash=? AND sessions.expires_at>? AND users.enabled=1",
                (token_hash, now),
            ).fetchone()
        return {"id": int(row[0]), "username": str(row[1]), "expires_at": row[2]} if row else None

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def set_enabled(self, username: str, enabled: bool) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("UPDATE users SET enabled=? WHERE username=?", (enabled, username.strip()))
            if not enabled:
                connection.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE username=?)", (username.strip(),))
            return cursor.rowcount > 0

    def list_users(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT id,username,enabled,created_at FROM users ORDER BY username").fetchall()
        return [{"id": row[0], "username": row[1], "enabled": bool(row[2]), "created_at": row[3]} for row in rows]
