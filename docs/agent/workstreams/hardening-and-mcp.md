# Hardening and image MCP

## Scope and resume state

- Reviewed application baseline: `05871c7daa5ea65f336e2a489f2fc00d92802b63` (`master`).
- Implemented: storage boundary, fail-closed sessions, identity validation, CSRF, attachment policy, rate-limited login, scoped API, transactional catalog and official-SDK stdio MCP.
- Implementation commit: `9a45dcf9b4c1e9fb85be3a2f677c5f6ffd7d48b1`, merged to `master`; primary checkout; no PR.
- Exact implementation resume commit: `9a45dcf9b4c1e9fb85be3a2f677c5f6ffd7d48b1`. Later handover-only commits do not change runtime code.
- Deployment observations and runbooks are deliberately excluded from this public card.

## Assessment findings at the reviewed baseline

| Priority | Finding | Direction |
| --- | --- | --- |
| P0 | File read and mutation routes join user-controlled paths without enforcing storage-root containment. `secure_filename` only sanitizes the basename. | One shared resolver for every route, reject absolute/parent/drive/UNC paths, enforce resolved containment and a symlink policy. Use safe file-serving primitives. |
| P0 | Session secret and administrator password have usable public defaults; `load_user` accepts every supplied user ID. | Refuse unsafe startup configuration; use a random required secret, verify actual identities, hash passwords and rate-limit login. |
| P1 | State-changing forms have no CSRF protection; logout is a GET. | Add CSRF tokens to browser mutations and POST logout; API credentials use a separate authentication path. |
| P1 | All uploaded types can be served inline on the application origin; `allowed_file()` is never called. | Validate decoded raster image content and dimensions; force attachment or isolate/sandbox active content; set `nosniff` and deliberate security headers. |
| P1 | Root inventory is returned anonymously by `/api/files`, despite a browser UI that hides the file listing. | Protect management inventory; preserve intentionally public direct links. |
| P1 | Dependencies are old, including Werkzeug 2.3.7 with published multipart parsing advisories. | Upgrade to maintained compatible releases; audit the complete resolved dependency set and regression-test parsing limits. |
| P1 | Docker defaults run the Flask development server as root; `.dockerignore` does not exclude `.env` or `.venv`. | Use a production WSGI server and non-root user; exclude secrets, virtualenvs and private artifacts from build context; add a health endpoint and resource limits. |
| P2 | Existence checks followed by saves/renames race; deletion is immediate; listing scans and sorts a whole directory. | Atomic temporary upload + commit, no-clobber writes, versions/locking, recoverable delete and paginated catalog. |
| P2 | Anonymous pages execute script that immediately dereferences the absent `fileInput`. | Guard or conditionally include authenticated upload handlers; verify login and anonymous pages. |
| P2 | Missing file URLs redirect to HTML; raw exception strings are flashed; README has a stale service name and implies the unused extension filter works. | Return correct 404/409/413 responses, log sanitized diagnostics and update documentation with implemented behavior. |

## Verified development evidence

An isolated test-client assessment with the pinned dependencies confirmed reads and mutations outside the temporary upload root, acceptance of a session signed with the example key, absent CSRF enforcement, anonymous inventory and inline HTML/SVG responses. Fixtures contained no real data. No destructive or load testing against a deployment was performed.

## Implementation sequence and gates

### 1. Security boundary

- Centralize storage containment and identity checks; fail startup on missing/example credentials.
- Add CSRF and explicit cookie security; protect catalog access and rate-limit authentication.
- Define raster allowlist, decode/size limits and active-content policy. If retaining general file sharing, keep unsafe types as attachments on an isolated content origin.
- Update dependencies and Docker build exclusions.
- Gate: traversal tests cover reads and every mutation, encoded separators, absolute paths, symlinks and platform-specific paths; unsigned/invalid identities and CSRF-less browser mutations fail; unsafe content never executes on the admin origin.

### 2. Reliable storage and API

- Extract storage operations from Flask route handlers; the browser and API share the same authorization and mutation rules.
- Add a small transactional metadata store (SQLite is a starting option for a single instance), with immutable asset ID, display/download name, canonical path, alias history, media type, size, digest, version, timestamps and deleted state.
- Separate metadata rename from canonical URL changes. Validate relative paths, reserve route namespaces and reject conflicts without overwriting.
- Add atomic upload finalization, idempotency keys for create operations and expected-version checks for mutations. Plan crash recovery across database and blob writes.
- Introduce authenticated `/api/v1/images` create/list/get/rename/path/delete/restore endpoints with structured results and errors; list with pagination and folder filters.
- Return `id`, `name`, `path`, `url`, `download_url`, `media_type`, `size`, `sha256` and `version`. Build URLs from trusted configuration, not an unchecked Host header.
- Catalog existing files and preserve valid `/view/...` and `/download/...` links before migration. A path change can retain a same-service alias; revocation must also remove aliases. Define caching and 404/410 behavior.
- Gate: existing links survive migration, conflicts never overwrite, retries never duplicate an asset, concurrent stale updates fail, and deleted assets/aliases stop resolving according to policy.

