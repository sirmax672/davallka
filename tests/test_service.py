import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import re
import sys
import threading
from pathlib import Path
from wsgiref.simple_server import make_server, WSGIRequestHandler

import httpx
from mcp import Client, StdioServerParameters
from PIL import Image
import pytest

from app import create_app
from mcp_server import create_mcp
from storage import StorageError, Store, safe_path

TOKEN = "a" * 48
READ_TOKEN = "r" * 48


def png():
    f = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(f, "PNG")
    return f.getvalue()


@pytest.fixture
def app(tmp_path):
    legacy = tmp_path / "uploads"
    legacy.mkdir()
    (legacy / "nested").mkdir()
    (legacy / "nested" / "old.png").write_bytes(png())
    (legacy / "note.html").write_text("<script>alert('fixture')</script>")
    return create_app({"TESTING": True, "SECRET_KEY": "s" * 48, "ADMIN_USERNAME": "existing-admin", "ADMIN_PASSWORD": "original-password", "UPLOAD_FOLDER": str(legacy), "STATE_FOLDER": str(tmp_path / "state"), "PUBLIC_BASE_URL": "https://files.example.test", "API_TOKENS": {TOKEN: ["read", "write", "delete"], READ_TOKEN: ["read"]}, "SESSION_COOKIE_SECURE": False})


@pytest.fixture
def client(app):
    return app.test_client()


def auth(token=TOKEN):
    return {"Authorization": "Bearer " + token}


def upload(client, path="new.png", key="test-key", content=None):
    return client.post("/api/v1/images", headers={**auth(), "Idempotency-Key": key}, data={"file": (io.BytesIO(content if content is not None else png()), "new.png"), "path": path}, content_type="multipart/form-data")


def csrf_login(client):
    html = client.get("/login").text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', html)[1]
    assert client.post("/login", data={"csrf_token": csrf, "username": "existing-admin", "password": "original-password"}).status_code == 302
    with client.session_transaction() as s:
        # The successful login rotates the session and its CSRF token.
        assert "csrf_token" not in s
    html = client.get("/").text
    return re.search(r'name="csrf_token" value="([^"]+)"', html)[1]


def test_fail_closed_configuration(app):
    config = dict(app.config)
    config["SECRET_KEY"] = "your-secret-key-change-this"
    with pytest.raises(RuntimeError):
        create_app(config)
    config["SECRET_KEY"] = "s" * 48
    config["ADMIN_PASSWORD_HASH"] = None
    config["ADMIN_PASSWORD"] = "password123"
    with pytest.raises(RuntimeError):
        create_app(config)


