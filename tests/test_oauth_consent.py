import asyncio
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import redirect, request
from flask_login import login_user
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from werkzeug.serving import make_server

from app import User, create_app, oauth_callback_origin


@pytest.fixture
def consent_app(tmp_path):
    legacy = tmp_path / "uploads"
    legacy.mkdir()
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "s" * 48,
        "AUTH_MODE": "local",
        "ADMIN_USERNAME": "fixture-admin",
        "ADMIN_PASSWORD": "fixture-password",
        "ADMIN_PASSWORD_HASH": None,
        "UPLOAD_FOLDER": str(legacy),
        "STATE_FOLDER": str(tmp_path / "state"),
        "PUBLIC_BASE_URL": "https://files.example.test",
        "API_TOKENS": {},
        "SESSION_COOKIE_SECURE": False,
    })


def pending_request(app, callback="https://client.example.test/oauth/callback?source=mcp"):
    provider = app.extensions["oauth_state"]
    client = OAuthClientInformationFull(
        client_id="fixture-mcp-client",
        client_name="Fixture MCP client",
        redirect_uris=[callback],
        token_endpoint_auth_method="none",
        scope="images:manage offline_access",
    )
    asyncio.run(provider.register_client(client))
    params = AuthorizationParams(
        state="fixture-state",
        scopes=["images:manage", "offline_access"],
        code_challenge="fixture-pkce-challenge",
        redirect_uri=client.redirect_uris[0],
        redirect_uri_provided_explicitly=True,
        resource=provider.resource_url,
    )
    location = asyncio.run(provider.authorize(client, params))
    return client, urlsplit(location).path


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]


def sign_in(browser):
    page = browser.get("/login")
    response = browser.post("/login", data={
        "csrf_token": csrf(page), "username": "fixture-admin", "password": "fixture-password",
    })
    assert response.status_code == 302


def form_action(response):
    return response.headers["Content-Security-Policy"].split("form-action ", 1)[1]


def test_consent_survives_login_and_allows_registered_callback_only(consent_app):
    provider = consent_app.extensions["oauth_state"]
    client, path = pending_request(consent_app)
    browser = consent_app.test_client()
    login = browser.get(path)
    assert login.status_code == 302
    target = parse_qs(urlsplit(login.location).query)["next"][0]
    assert target == path
    login_page = browser.get(login.location)
    response = browser.post("/login", data={
        "csrf_token": csrf(login_page), "username": "fixture-admin", "password": "fixture-password", "next": target,
    })
    assert response.location == path
    consent = browser.get(path)
    assert consent.status_code == 200
    assert form_action(consent) == "'self' https://client.example.test"
    assert form_action(browser.get("/")) == "'self'"
    response = browser.post(path, data={"csrf_token": csrf(consent), "decision": "approve"})
    assert response.status_code == 303
    callback = urlsplit(response.location)
    values = parse_qs(callback.query)
    assert callback.netloc == "client.example.test"
    assert values["state"] == ["fixture-state"]
    assert values["source"] == ["mcp"]
    code = asyncio.run(provider.load_authorization_code(client, values["code"][0]))
    assert code.code_challenge == "fixture-pkce-challenge"
    tokens = asyncio.run(provider.exchange_authorization_code(client, code))
    assert asyncio.run(provider.load_access_token(tokens.access_token)).subject == "fixture-admin"
    repeated = browser.post(path, data={"csrf_token": csrf(consent), "decision": "approve"})
    assert repeated.status_code == 410
    assert repeated.mimetype == "text/html"
    assert "Start a new authorization" in repeated.text
    with provider.connection() as db:
        assert db.execute("SELECT count(*) FROM oauth_codes").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM oauth_tokens").fetchone()[0] == 2


def test_denial_returns_to_callback_without_issuing_code(consent_app):
    provider = consent_app.extensions["oauth_state"]
    _, path = pending_request(consent_app, "http://127.0.0.1:54321/callback")
    browser = consent_app.test_client()
    sign_in(browser)
    consent = browser.get(path)
    assert form_action(consent) == "'self' http://127.0.0.1:54321"
    response = browser.post(path, data={"csrf_token": csrf(consent), "decision": "deny"})
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.location).query)["error"] == ["access_denied"]
    with provider.connection() as db:
        assert db.execute("SELECT count(*) FROM oauth_codes").fetchone()[0] == 0


