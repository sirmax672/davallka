# MCP consent callback

## Scope and Git

- Fix browser return from OAuth consent to a registered MCP client.
- Branch/worktree: `master`, primary checkout; base `11662a5`.
- PR/issue: none. Exact implementation/resume commit: `d137cfcb67ee95a46409aa551f630a322e51ccd8`.

## Current state

Chromium applies CSP `form-action` to redirects after form submissions. Permitting the registered callback origin in the form policy handled a direct callback but still blocked clients whose callback redirected to a second origin.

The consent POST now returns a same-origin HTML document. The document then navigates to the validated registered callback and provides a manual link if JavaScript is unavailable. The form policy stays self-only. Callback registration, CSRF, single-use codes and recovery for expired or consumed consent requests are unchanged.

## Verification

- 38 tests passed on Windows, including headless Chromium following an HTTP 308 from the registered callback to a third local origin.
- The Linux image passed 37 tests with the Chromium test skipped because no browser is installed there.
- An isolated production-image candidate passed discovery, dynamic registration, consent, PKCE token exchange and MCP tool discovery.
- Regression coverage includes login return, callback state, token exchange, denial, CSRF, expired/repeated consent and callback URL validation.
- Browser fixtures use synthetic clients, credentials and temporary state; no real account or image data is involved.

## Next action

No remaining implementation work. Operational details belong in private memory.
