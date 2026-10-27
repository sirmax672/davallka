"""Encrypted server-side storage for bounded OIDC authorization sessions."""
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import secrets
import sqlite3
import time

from cryptography.fernet import Fernet, InvalidToken


class OIDCSessionStore:
    """Keep provider tokens out of the signed browser cookie."""

    def __init__(self, database_path, application_secret, issuer, lifetime_seconds=43200):
        self.database_path = database_path
        self.issuer = issuer
        self.lifetime_seconds = lifetime_seconds
        key = hmac.new(
            application_secret.encode("utf-8"),
            b"davallka-oidc-session-token-encryption-v1",
            hashlib.sha256,
        ).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(key))
        with self.connection() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS oidc_sessions (
                    session_id TEXT PRIMARY KEY,
                    issuer TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    encrypted_token BLOB NOT NULL,
                    created_at REAL NOT NULL,
                    checked_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )"""
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(oidc_sessions)")}
            if "display_name" not in columns:
                db.execute("ALTER TABLE oidc_sessions ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _minimal_token(token):
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("OIDC provider did not return an access token")
        now = time.time()
        expires_at = token.get("expires_at")
        if not isinstance(expires_at, (int, float)):
            expires_in = token.get("expires_in", 300)
            expires_at = now + max(1, int(expires_in))
        result = {
            "access_token": access_token,
            "token_type": token.get("token_type", "Bearer"),
            "expires_at": float(expires_at),
        }
        refresh_token = token.get("refresh_token")
        if isinstance(refresh_token, str) and refresh_token:
            result["refresh_token"] = refresh_token
        return result

    def _encrypt(self, token):
        return self.cipher.encrypt(json.dumps(self._minimal_token(token)).encode("utf-8"))

    def _decrypt(self, value):
        return json.loads(self.cipher.decrypt(value).decode("utf-8"))

    def create(self, subject, token, checked_at=None, display_name=""):
        if not isinstance(subject, str) or not subject:
            raise ValueError("OIDC provider did not return a stable subject")
        if not isinstance(display_name, str):
            display_name = ""
        display_name = " ".join(display_name.split())[:120]
        session_id = secrets.token_urlsafe(32)
        now = time.time()
        with self.connection() as db:
            db.execute("DELETE FROM oidc_sessions WHERE expires_at<=?", (now,))
            db.execute(
                "INSERT INTO oidc_sessions(session_id,issuer,subject,display_name,encrypted_token,created_at,checked_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    self.issuer,
                    subject,
                    display_name,
                    self._encrypt(token),
                    now,
                    checked_at if checked_at is not None else now,
                    now + self.lifetime_seconds,
                ),
            )
            db.commit()
        return session_id

    def get(self, session_id):
        if not isinstance(session_id, str) or len(session_id) > 128:
            return None
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM oidc_sessions WHERE session_id=? AND issuer=?",
                (session_id, self.issuer),
            ).fetchone()
        if not row:
            return None
        if row["expires_at"] <= time.time():
            self.revoke(session_id)
            return None
        try:
            token = self._decrypt(row["encrypted_token"])
        except (InvalidToken, ValueError, json.JSONDecodeError):
            self.revoke(session_id)
            return None
        return {
            "session_id": row["session_id"],
            "issuer": row["issuer"],
            "subject": row["subject"],
            "display_name": row["display_name"],
            "token": token,
            "created_at": row["created_at"],
            "checked_at": row["checked_at"],
            "expires_at": row["expires_at"],
        }

    def update(self, session_id, token, checked_at=None, display_name=None):
        if display_name is None:
            with self.connection() as db:
                db.execute(
                    "UPDATE oidc_sessions SET encrypted_token=?,checked_at=? WHERE session_id=? AND issuer=?",
                    (
                        self._encrypt(token),
                        checked_at if checked_at is not None else time.time(),
                        session_id,
                        self.issuer,
                    ),
                )
            return
        if not isinstance(display_name, str):
            display_name = ""
        display_name = " ".join(display_name.split())[:120]
        with self.connection() as db:
            db.execute(
                "UPDATE oidc_sessions SET encrypted_token=?,checked_at=?,display_name=? WHERE session_id=? AND issuer=?",
                (
                    self._encrypt(token),
                    checked_at if checked_at is not None else time.time(),
                    display_name,
                    session_id,
                    self.issuer,
                ),
            )
            db.commit()

    def revoke(self, session_id):
        with self.connection() as db:
            db.execute("DELETE FROM oidc_sessions WHERE session_id=?", (session_id,))
            db.commit()
