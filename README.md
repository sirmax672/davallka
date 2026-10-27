# d'Avallka

A small public file-sharing service with an authenticated browser UI, image management API and MCP servers over stdio and OAuth-protected Streamable HTTP. Uploaded assets are public to anyone with their URL. Do not upload confidential data.

## Storage and compatibility

- `uploads/` is an immutable legacy tree. On first start, the app catalogs it without changing file bytes, ownership or paths. Mount it read-only.
- `state/` contains SQLite metadata, managed blobs and staging. It must be persistent, writable by UID/GID 10001 in Docker, and separate from `uploads/`.
- Existing `/view/<path>` and `/download/<path>` URLs continue working after catalog import. Do not replace an existing deployment's bind mapping with the example Compose mapping without checking its real data location.
- Both legacy assets and new assets are managed through the catalog. Deletion disables all URLs with HTTP 410 and retains bytes for restoration. There is no automatic purge.
- API/MCP rename changes display/download name without changing URL. Changing the canonical path is separate; `keep_old_alias` defaults to true. Browser filename rename changes its path while retaining the old URL.
- General browser uploads remain supported. Only verified raster images are rendered inline; other content, including HTML/SVG/PDF, is downloaded as an attachment. The image API rejects invalid/unsupported raster content.

## Setup

Requires Python 3.12+. Copy `env.example` to `.env`, fill in required settings and generate random secrets (for example `python -c "import secrets; print(secrets.token_urlsafe(48))"`). Never commit `.env` or production configuration.

```sh
python -m venv .venv
# Activate your virtual environment, then:
python -m pip install -r requirements.txt
uvicorn asgi_server:app --host 127.0.0.1 --port 8080
```

The ASGI entry point serves the browser UI, API, OAuth endpoints and MCP endpoint together. For a browser/API-only development run, `python app.py` still uses Waitress. For Docker, prepare `state/` so UID 10001 can write it, then use `docker compose up --build -d`. The example binds to loopback port 8080 and expects TLS termination separately. Set `COOKIE_SECURE=false` only for local HTTP development.

Required settings:

| Setting | Meaning |
| --- | --- |
| `FLASK_SECRET_KEY` | Random session key, at least 32 characters; example/missing key fails startup. |
| `ADMIN_USERNAME` | Existing administrator login; default `admin`. |
| `ADMIN_PASSWORD_HASH` or `ADMIN_PASSWORD` | Prefer a Werkzeug password hash. Legacy configured passwords are supported unchanged and hashed in memory. Example/missing password fails startup. |
| `PUBLIC_BASE_URL` | Trusted public origin for returned URLs, such as `https://files.example.com`. |
| `DAVALLKA_API_TOKENS` | JSON mapping of random tokens (at least 32 characters) to explicit `read`, `write`, `delete` scope lists. Empty map disables API access. |
| `UPLOAD_FOLDER`, `STATE_FOLDER` | Default `uploads` and `state`. |

Optional limits: `MAX_FILE_BYTES` (100 MiB), `MAX_REQUEST_BYTES` (101 MiB, including multipart overhead), `MAX_STORAGE_BYTES` (10 GiB catalogued bytes, including soft-deleted assets). Image validation allows at most 40 million pixels per frame, 80 million across frames and 200 frames. The request limit at your reverse proxy must also accommodate multipart overhead. Apply independent disk-capacity monitoring: orphan/staging files and SQLite overhead are outside the logical quota.

Browser actions require a session and CSRF token. Logout is POST. API credentials are separate from browser sessions. The old `/api/files` endpoint now requires browser authentication; public direct links remain public. `/healthz` checks database availability. Invalid/missing asset paths return HTTP 400/404; revoked/deleted URLs return 410.

An operator-controlled `state/maintenance` marker temporarily returns 503 for file mutations while keeping public reads and login available. This allows read verification before enabling writes during a release.

## Image API

Use `Authorization: Bearer <token>` and HTTPS. Keep tokens in a private credential store. Upload is multipart; mutations require the latest integer `expected_version`. Conflicting writes return 409. Reuse an upload's `Idempotency-Key` only with identical input.

