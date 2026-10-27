# Account, settings and file manager UI

## Scope

Refresh the browser UI while retaining its dark background and cyan accent. Move MCP connection instructions to an authenticated settings page, provide an account menu with sign-out, and make the file list easier to scan and use.

## Git and links

- Branch / base: `master` / current project baseline
- Worktree: primary checkout
- PR / issue: none
- Exact resume commit: pending

## Current state

The home page uses a compact upload panel, path breadcrumbs, a responsive file list and per-item action menus. Settings includes the MCP address, client connection guidance and a summary of requested image-management permissions. The account menu displays profile-derived OIDC names without exposing the opaque login identity. Existing OIDC sessions acquire a display label through the normal profile revalidation; SQLite receives one additive column with a default. Local login remains unchanged.

## Verified results

- Python syntax compilation passed for `app.py` and `oidc_sessions.py`; rerun after final edits.
- `git diff --check` passed before the latest template changes; rerun after editing.
- The local Python environment does not include Jinja2, so template parsing remains to be checked in the release image before rollout.
- No tests were added or run.

## Findings, decisions and blockers

The OIDC subject remains an internal authorization key. MCP setup details are no longer shown in the file browser. Settings remain login-protected, and destructive file actions preserve their existing POST and CSRF behavior.

## Next action

Recheck Python syntax and the intended diff, parse templates with the release image, then finish the authorized release while preserving the existing catalog, blobs, legacy assets and runtime settings. Keep operational details in private project memory.
