# MCP consent callback

## Scope and Git

- Fix browser return from OAuth consent to a registered MCP client.
- Branch/worktree: `master`, primary checkout; base `9a59d01`.
- PR/issue: none. Implementation commit: pending.

## Current state

Chromium applies CSP `form-action` to redirects after form submissions. A self-only consent policy blocked the callback after the authorization request was already consumed, making a repeated click report a missing request.

The consent response now permits the validated registered callback origin in its own `form-action` policy. Other pages retain self-only forms. Callback sources are built from validated scheme, hostname and port; path/query content cannot inject CSP directives. Consent uses an explicit 303 redirect, keeps one-use authorization codes and CSRF, and suppresses duplicate form submissions. Expired or consumed links show an HTML recovery page that tells the user to restart authorization in their MCP client.

## Verification

- 38 tests passed on Windows, including a real headless Chromium callback using an isolated temporary profile and two local origins.
- Regression coverage includes login return, callback state, token exchange, denial, CSRF, expired/repeated consent and CSP origin validation.
- Browser fixtures use synthetic clients, credentials and temporary state; no real account or image data is involved.

## Next action

Finish the release and update this card's implementation reference. Operational details belong in private memory.
