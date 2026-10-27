"""Persistent OAuth state for the remote d'Avallka MCP resource server."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import secrets
import sqlite3
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizeError,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


SCOPES = {"images:manage", "offline_access"}
ACCESS_TOKEN_TTL = 15 * 60
REFRESH_TOKEN_TTL = 30 * 24 * 60 * 60
AUTHORIZATION_TTL = 10 * 60
MAX_REGISTERED_CLIENTS = 1000


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _redirect_with(uri: str, values: dict[str, str]) -> str:
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend(values.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


class OAuthState:
    """Implements the MCP SDK provider contract with hashed one-use secrets."""

    def __init__(self, database_path, issuer_url: str, resource_url: str, consent_url: str):
        self.database_path = str(database_path)
        self.issuer_url = issuer_url.rstrip("/")
        self.resource_url = resource_url.rstrip("/")
        self.consent_url = consent_url.rstrip("/")
        self.subject_validator = None
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS oauth_clients (
                    client_id TEXT PRIMARY KEY,
                    metadata_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_pending (
                    pending_hash TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_codes (
                    code_hash TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    code_json TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    token_hash TEXT PRIMARY KEY,
                    token_type TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS oauth_tokens_owner
                    ON oauth_tokens(client_id, subject, token_type);
                """
            )

    def set_subject_validator(self, validator):
        """Install a live authorization check for subjects owning MCP tokens."""
        self.subject_validator = validator

    async def subject_is_authorized(self, subject):
        return self.subject_validator is None or await self.subject_validator(subject)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
        finally:
            db.close()

    def _prune(self, db, now: int):
        db.execute("DELETE FROM oauth_pending WHERE expires_at < ?", (now,))
        db.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (now,))
        db.execute("DELETE FROM oauth_tokens WHERE expires_at < ?", (now,))

    @staticmethod
    def _client_payload(client):
        return client.model_dump(mode="json", exclude_none=False)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self.connection() as db:
            row = db.execute("SELECT metadata_json FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
        if not row:
            return None
        return OAuthClientInformationFull.model_validate_json(row["metadata_json"])

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        redirects = client_info.redirect_uris or []
        if not redirects or len(redirects) > 10:
            raise RegistrationError("invalid_redirect_uri", "Register between 1 and 10 redirect URIs")
        for redirect in redirects:
            parts = urlsplit(str(redirect))
            if parts.username or parts.password or parts.fragment:
                raise RegistrationError("invalid_redirect_uri", "Redirect URIs cannot contain credentials or fragments")
            loopback = (parts.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
            if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
                raise RegistrationError("invalid_redirect_uri", "Redirect URIs must use HTTPS or a loopback HTTP address")
        if client_info.response_types != ["code"]:
            raise RegistrationError("invalid_client_metadata", "Only the authorization code flow is supported")
        if not set(client_info.grant_types) <= {"authorization_code", "refresh_token"}:
            raise RegistrationError("invalid_client_metadata", "Only authorization code and refresh token grants are supported")
        if client_info.token_endpoint_auth_method not in {"none", "client_secret_post", "client_secret_basic"}:
            raise RegistrationError("invalid_client_metadata", "Unsupported client authentication method")
        name = (client_info.client_name or "").strip()
        if len(name) > 120:
            raise RegistrationError("invalid_client_metadata", "Client name must be at most 120 characters")
        if client_info.scope:
            requested = set(client_info.scope.split())
            if not requested <= SCOPES or "images:manage" not in requested:
                raise RegistrationError("invalid_client_metadata", "Only the images:manage and offline_access scopes are supported")
        payload = _json(self._client_payload(client_info))
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                count = db.execute("SELECT count(*) FROM oauth_clients").fetchone()[0]
                if count >= MAX_REGISTERED_CLIENTS:
                    raise RegistrationError("invalid_client_metadata", "The registered client limit has been reached")
                db.execute(
                    "INSERT INTO oauth_clients(client_id,metadata_json,created_at) VALUES(?,?,?)",
                    (client_info.client_id, payload, int(time.time())),
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        client_redirects = {str(uri) for uri in (client.redirect_uris or [])}
        redirect_uri = str(params.redirect_uri)
        if redirect_uri not in client_redirects:
            raise AuthorizeError("invalid_request", "The redirect URI is not registered for this client")
        if params.resource != self.resource_url:
            raise AuthorizeError("invalid_target", "The requested resource does not match this MCP server")
        scopes = list(params.scopes or (client.scope or "images:manage offline_access").split())
        client_scopes = set((client.scope or "images:manage offline_access").split())
        if not set(scopes) <= SCOPES or not set(scopes) <= client_scopes or "images:manage" not in scopes:
            raise AuthorizeError("invalid_scope", "The requested scopes are not available")
        payload = {
            "client_id": client.client_id,
            "redirect_uri": redirect_uri,
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "state": params.state,
            "code_challenge": params.code_challenge,
            "resource": self.resource_url,
            "scopes": scopes,
        }
        pending = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db, now)
            db.execute(
                "INSERT INTO oauth_pending(pending_hash,client_id,payload_json,expires_at) VALUES(?,?,?,?)",
                (_hash(pending), client.client_id, _json(payload), now + AUTHORIZATION_TTL),
            )
            db.execute("COMMIT")
        return f"{self.consent_url}/{pending}"

    def pending_consent(self, pending: str):
        with self.connection() as db:
            row = db.execute(
                "SELECT client_id,payload_json,expires_at FROM oauth_pending WHERE pending_hash=?",
                (_hash(pending),),
            ).fetchone()
            if not row or row["expires_at"] < int(time.time()):
                if row:
                    db.execute("DELETE FROM oauth_pending WHERE pending_hash=?", (_hash(pending),))
                return None
            client_row = db.execute(
                "SELECT metadata_json FROM oauth_clients WHERE client_id=?", (row["client_id"],)
            ).fetchone()
        if not client_row:
            return None
        return json.loads(row["payload_json"]), OAuthClientInformationFull.model_validate_json(client_row["metadata_json"])

    def finish_consent(self, pending: str, subject: str, approved: bool):
        now = int(time.time())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT client_id,payload_json,expires_at FROM oauth_pending WHERE pending_hash=?",
                (_hash(pending),),
            ).fetchone()
            if not row or row["expires_at"] < now:
                db.execute("ROLLBACK")
                return None
            payload = json.loads(row["payload_json"])
            db.execute("DELETE FROM oauth_pending WHERE pending_hash=?", (_hash(pending),))
            if approved:
                code_value = secrets.token_urlsafe(32)
                code = AuthorizationCode(
                    code=code_value,
                    scopes=payload["scopes"],
                    expires_at=now + AUTHORIZATION_TTL,
                    client_id=row["client_id"],
                    code_challenge=payload["code_challenge"],
                    redirect_uri=payload["redirect_uri"],
                    redirect_uri_provided_explicitly=payload["redirect_uri_provided_explicitly"],
                    resource=payload["resource"],
                    subject=subject,
                )
                db.execute(
                    "INSERT INTO oauth_codes(code_hash,client_id,code_json,expires_at) VALUES(?,?,?,?)",
                    (_hash(code_value), row["client_id"], _json(code.model_dump(mode="json", exclude={"code"})), int(code.expires_at)),
                )
                params = {"code": code_value}
            else:
                params = {"error": "access_denied", "error_description": "The user denied access"}
            if payload["state"] is not None:
                params["state"] = payload["state"]
            params["iss"] = self.issuer_url
            db.execute("COMMIT")
        return _redirect_with(payload["redirect_uri"], params)

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT code_json,expires_at FROM oauth_codes WHERE code_hash=? AND client_id=?",
                (_hash(authorization_code), client.client_id),
            ).fetchone()
        if not row or row["expires_at"] < int(time.time()):
            return None
        payload = json.loads(row["code_json"])
        payload["code"] = authorization_code
        return AuthorizationCode.model_validate(payload)

    def _insert_tokens(self, db, client_id: str, subject: str, scopes: list[str], resource: str):
        now = int(time.time())
        access = "davallka_at_" + secrets.token_urlsafe(48)
        refresh = "davallka_rt_" + secrets.token_urlsafe(48)
        db.executemany(
            "INSERT INTO oauth_tokens(token_hash,token_type,client_id,subject,scopes_json,resource,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)",
            [
                (_hash(access), "access", client_id, subject, _json(scopes), resource, now + ACCESS_TOKEN_TTL, now),
                (_hash(refresh), "refresh", client_id, subject, _json(scopes), resource, now + REFRESH_TOKEN_TTL, now),
            ],
        )
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TOKEN_TTL, scope=" ".join(scopes), refresh_token=refresh)

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        now = int(time.time())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT code_json,expires_at FROM oauth_codes WHERE code_hash=? AND client_id=?",
                (_hash(authorization_code.code), client.client_id),
            ).fetchone()
            if not row or row["expires_at"] < now:
                db.execute("ROLLBACK")
                raise TokenError("invalid_grant", "The authorization code is invalid or has expired")
            payload = json.loads(row["code_json"])
            payload["code"] = authorization_code.code
            stored = AuthorizationCode.model_validate(payload)
            if stored.resource != self.resource_url or not stored.subject:
                db.execute("ROLLBACK")
                raise TokenError("invalid_grant", "The authorization code is not valid for this resource")
            db.execute("DELETE FROM oauth_codes WHERE code_hash=?", (_hash(stored.code),))
            db.execute("COMMIT")
        if not await self.subject_is_authorized(stored.subject):
            raise TokenError("invalid_grant", "The administrator authorization is no longer active")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            result = self._insert_tokens(db, client.client_id, stored.subject, stored.scopes, stored.resource)
            db.execute("COMMIT")
        return result

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        now = int(time.time())
        with self.connection() as db:
            row = db.execute(
                "SELECT client_id,subject,scopes_json,resource,expires_at FROM oauth_tokens WHERE token_hash=? AND token_type='refresh'",
                (_hash(refresh_token),),
            ).fetchone()
        if not row or row["client_id"] != client.client_id or row["expires_at"] < now:
            return None
        if not await self.subject_is_authorized(row["subject"]):
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            subject=row["subject"],
            scopes=json.loads(row["scopes_json"]),
            resource=row["resource"],
            expires_at=row["expires_at"],
        )

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        if not await self.subject_is_authorized(refresh_token.subject):
            raise TokenError("invalid_grant", "The administrator authorization is no longer active")
        now = int(time.time())
        token_hash = _hash(refresh_token.token)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT client_id,subject,scopes_json,resource,expires_at FROM oauth_tokens WHERE token_hash=? AND token_type='refresh'",
                (token_hash,),
            ).fetchone()
            if not row or row["client_id"] != client.client_id or row["expires_at"] < now or row["resource"] != self.resource_url:
                db.execute("ROLLBACK")
                raise TokenError("invalid_grant", "The refresh token is invalid or has expired")
            old_scopes = json.loads(row["scopes_json"])
            new_scopes = scopes or old_scopes
            if not set(new_scopes) <= set(old_scopes) or "images:manage" not in new_scopes:
                db.execute("ROLLBACK")
                raise TokenError("invalid_scope", "The refresh request cannot increase the approved scopes")
            db.execute("DELETE FROM oauth_tokens WHERE token_hash=?", (token_hash,))
            result = self._insert_tokens(db, client.client_id, row["subject"], new_scopes, row["resource"])
            db.execute("COMMIT")
        return result

    async def load_access_token(self, token: str) -> AccessToken | None:
        now = int(time.time())
        with self.connection() as db:
            row = db.execute(
                "SELECT client_id,subject,scopes_json,resource,expires_at FROM oauth_tokens WHERE token_hash=? AND token_type='access'",
                (_hash(token),),
            ).fetchone()
        if not row or row["expires_at"] < now:
            return None
        if not await self.subject_is_authorized(row["subject"]):
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            subject=row["subject"],
            scopes=json.loads(row["scopes_json"]),
            resource=row["resource"],
            expires_at=row["expires_at"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM oauth_tokens WHERE client_id=? AND subject=?",
                (token.client_id, token.subject or ""),
            )
            db.execute("COMMIT")
