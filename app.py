"""Browser UI and authenticated image API, sharing the same storage service."""
import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import secrets
import threading
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import quote, urlsplit

import httpx
from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from oauth_server import OAuthState, REFRESH_TOKEN_TTL
from oidc_sessions import OIDCSessionStore
from storage import Store, StorageError, safe_name, safe_path


class User(UserMixin):
    def __init__(self, username):
        self.id = username


class OIDCUnavailable(Exception):
    """The identity provider could not be safely consulted."""


def safe_local_redirect(target):
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or parts.username or parts.password:
        return None
    return target


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("FLASK_SECRET_KEY"),
        ADMIN_USERNAME=os.getenv("ADMIN_USERNAME", "admin"),
        ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD"),
        ADMIN_PASSWORD_HASH=os.getenv("ADMIN_PASSWORD_HASH"),
        AUTH_MODE=os.getenv("AUTH_MODE", "local"),
        OIDC_ISSUER=os.getenv("OIDC_ISSUER", "").strip(),
        OIDC_CLIENT_ID=os.getenv("OIDC_CLIENT_ID", ""),
        OIDC_CLIENT_SECRET=os.getenv("OIDC_CLIENT_SECRET", ""),
        OIDC_ADMIN_GROUP=os.getenv("OIDC_ADMIN_GROUP", ""),
        OIDC_GROUP_CLAIM=os.getenv("OIDC_GROUP_CLAIM", "groups"),
        OIDC_REVALIDATE_SECONDS=int(os.getenv("OIDC_REVALIDATE_SECONDS", "30")),
        UPLOAD_FOLDER=os.getenv("UPLOAD_FOLDER", "uploads"),
        STATE_FOLDER=os.getenv("STATE_FOLDER", "state"),
        PUBLIC_BASE_URL=os.getenv("PUBLIC_BASE_URL", ""),
        API_TOKENS=json.loads(os.getenv("DAVALLKA_API_TOKENS", "{}")),
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_REQUEST_BYTES", str(101 * 1024**2))),
        MAX_FILE_BYTES=int(os.getenv("MAX_FILE_BYTES", str(100 * 1024**2))),
        MAX_STORAGE_BYTES=int(os.getenv("MAX_STORAGE_BYTES", str(10 * 1024**3))),
        MAX_FORM_MEMORY_SIZE=256 * 1024,
        MAX_FORM_PARTS=20,
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true").lower() == "true",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    if test_config:
        app.config.update(test_config)
    cfg = app.config
    if not cfg["SECRET_KEY"] or len(cfg["SECRET_KEY"]) < 32 or cfg["SECRET_KEY"] == "your-secret-key-change-this":
        raise RuntimeError("A random FLASK_SECRET_KEY of at least 32 characters is required")
    cfg["AUTH_MODE"] = str(cfg["AUTH_MODE"]).strip().lower()
    if cfg["AUTH_MODE"] not in {"local", "hybrid", "oidc"}:
        raise RuntimeError("AUTH_MODE must be local, hybrid, or oidc")
    local_auth_enabled = cfg["AUTH_MODE"] in {"local", "hybrid"}
    oidc_auth_enabled = cfg["AUTH_MODE"] in {"hybrid", "oidc"}
    if local_auth_enabled and not cfg["ADMIN_USERNAME"]:
        raise RuntimeError("ADMIN_USERNAME is required")
    if local_auth_enabled and not cfg["ADMIN_PASSWORD_HASH"]:
        if not cfg["ADMIN_PASSWORD"] or cfg["ADMIN_PASSWORD"] == "password123":
            raise RuntimeError("A non-example administrator password or hash is required")
        cfg["ADMIN_PASSWORD_HASH"] = generate_password_hash(cfg["ADMIN_PASSWORD"])
    cfg.pop("ADMIN_PASSWORD", None)
    if oidc_auth_enabled:
        issuer = urlsplit(cfg["OIDC_ISSUER"])
        if (
            issuer.scheme != "https"
            or not issuer.netloc
            or issuer.username
            or issuer.password
            or issuer.query
            or issuer.fragment
        ):
            raise RuntimeError("OIDC_ISSUER must be an HTTPS issuer URL")
        cfg["OIDC_ISSUER"] = cfg["OIDC_ISSUER"].rstrip("/") + "/"
        if not cfg["OIDC_CLIENT_ID"] or not cfg["OIDC_CLIENT_SECRET"] or not cfg["OIDC_ADMIN_GROUP"]:
            raise RuntimeError("OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, and OIDC_ADMIN_GROUP are required for OIDC login")
        if not cfg["OIDC_GROUP_CLAIM"] or not 1 <= cfg["OIDC_REVALIDATE_SECONDS"] <= 30:
            raise RuntimeError("OIDC_GROUP_CLAIM is required and OIDC_REVALIDATE_SECONDS must be between 1 and 30")
    base = urlsplit(cfg["PUBLIC_BASE_URL"])
    if base.scheme not in {"https", "http"} or not base.netloc or base.username or base.password or base.query or base.fragment or base.path not in {"", "/"}:
        raise RuntimeError("PUBLIC_BASE_URL must be a trusted absolute origin")
    cfg["PUBLIC_BASE_URL"] = cfg["PUBLIC_BASE_URL"].rstrip("/")
    if not isinstance(cfg["API_TOKENS"], dict):
        raise RuntimeError("DAVALLKA_API_TOKENS must be a JSON token-to-scope map")
    for token, scopes in cfg["API_TOKENS"].items():
        if not isinstance(token, str) or len(token) < 32 or not isinstance(scopes, list) or not set(scopes) <= {"read", "write", "delete"}:
            raise RuntimeError("API tokens must be random and have explicit read/write/delete scopes")
    store = Store(cfg["UPLOAD_FOLDER"], cfg["STATE_FOLDER"], cfg["MAX_FILE_BYTES"], cfg["MAX_STORAGE_BYTES"])
    store.import_legacy()
    app.extensions["store"] = store
    oauth = OAuthState(
        store.db_path,
        cfg["PUBLIC_BASE_URL"],
        cfg["PUBLIC_BASE_URL"] + "/mcp",
        cfg["PUBLIC_BASE_URL"] + "/oauth/consent",
    )
    app.extensions["oauth_state"] = oauth
    oidc_client = None
    oidc_sessions = None
    if oidc_auth_enabled:
        from authlib.integrations.flask_client import OAuth

        oidc_registry = OAuth(app)
        discovery_url = cfg["OIDC_ISSUER"] + ".well-known/openid-configuration"
        oidc_client = oidc_registry.register(
            "authentik",
            client_id=cfg["OIDC_CLIENT_ID"],
            client_secret=cfg["OIDC_CLIENT_SECRET"],
            server_metadata_url=discovery_url,
            client_kwargs={
                "scope": "openid profile offline_access",
                "code_challenge_method": "S256",
                "default_timeout": 5.0,
                "token_endpoint_auth_method": "client_secret_basic",
            },
        )
        oidc_sessions = OIDCSessionStore(
            store.db_path,
            cfg["SECRET_KEY"],
            cfg["OIDC_ISSUER"],
            lifetime_seconds=REFRESH_TOKEN_TTL,
        )
        app.extensions["oidc_sessions"] = oidc_sessions

    def oidc_metadata():
        try:
            metadata = oidc_client.load_server_metadata()
        except Exception as exc:
            raise OIDCUnavailable from exc
        if (
            not isinstance(metadata, dict)
            or not isinstance(metadata.get("issuer"), str)
            or metadata["issuer"].rstrip("/") != cfg["OIDC_ISSUER"].rstrip("/")
        ):
            raise OIDCUnavailable
        required_endpoints = ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri")
        if any(not metadata.get(name) for name in required_endpoints):
            raise OIDCUnavailable
        for name in required_endpoints:
            endpoint = metadata[name]
            if not isinstance(endpoint, str):
                raise OIDCUnavailable
            parsed = urlsplit(endpoint)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                raise OIDCUnavailable
        return metadata

    def refresh_oidc_token(token, metadata):
        if token["expires_at"] > datetime.now().timestamp() + 60:
            return token
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            return None
        try:
            response = httpx.post(
                metadata["token_endpoint"],
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                auth=(cfg["OIDC_CLIENT_ID"], cfg["OIDC_CLIENT_SECRET"]),
                headers={"Accept": "application/json"},
                timeout=5.0,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise OIDCUnavailable from exc
        if response.status_code in {400, 401, 403}:
            return None
        if response.status_code != 200:
            raise OIDCUnavailable
        try:
            refreshed = response.json()
            refreshed = OIDCSessionStore._minimal_token(refreshed)
        except (ValueError, TypeError, json.JSONDecodeError):
            raise OIDCUnavailable from None
        if "refresh_token" not in refreshed:
            refreshed["refresh_token"] = refresh_token
        return refreshed

    def oidc_userinfo(token, metadata):
        try:
            response = httpx.get(
                metadata["userinfo_endpoint"],
                headers={"Authorization": "Bearer " + token["access_token"], "Accept": "application/json"},
                timeout=5.0,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise OIDCUnavailable from exc
        if response.status_code in {400, 401, 403}:
            return None
        if response.status_code != 200:
            raise OIDCUnavailable
        try:
            profile = response.json()
        except (ValueError, json.JSONDecodeError):
            raise OIDCUnavailable from None
        return profile if isinstance(profile, dict) else None

    def check_oidc_session(session_id):
        record = oidc_sessions.get(session_id)
        if not record:
            return False
        now = datetime.now().timestamp()
        token = record["token"]
        if (
            now - record["checked_at"] < cfg["OIDC_REVALIDATE_SECONDS"]
            and token["expires_at"] > now + 60
        ):
            return True
        metadata = oidc_metadata()
        token = refresh_oidc_token(token, metadata)
        if not token:
            oidc_sessions.revoke(session_id)
            return False
        profile = oidc_userinfo(token, metadata)
        groups = profile.get(cfg["OIDC_GROUP_CLAIM"]) if profile else None
        if (
            not profile
            or profile.get("sub") != record["subject"]
            or not isinstance(groups, list)
            or not all(isinstance(group, str) for group in groups)
            or cfg["OIDC_ADMIN_GROUP"] not in groups
        ):
            oidc_sessions.revoke(session_id)
            return False
        oidc_sessions.update(session_id, token, checked_at=now)
        return True

    oidc_validation_lock = threading.RLock()

    def oidc_session_is_admin(session_id):
        with oidc_validation_lock:
            return check_oidc_session(session_id)

    async def mcp_subject_is_authorized(subject):
        if not isinstance(subject, str):
            return False
        if local_auth_enabled and hmac.compare_digest(subject.encode(), cfg["ADMIN_USERNAME"].encode()):
            return True
        if oidc_auth_enabled and subject.startswith("oidc:"):
            try:
                return await asyncio.to_thread(oidc_session_is_admin, subject[5:])
            except OIDCUnavailable:
                return False
        return False

    oauth.set_subject_validator(mcp_subject_is_authorized)

    manager = LoginManager(app)
    manager.login_view = "login"

    @manager.user_loader
    def load_user(user_id):
        if not isinstance(user_id, str):
            return None
        if local_auth_enabled and hmac.compare_digest(user_id.encode(), cfg["ADMIN_USERNAME"].encode()):
            return User(user_id)
        if oidc_auth_enabled and user_id.startswith("oidc:") and oidc_session_is_admin(user_id[5:]):
            return User(user_id)
        return None

    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return session["csrf_token"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def csrf_check():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.path not in {"/login", "/logout"} and (Path(cfg["STATE_FOLDER"]) / "maintenance").exists():
            abort(503, "File management is temporarily paused; retry shortly")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not request.path.startswith("/api/v1/"):
            expected, supplied = session.get("csrf_token", ""), request.form.get("csrf_token", "")
            if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
                abort(400, "Missing or invalid CSRF token")

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        if not request.path.startswith(("/view/", "/download/", "/static/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(StorageError)
    def storage_error(exc):
        return jsonify(error={"code": exc.code, "message": str(exc)}), exc.status

    @app.errorhandler(OIDCUnavailable)
    def oidc_unavailable(_exc):
        return jsonify(error={"code": "identity_provider_unavailable", "message": "Identity authorization is temporarily unavailable"}), 503

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error={"code": exc.name.lower().replace(" ", "_"), "message": exc.description}), exc.code

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        logging.getLogger(__name__).error("Unhandled request failure: %s", type(exc).__name__)
        return jsonify(error={"code": "internal_error", "message": "The operation could not be completed"}), 500

    def require_scope(scope):
        def decorator(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                auth = request.headers.get("Authorization", "")
                supplied = auth[7:] if auth.startswith("Bearer ") else ""
                matched = None
                for token, scopes in cfg["API_TOKENS"].items():
                    if hmac.compare_digest(supplied.encode(), token.encode()):
                        matched = scopes
                if matched is None:
                    response = jsonify(error={"code": "unauthorized", "message": "API credential required"})
                    response.status_code = 401
                    response.headers["WWW-Authenticate"] = "Bearer"
                    return response
                if scope not in matched:
                    abort(403, "Credential lacks required scope")
                return fn(*args, **kwargs)
            return wrapped
        return decorator

    def public(asset):
        keys = ("id", "name", "media_type", "size", "sha256", "version", "created_at", "modified_at", "deleted_at")
        data = {k: asset[k] for k in keys}
        data["path"] = asset["canonical_path"]
        encoded = quote(data["path"], safe="/")
        data["url"] = cfg["PUBLIC_BASE_URL"] + "/view/" + encoded
        data["download_url"] = cfg["PUBLIC_BASE_URL"] + "/download/" + encoded
        return data

    def join_folder(name):
        folder = safe_path(request.form.get("current_path", ""), allow_empty=True)
        return safe_path((folder + "/" if folder else "") + safe_name(name)), folder

    @app.template_filter("timestamp_to_date")
    def timestamp_to_date(timestamp):
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    @app.get("/healthz")
    def health():
        with store.connection() as db:
            db.execute("SELECT 1").fetchone()
        return jsonify(status="ok")

    @app.get("/.well-known/oauth-protected-resource")
    def oauth_resource_metadata():
        return jsonify(
            resource=cfg["PUBLIC_BASE_URL"] + "/mcp",
            authorization_servers=[cfg["PUBLIC_BASE_URL"]],
            scopes_supported=["images:manage"],
            bearer_methods_supported=["header"],
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            if not local_auth_enabled:
                abort(404)
            # Hash peer address; ignore untrusted forwarding headers.
            key = hashlib.sha256((request.remote_addr or "unknown").encode()).hexdigest()
            store.login_attempt(key)
            username, password = request.form.get("username", ""), request.form.get("password", "")
            valid_password = check_password_hash(cfg["ADMIN_PASSWORD_HASH"], password)
            if hmac.compare_digest(username.encode(), cfg["ADMIN_USERNAME"].encode()) and valid_password:
                session.clear()
                login_user(User(cfg["ADMIN_USERNAME"]))
                session.permanent = True
                target = safe_local_redirect(request.form.get("next") or request.args.get("next"))
                return redirect(target or url_for("index"))
            store.login_attempt(key, failed=True)
            flash("Invalid username or password")
        return render_template(
            "login.html",
            show_local_login=local_auth_enabled,
            show_oidc_login=oidc_auth_enabled,
        )

    @app.get("/auth/oidc/login")
    def oidc_login():
        if not oidc_auth_enabled:
            abort(404)
        session["oidc_next"] = safe_local_redirect(request.args.get("next")) or ""
        callback_url = cfg["PUBLIC_BASE_URL"] + "/auth/oidc/callback"
        try:
            return oidc_client.authorize_redirect(callback_url)
        except Exception as exc:
            logging.getLogger(__name__).warning("OIDC authorization could not start: %s", type(exc).__name__)
            flash("Single sign-on is temporarily unavailable")
            return redirect(url_for("login")), 303

    @app.get("/auth/oidc/callback")
    def oidc_callback():
        if not oidc_auth_enabled:
            abort(404)
        try:
            token = oidc_client.authorize_access_token()
            metadata = oidc_metadata()
            profile = oidc_userinfo(token, metadata)
        except Exception as exc:
            logging.getLogger(__name__).warning("OIDC sign-in could not be completed: %s", type(exc).__name__)
            session.pop("oidc_next", None)
            flash("Single sign-on could not be completed")
            return redirect(url_for("login")), 303
        groups = profile.get(cfg["OIDC_GROUP_CLAIM"]) if profile else None
        verified_subject = (token.get("userinfo") or {}).get("sub") if isinstance(token, dict) else None
        if (
            not profile
            or not isinstance(profile.get("sub"), str)
            or profile.get("sub") != verified_subject
            or not isinstance(groups, list)
            or not all(isinstance(group, str) for group in groups)
            or cfg["OIDC_ADMIN_GROUP"] not in groups
        ):
            session.pop("oidc_next", None)
            flash("This account is not authorized to administer this service")
            return redirect(url_for("login")), 303
        try:
            oidc_session_id = oidc_sessions.create(profile["sub"], token)
        except (TypeError, ValueError):
            logging.getLogger(__name__).warning("OIDC provider returned an unusable login response")
            session.pop("oidc_next", None)
            flash("Single sign-on could not be completed")
            return redirect(url_for("login")), 303
        target = session.pop("oidc_next", "")
        session.clear()
        login_user(User("oidc:" + oidc_session_id), fresh=True)
        session.permanent = True
        return redirect(safe_local_redirect(target) or url_for("index"))

    @app.route("/oauth/consent/<pending_id>", methods=["GET", "POST"])
    @login_required
    def oauth_consent(pending_id):
        pending = oauth.pending_consent(pending_id)
        if not pending:
            abort(404, "This authorization request expired or was already handled")
        details, client = pending
        if request.method == "POST":
            decision = request.form.get("decision")
            if decision not in {"approve", "deny"}:
                abort(400, "Choose whether to approve or deny this authorization")
            callback = oauth.finish_consent(pending_id, current_user.get_id(), decision == "approve")
            if not callback:
                abort(404, "This authorization request expired or was already handled")
            return redirect(callback)
        scope_labels = {
            "images:manage": "View, upload, rename, move, delete and restore images",
            "offline_access": "Keep this connection active by refreshing its access token",
        }
        scopes = [scope_labels.get(scope, scope) for scope in details["scopes"]]
        return render_template(
            "oauth_consent.html",
            client_name=client.client_name or "MCP application",
            redirect_uri=details["redirect_uri"],
            scopes=scopes,
        )

    @app.post("/logout")
    def logout():
        principal = session.get("_user_id", "")
        if oidc_sessions and isinstance(principal, str) and principal.startswith("oidc:"):
            oidc_sessions.revoke(principal[5:])
        session.clear()
        return redirect(url_for("index"))

    @app.get("/logout")
    def logout_get():
        abort(405)

    @app.get("/")
    @app.get("/<path:folder_path>")
    def index(folder_path=""):
        safe_path(folder_path, allow_empty=True)
        if folder_path.startswith("api/"):
            abort(404)
        files, folders = store.browse(folder_path) if current_user.is_authenticated else ([], [])
        for asset in files:
            asset.update(relative_path=asset["canonical_path"], modified=asset["modified_at"], extension=PurePosixPath(asset["canonical_path"]).suffix.lstrip("."))
        return render_template(
            "index.html",
            files=files,
            folders=folders,
            current_path=folder_path,
            mcp_endpoint=cfg["PUBLIC_BASE_URL"] + "/mcp",
        )

    @app.post("/create_folder")
    @login_required
    def create_folder():
        path, folder = join_folder(request.form.get("folder_name", "").strip())
        store.create_folder(path)
        return redirect(url_for("index", folder_path=folder))

    @app.post("/upload")
    @login_required
    def upload_file():
        file = request.files.get("file")
        if not file or not file.filename:
            raise StorageError("A file is required")
        path, folder = join_folder(file.filename)
        store.upload(file.stream, file.filename, path, strict_image=False, choose_suffix=True)
        flash("File uploaded")
        return redirect(url_for("index", folder_path=folder))

    def serve(path, download=False):
        asset = store.lookup(path)
        response = send_file(store.file_path(asset), mimetype=asset["media_type"], as_attachment=download or not asset["is_image"], download_name=asset["name"], etag=asset["sha256"], conditional=True, max_age=0)
        response.headers["Cache-Control"] = "no-cache"
        if not asset["is_image"]:
            response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; frame-ancestors 'none'"
        return response

    @app.get("/download/<path:file_path>")
    def download_file(file_path):
        return serve(file_path, True)

    @app.get("/view/<path:file_path>")
    def view_file(file_path):
        return serve(file_path)

    @app.post("/delete/<path:file_path>")
    @login_required
    def delete_file(file_path):
        safe_path(file_path)
        folder = safe_path(request.form.get("current_path", ""), allow_empty=True)
        try:
            asset = store.lookup(file_path)
        except StorageError as exc:
            if exc.status != 404:
                raise
            store.delete_folder(file_path)
        else:
            version = request.form.get("version", type=int)
            store.mutate(asset["id"], "delete", version)
        return redirect(url_for("index", folder_path=folder))

    @app.post("/rename/<path:item_path>")
    @login_required
    def rename_item(item_path):
        safe_path(item_path)
        name = safe_name(request.form.get("new_name", "").strip())
        folder = safe_path(request.form.get("current_path", ""), allow_empty=True)
        try:
            asset = store.lookup(item_path)
        except StorageError as exc:
            if exc.status != 404:
                raise
            store.rename_folder(item_path, name)
        else:
            # Browser rename preserves its familiar filename/URL behavior, retaining the old alias.
            store.mutate(asset["id"], "browser_rename", request.form.get("version", type=int), name)
        return redirect(url_for("index", folder_path=folder))

    @app.get("/api/files")
    @login_required
    def api_files():
        files, _ = store.browse("")
        return jsonify([{"name": a["name"], "size": a["size"], "modified": a["modified_at"], "extension": PurePosixPath(a["canonical_path"]).suffix.lstrip("."), "is_image": bool(a["is_image"])} for a in files])

    @app.get("/api/v1/images")
    @require_scope("read")
    def list_images():
        try:
            limit, offset = int(request.args.get("limit", 50)), int(request.args.get("offset", 0))
            older_than = float(request.args["older_than"]) if "older_than" in request.args else None
        except ValueError:
            raise StorageError("Invalid pagination or timestamp") from None
        if older_than is not None and not math.isfinite(older_than):
            raise StorageError("Invalid timestamp")
        result = store.list(limit, offset, request.args.get("folder"), request.args.get("deleted") == "true", older_than=older_than)
        result["items"] = [public(a) for a in result["items"]]
        return jsonify(result)

    @app.post("/api/v1/images")
    @require_scope("write")
    def upload_image():
        file = request.files.get("file")
        if not file or not file.filename:
            raise StorageError("A raster image file is required")
        asset, created = store.upload(file.stream, request.form.get("name") or file.filename, request.form.get("path"), request.headers.get("Idempotency-Key"))
        return jsonify(public(asset)), 201 if created else 200

    @app.get("/api/v1/images/<asset_id>")
    @require_scope("read")
    def get_image(asset_id):
        return jsonify(public(store.get(asset_id)))

    @app.post("/api/v1/images/<asset_id>/<operation>")
    @require_scope("write")
    def change_image(asset_id, operation):
        if operation not in {"rename", "path", "restore"}:
            abort(404)
        body = request.get_json()
        if not isinstance(body, dict):
            raise StorageError("JSON object required")
        keep = body.get("keep_old_alias", True)
        if type(keep) is not bool:
            raise StorageError("keep_old_alias must be a boolean")
        value = body.get("name") if operation == "rename" else body.get("path")
        asset = store.mutate(asset_id, operation, body.get("expected_version"), value, keep)
        data = public(asset)
        if operation == "path":
            data["keep_old_alias"] = keep
        return jsonify(data)

    @app.delete("/api/v1/images/<asset_id>")
    @require_scope("delete")
    def delete_image(asset_id):
        body = request.get_json()
        if not isinstance(body, dict):
            raise StorageError("JSON object required")
        return jsonify(public(store.mutate(asset_id, "delete", body.get("expected_version"))))

    return app


if __name__ == "__main__":
    from waitress import serve
    serve(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8080")), threads=8, max_request_body_size=int(os.getenv("MAX_REQUEST_BYTES", str(101 * 1024**2))))