def test_legacy_is_byte_preserving_and_import_is_idempotent(app, client):
    store = app.extensions["store"]
    before = {p.relative_to(store.legacy).as_posix(): p.read_bytes() for p in store.legacy.rglob("*") if p.is_file()}
    assert store.import_legacy() == 0
    response = client.get("/view/nested/old.png")
    assert response.status_code == 200 and response.data == png()
    assert response.mimetype == "image/png"
    response.close()
    assert client.get("/view/missing.png").status_code == 404
    assert client.get("/view/note.html").headers["Content-Disposition"].startswith("attachment")
    for asset in store.list(images_only=False)["items"]:
        store.mutate(asset["id"], "delete", asset["version"])
    after = {p.relative_to(store.legacy).as_posix(): p.read_bytes() for p in store.legacy.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("path", ["../app.py", "/etc/passwd", "a/../../b", "a\\b", "C:/file", "//host/share", "a//b", ".env", "a/./b", "a/../b", "NUL.txt", "a/CON", "a/", "a\x00b"])
def test_invalid_paths(path):
    with pytest.raises(StorageError):
        safe_path(path)


def test_traversal_symlink_and_untrusted_host(app, client, tmp_path):
    (tmp_path / "outside.txt").write_text("CANARY")
    for route in ["/view/%2e%2e/outside.txt", "/download/%2e%2e/outside.txt"]:
        assert client.get(route).status_code == 400
    assert upload(client, "../escape.png").status_code == 400
    store = app.extensions["store"]
    try:
        (store.legacy / "link").symlink_to(tmp_path / "outside.txt")
    except OSError:
        pass  # Windows may not grant symlink privilege; Linux container suite covers it.
    else:
        from storage import contained
        with pytest.raises(StorageError):
            contained(store.legacy, "link")
    result = upload(client).json
    assert result["url"].startswith("https://files.example.test/")
    assert client.get("/api/v1/images/" + result["id"], headers={**auth(), "Host": "evil.example"}).json["url"] == result["url"]


def test_authentication_csrf_and_existing_credentials(app, client):
    assert client.get("/api/v1/images").status_code == 401
    assert client.get("/api/files").status_code == 302
    assert client.post("/login", data={"username": "existing-admin", "password": "original-password"}).status_code == 400
    assert client.get("/logout").status_code == 405
    with client.session_transaction() as s:
        s["_user_id"] = "arbitrary-user"
        s["csrf_token"] = "fixture"
    assert client.post("/create_folder", data={"csrf_token": "fixture", "folder_name": "forbidden"}).status_code == 302
    csrf = csrf_login(client)
    assert client.post("/create_folder", data={"folder_name": "no-csrf"}).status_code == 400
    assert client.post("/create_folder", data={"csrf_token": csrf, "folder_name": "ok"}).status_code == 302
    assert client.post("/create_folder", data={"csrf_token": csrf, "folder_name": "escape", "current_path": ".."}).status_code == 400
    assert client.post("/logout", data={"csrf_token": csrf}).status_code == 302


def test_login_rate_limit(client):
    html = client.get("/login").text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', html)[1]
    for _ in range(10):
        assert client.post("/login", data={"csrf_token": csrf, "username": "wrong", "password": "wrong"}).status_code == 200
    assert client.post("/login", data={"csrf_token": csrf, "username": "wrong", "password": "wrong"}).status_code == 429


def test_image_api_lifecycle_and_aliases(client):
    response = upload(client)
    assert response.status_code == 201
    original = response.json
    assert upload(client).status_code == 200
    assert upload(client).json["id"] == original["id"]
    assert upload(client, path="different.png").status_code == 409
    assert upload(client, key="different-key").status_code == 409
    assert upload(client, "bad.png", "invalid", b"not an image").status_code == 400
    aid = original["id"]
    url = "/api/v1/images/" + aid
    assert client.post(url + "/rename", headers=auth(READ_TOKEN), json={"name": "renamed.png", "expected_version": 1}).status_code == 403
    renamed = client.post(url + "/rename", headers=auth(), json={"name": "renamed.png", "expected_version": 1}).json
    assert renamed["url"] == original["url"] and renamed["name"] == "renamed.png"
    assert client.post(url + "/path", headers=auth(), json={"path": "moved.png", "expected_version": 1}).status_code == 409
    moved = client.post(url + "/path", headers=auth(), json={"path": "folder/moved.png", "expected_version": 2, "keep_old_alias": True}).json
    assert client.get("/view/new.png").status_code == 200
    assert client.get("/view/folder/moved.png").data == png()
    moved = client.post(url + "/path", headers=auth(), json={"path": "final.png", "expected_version": moved["version"], "keep_old_alias": False}).json
    assert client.get("/view/folder/moved.png").status_code == 410
    deleted = client.delete(url, headers=auth(), json={"expected_version": moved["version"]}).json
    assert client.get("/view/new.png").status_code == 410
    assert client.get("/view/final.png").status_code == 410
    assert client.get("/api/v1/images?deleted=true", headers=auth()).json["total"] == 1
    restored = client.post(url + "/restore", headers=auth(), json={"expected_version": deleted["version"]}).json
    assert restored["deleted_at"] is None
    assert client.get("/view/new.png").status_code == 200
    assert client.get("/view/folder/moved.png").status_code == 410
    assert client.get("/api/v1/images?folder=", headers=auth()).json["total"] == 1


def test_concurrent_uploads_never_overwrite(app):
    store = app.extensions["store"]
    def attempt(_):
        try:
            return store.upload(io.BytesIO(png()), "race.png", idempotency_key="concurrent")[0]["id"]
        except StorageError as e:
            return e.code
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(attempt, range(4)))
    assert len(set(ids)) == 1
    assert store.list(folder="")["total"] == 1