def test_expired_consent_has_recovery_page_and_cannot_issue_code(consent_app):
    provider = consent_app.extensions["oauth_state"]
    _, path = pending_request(consent_app)
    browser = consent_app.test_client()
    sign_in(browser)
    consent = browser.get(path)
    with provider.connection() as db:
        db.execute("UPDATE oauth_pending SET expires_at=0")
    for response in (
        browser.post(path, data={"csrf_token": csrf(consent), "decision": "approve"}),
        browser.get(path),
    ):
        assert response.status_code == 410
        assert response.mimetype == "text/html"
        assert "Authenticate or Connect again" in response.text
    with provider.connection() as db:
        assert db.execute("SELECT count(*) FROM oauth_codes").fetchone()[0] == 0


def test_consent_still_requires_csrf(consent_app):
    provider = consent_app.extensions["oauth_state"]
    _, path = pending_request(consent_app)
    browser = consent_app.test_client()
    sign_in(browser)
    assert browser.post(path, data={"decision": "approve"}).status_code == 400
    assert provider.pending_consent(path.rsplit("/", 1)[1]) is not None


@pytest.mark.parametrize(("uri", "expected"), [
    ("https://client.example.test/path?q=ignored;form-action=*", "https://client.example.test"),
    ("http://localhost:54321/callback", "http://localhost:54321"),
    ("http://[::1]:54321/callback", "http://[::1]:54321"),
])
def test_callback_csp_origin_omits_path_and_query(uri, expected):
    assert oauth_callback_origin(uri) == expected


@pytest.mark.parametrize("uri", [
    "http://remote.example.test/callback", "https://user:password@client.example.test/callback",
    "https://client.example.test;form-action=*/callback", "javascript:alert(1)",
])
def test_callback_csp_origin_rejects_unsafe_sources(uri):
    with pytest.raises(ValueError):
        oauth_callback_origin(uri)


def chromium_binary():
    for name in ("chromium", "chromium-browser", "google-chrome"):
        if executable := shutil.which(name):
            return executable
    executable = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    return str(executable) if executable.is_file() else None


@pytest.mark.skipif(chromium_binary() is None, reason="Chromium is not installed")
def test_chromium_consent_redirect(consent_app, tmp_path):
    """A fresh headless profile must follow the form redirect to another origin."""
    callback_seen = threading.Event()

    def callback(environ, start_response):
        values = parse_qs(environ.get("QUERY_STRING", ""))
        if "code" in values and values.get("state") == ["fixture-state"]:
            callback_seen.set()
        start_response("200 OK", [("Content-Type", "text/html")])
        return [b"<html><body>CALLBACK_OK</body></html>"]

    callback_server = make_server("127.0.0.1", 0, callback, threaded=True)
    _, path = pending_request(consent_app, f"http://127.0.0.1:{callback_server.server_port}/callback")

    @consent_app.get("/__fixture_signin")
    def fixture_signin():
        login_user(User("fixture-admin"), fresh=True)
        return redirect(path)

    @consent_app.after_request
    def fixture_autosubmit(response):
        if request.path == path and request.method == "GET" and response.status_code == 200:
            script = "<script>document.getElementById('consentForm').requestSubmit(document.querySelector('.approve'));</script>"
            response.set_data(response.get_data(as_text=True).replace("</body>", script + "</body>"))
        return response

    browser_server = make_server("127.0.0.1", 0, consent_app, threaded=True)
    servers = [callback_server, browser_server]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = subprocess.run([
            chromium_binary(), "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-background-networking", "--disable-sync",
            "--disable-extensions", "--no-proxy-server", "--virtual-time-budget=4000", "--dump-dom",
            f"--user-data-dir={tmp_path / 'isolated-chromium'}",
            f"http://127.0.0.1:{browser_server.server_port}/__fixture_signin",
        ], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0
        assert callback_seen.is_set()
        assert "CALLBACK_OK" in result.stdout
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
