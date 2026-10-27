# Account, settings and file manager UI

## Scope

Refresh the browser UI while retaining its dark background and cyan accent. Move MCP connection instructions to an authenticated settings page, provide an account menu with sign-out, and make the file list easier to scan and use.

## Git and links

- Branch / base: `master` / current project baseline
- Worktree: primary checkout
- PR / issue: none
- Exact resume commit: `903d2064912e5cfee31529d31b534163df4741aa`

## Current state

The home page uses a compact upload panel, path breadcrumbs, a responsive file list and per-item action menus. Settings includes the MCP address, client connection guidance and a summary of requested image-management permissions. The account menu displays profile-derived OIDC names without exposing the opaque login identity. Existing OIDC sessions acquire a display label through the normal profile revalidation; SQLite receives one additive column with a default. Local login remains unchanged.

## Verified results

- Python syntax compilation passed for `app.py` and `oidc_sessions.py`.
- Jinja parsed the shared base, file manager and settings templates in the release image.
- `git diff --check` passed.
- No tests were added or run.

## Findings, decisions and blockers

The OIDC subject remains an internal authorization key. MCP setup details are no longer shown in the file browser. Settings remain login-protected, and destructive file actions preserve their existing POST and CSRF behavior.

## Next action

No remaining code work. Keep deployment details and runbooks in private project memory.
