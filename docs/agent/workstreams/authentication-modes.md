# Optional OIDC admin authentication

## Scope and resume reference

- Add deployment-selected `local`, `hybrid`, and `oidc` browser login modes while keeping local authentication as the default.
- Branch: `master`, primary checkout. Implementation: `c92e533`, based on `2ba8b01cbd5f389c75f63383f710d5d867e92e32`.
- Public development handover only. Provider credentials, issuer URLs, production host details and operational procedures belong in private memory.

## Current state

- Local password login remains unchanged in `local` and `hybrid`; `oidc` does not accept local credentials.
- OIDC uses authorization code, PKCE S256, state/nonce validation, an exact callback from the configured public origin and a case-sensitive administrator group claim.
- Provider tokens are encrypted in the persistent SQLite state. Group membership is refreshed within at most 30 seconds; browser authorization fails closed on an unavailable provider, and HTTP MCP token validation applies the same check.
- Default requested scopes follow the Authentik integration convention: `openid profile email entitlements offline_access`. The entitlements scope mapping must expose the configured groups claim; scopes are deployment-configurable while `openid`, `entitlements`, and `offline_access` remain required.
- Logout revokes the local OIDC session. It does not end the provider-wide session.
- OIDC activation requires a confidential OIDC provider client, administrator-group binding, correct group claim mapping and protected deployment secrets. Never infer or link an Authentik account using email or username.

## Verification

- Python compilation and `git diff --check` pass. The release builds in the deployment environment; provider-backed sign-in still requires an authorized Authentik application/provider, group mapping and protected client configuration.
- No unit/integration test suite was added or run in this workstream.

## Next action

Complete provider setup using the team's Authentik integration convention, then configure the issuer, confidential client and exact administrator group in protected deployment settings. Start in `hybrid`, validate sign-in, group revocation and MCP token rejection, then select `oidc` if SSO-only is desired. Keep provider credentials, issuer URLs and runtime operations in private deployment documentation.