def test_browser_rename_keeps_links_and_soft_delete_preserves_bytes(app, client):
    csrf = csrf_login(client)
    old = app.extensions["store"].lookup("nested/old.png")
    assert client.post("/rename/nested/old.png", data={"csrf_token": csrf, "new_name": "new.png", "current_path": "nested", "version": old["version"]}).status_code == 302
    assert client.get("/view/nested/old.png").data == png()
    updated = app.extensions["store"].get(old["id"])
    assert client.post("/delete/nested/new.png", data={"csrf_token": csrf, "current_path": "nested", "version": updated["version"]}).status_code == 302
    assert (app.extensions["store"].legacy / "nested/old.png").read_bytes() == png()
    assert client.get("/view/nested/old.png").status_code == 410


def test_quota_and_request_limits(app, client):
    app.extensions["store"].max_file_bytes = 1
    assert upload(client).status_code == 413
    app.extensions["store"].max_file_bytes = 10000
    app.extensions["store"].max_storage_bytes = 1
    assert upload(client).status_code == 507


def test_write_freeze_preserves_reads_and_login(app, client):
    marker = Path(app.config["STATE_FOLDER"]) / "maintenance"
    marker.touch()
    assert upload(client).status_code == 503
    assert client.get("/view/nested/old.png").data == png()
    assert client.get("/login").status_code == 200
    marker.unlink()
    assert upload(client).status_code == 201


def test_mcp_tools_end_to_end(app):
    async def scenario():
        server = create_mcp("https://files.example.test", TOKEN, httpx.WSGITransport(app=app))
        async with Client(server) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools.tools}
            assert names == {"upload_image", "get_image", "list_images", "rename_image", "set_image_path", "delete_image", "restore_image", "list_cleanup_candidates"}
            async def call(name, args):
                result = await c.call_tool(name, args)
                assert not result.is_error, result
                return result.structured_content
            asset = await call("upload_image", {"image_base64": base64.b64encode(png()).decode(), "name": "mcp.png", "idempotency_key": "mcp-test"})
            aid = asset["id"]
            asset = await call("rename_image", {"image_id": aid, "new_name": "friendly.png", "expected_version": asset["version"]})
            asset = await call("set_image_path", {"image_id": aid, "new_path": "mcp/moved.png", "expected_version": asset["version"]})
            assert (await call("get_image", {"image_id": aid}))["path"] == "mcp/moved.png"
            assert (await call("list_images", {}))["total"] >= 2
            await call("list_cleanup_candidates", {"older_than_days": 30})
            asset = await call("delete_image", {"image_id": aid, "expected_version": asset["version"]})
            assert asset["deleted_at"]
            assert (await call("restore_image", {"image_id": aid, "expected_version": asset["version"]}))["deleted_at"] is None
            assert (await c.call_tool("upload_image", {"image_base64": "invalid", "name": "bad.png", "idempotency_key": "invalid"})).is_error
    asyncio.run(scenario())


def test_real_stdio_transport(app):
    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass
    http = make_server("127.0.0.1", 0, app, handler_class=QuietHandler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve().parents[1] / "mcp_server.py")], env={"DAVALLKA_API_URL": f"http://127.0.0.1:{http.server_port}", "DAVALLKA_MCP_API_TOKEN": TOKEN})
        async with Client(params) as c:
            assert len((await c.list_tools()).tools) == 8
            result = await c.call_tool("list_images", {})
            assert not result.is_error and result.structured_content["total"] == 1
    try:
        asyncio.run(scenario())
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)