### 3. MCP adapter

Use an official supported SDK and call the management API; do not automate the login form or grant the adapter arbitrary server filesystem access.

| Tool | Intended behavior |
| --- | --- |
| `upload_image` | Accept bounded image bytes or a client-side file upload; validate and return asset ID plus public URLs. Use an idempotency key. |
| `get_image` / `list_images` | Retrieve current URLs and metadata; search/filter and paginate. |
| `rename_image` | Change display/download name by ID with expected version; leave URL unchanged. |
| `set_image_path` | Change canonical relative path and return new URL plus old-alias disposition. Expose `keep_old_alias` explicitly. |
| `delete_image` / `restore_image` | Delete or restore by stable ID with expected version. Show exact targets in previews for bulk cleanup. |
| `list_cleanup_candidates` | List old/unused candidates without deleting; metadata age does not prove a file is unreferenced externally. |

- Start with stdio on the agent machine and a revocable, scoped service credential for the API. Never put credentials in prompts, tool results or logs.
- A remote MCP server cannot read files on a caller's laptop by path. Define bounded base64 for small inputs or a separate authenticated multipart/upload-session flow for larger images. Local-file helpers must enforce allowed client roots and symlink policy.
- Do not enable arbitrary URL fetches by default. If added later, enforce SSRF controls across DNS resolution and redirects, reject private/link-local/metadata endpoints and bound time/size.
- Remote HTTP transport needs HTTPS and protocol-compatible authorization with per-client scopes and audience validation. Do not pass MCP tokens through as API tokens.
- Mark read and destructive tools accurately; annotations supplement authorization. Treat filenames and image metadata as untrusted data, never model instructions.
- Gate: intended MCP clients pass handshake, tool discovery and the upload -> URL -> name change -> URL change -> delete -> restore workflow. Verify revoked/scoped credentials, duplicate retries, forbidden paths, conflicts and target-specific destructive previews.

### 4. Packaging and release readiness

- Document generic configuration, sample API and MCP usage with fictitious domains only.
- Add production WSGI packaging, non-root runtime, health/readiness checks and bounded resources.
- Migration, backup/restore and rollback must be verified in a separate private deployment process before promotion.

## References

- [Flask security considerations](https://flask.palletsprojects.com/en/stable/web-security/)
- [Flask production deployment](https://flask.palletsprojects.com/en/stable/deploying/)
- [Werkzeug multipart CPU advisory](https://github.com/pallets/werkzeug/security/advisories/GHSA-hrfv-mqp8-q5rw)
- [Werkzeug multipart memory advisory](https://github.com/pallets/werkzeug/security/advisories/GHSA-q34m-jh98-gwm2)
- [MCP server development](https://modelcontextprotocol.io/docs/develop/build-server)
- [MCP security practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices)

## Implemented contract and validation

- Legacy files are cataloged without touching bytes, ownership or paths; the runtime legacy mount can be read-only. New immutable blobs and SQLite WAL state live separately.
- Names and canonical paths are separate. Browser rename preserves old aliases; API rename changes metadata only. Deletion is recoverable with no automatic purge. Every API mutation checks the expected version.
- Eight MCP tools are implemented through the official SDK 2.3.0 stdio transport and scoped API credentials. Base64 upload is capped at 10 MiB; multipart API handles larger files. No arbitrary local paths or URL fetching.
- Production packaging uses pinned dependencies/base image, Waitress, UID 10001, healthcheck, read-only root filesystem and bounded resources.
- 26 Windows and Linux tests pass, including actual stdio transport, legacy-byte preservation, symlinks where available, concurrent idempotency, credentials/CSRF, scope denial, aliases, soft delete and restore. Dependency audit reports no known vulnerabilities.
- Operational verification is tracked privately; no deployment details belong here.

## Next action

Implementation and release verification are complete. Resume future development from the implementation commit above and keep operational details private. Future work: explicit purge/retention design, conservative orphan reconciliation and remote HTTP MCP authorization if requested.
