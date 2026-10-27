# Optional OIDC admin authentication

## Scope and resume reference

- Add deployment-selected `local`, `hybrid`, and `oidc` browser login modes while keeping local authentication as the default.
- Branch: `master`, primary checkout. Implementation: `2ba8b01cbd5f389c75f63383f710d5d867e92e32`, based on `2628272420a1228ad06edea515c661c3d547dd8d`.
- Public development handover only. Provider credentials, issuer URLs, production host details and operational procedures belong in private memory.

## Current state

- Local password login remains unchanged in `local` and `hybrid`; `oidc` does not accept local credentials.
- OIDC uses authorization code, PKCE S256, state/nonce validation, an exact callback from the configured public origin and a case-sensitive administrator group claim.
- Provider tokens are encrypted in the persistent SQLite state. Group membership is refreshed within at most 30 seconds; browser authorization fails closed on an unavailable provider, and HTTP MCP token validation applies the same check.
- Logout revokes the local OIDC session. It does not end the provider-wide session.
- OIDC activation requires a confidential OIDC provider client, administrator-group binding, correct group claim mapping and protected deployment secrets. Never infer or link an Authentik account using email or username.

## Verification

- Python compilation and `git diff --check` pass. Release image build and deployment are pending.
- No unit/integration test suite was added or run in this workstream.

## Next action

Build and deploy the reviewed code in a data-preserving release. Activate `hybrid` only after an authorized provider and protected client configuration are available; validate OIDC login, group revocation and MCP token rejection on an isolated candidate before enabling it.