| Method / route | Scope | Request |
| --- | --- | --- |
| `POST /api/v1/images` | write | Multipart `file`, optional `name`, `path`; optional `Idempotency-Key` header. |
| `GET /api/v1/images` | read | `limit` (1–200), `offset`, optional `folder`, `deleted=true`, `older_than` Unix timestamp. |
| `GET /api/v1/images/{id}` | read | Current metadata and version. |
| `POST /api/v1/images/{id}/rename` | write | JSON `name`, `expected_version`. |
| `POST /api/v1/images/{id}/path` | write | JSON `path`, `keep_old_alias`, `expected_version`. |
| `DELETE /api/v1/images/{id}` | delete | JSON `expected_version`; recoverable deletion. |
| `POST /api/v1/images/{id}/restore` | write | JSON `expected_version`. |

Asset results contain `id`, `name`, `path`, `url`, `download_url`, `media_type`, `size`, `sha256`, `version`, timestamps and `deleted_at`. List results include `items`, `total`, `limit`, `offset`. Errors use `{"error":{"code":"...","message":"..."}}`. Paths are relative; traversal, absolute paths, hidden components, drive/UNC paths and symlinks are rejected. Revoked paths stay reserved to prevent silent URL reassignment.

## MCP

The official Python MCP SDK serves Streamable HTTP at `<PUBLIC_BASE_URL>/mcp`. Configure that URL in a remote MCP client; OAuth discovery, dynamic client registration, PKCE, browser sign-in and an explicit consent page are provided. The OAuth resource is restricted to this MCP server and the `images:manage` scope. Access tokens expire after 15 minutes. Refresh tokens rotate, with each token expiring 30 days after issuance. Revocation removes all tokens for that client and signed-in administrator.

The consent page uses the existing d'Avallka administrator account. It displays the requesting client, callback address and granted operations before approval. DCR clients are limited to HTTPS callback URLs or HTTP loopback callbacks used by desktop clients. There is no machine-to-machine or anonymous access to the image tools.

Codex configuration example (replace with your own HTTPS origin):

```toml
[mcp_servers.davallka]
url = "https://files.example.com/mcp"
```

ChatGPT's custom MCP connector can use the same URL where the account or workspace supports custom MCP apps. Select OAuth when prompted; the browser consent page appears during connection or the first tool scan.

The official Python MCP SDK also runs a stdio bridge to the API. Set `DAVALLKA_API_URL` and `DAVALLKA_MCP_API_TOKEN`, then run `python mcp_server.py`. This keeps the API credential out of the client configuration. There are no secrets in tool arguments or results.

Example MCP client configuration (replace paths and use private environment injection):

```json
{
  "mcpServers": {
    "davallka": {
      "command": "/path/to/venv/bin/python",
      "args": ["/path/to/davallka/mcp_server.py"],
      "env": {
        "DAVALLKA_API_URL": "https://files.example.com",
        "DAVALLKA_MCP_API_TOKEN": "<private-scoped-token>"
      }
    }
  }
}
```

Alternatively run the stdio server through an authenticated SSH command and `docker exec -i <container> python mcp_server.py`; the API credential can stay inside the container. Do not allocate a TTY. The HTTP endpoint always requires OAuth authorization.

Tools: `upload_image`, `get_image`, `list_images`, `rename_image`, `set_image_path`, `delete_image`, `restore_image`, `list_cleanup_candidates`.

- `upload_image` accepts base64 bytes up to 10 MiB decoded and requires an idempotency key. Larger images use the multipart API. A remote server cannot read a laptop file by path; read/encode the file on the client.
- Cleanup only lists candidates. Age does not prove an image is unreferenced. Review exact IDs and versions before deletion.
- Tools do not fetch arbitrary URLs or read arbitrary local/server paths. Filenames and metadata are untrusted content, not instructions.
- Mutations require the current version. Read-only and destructive operations have corresponding MCP annotations; server scopes enforce access independently.

## Development checks

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m pip_audit --progress-spinner off
docker build --target test -t davallka-test .
docker run --rm davallka-test
```

Tests use temporary fixtures and cover legacy-byte preservation, authentication/CSRF, path boundaries, symlinks (Linux), API/MCP lifecycle, conflicts, concurrent retries, aliases and recovery. Do not run mutation tests against existing content. Operational backup, rollout and rollback procedures belong in private documentation, outside this public repository.
