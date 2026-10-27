# Current development state

## Goal

Maintain hardened file sharing, an authenticated image API and MCP access while preserving legacy bytes and links.

## State

The hardening, image API and MCP implementation is complete at `ec2ffd18630740b186981c37e456d28752b42897`. The service uses a transactional catalog over read-only legacy assets and separate managed blobs. Existing configured administrator credentials remain supported; unsafe session secrets fail startup. API/MCP support upload, metadata rename, URL changes with aliases, recoverable deletion and restoration.

MCP is available over stdio and remote Streamable HTTP. The HTTP transport uses OAuth authorization code with PKCE, dynamic client registration, resource-bound tokens, and explicit consent through the existing administrator login. Both transports expose the same eight image-management tools. A disposable candidate passed OAuth discovery, consent, PKCE exchange, refresh/revocation and image lifecycle checks; no production user or image was used for those checks. Operational release evidence and recovery details stay private.

Browser authentication now has deployment modes `local`, `hybrid`, and `oidc`; `local` remains the default. OIDC uses an exact configured administrator group, encrypted server-side token storage, bounded group revalidation, and the same authorization check for remote MCP tokens. Enabling OIDC requires a confidential provider client and its secret in protected runtime configuration. Do not add provider URLs, deployment state, client identifiers or secrets here.

Windows and Linux validation for the existing hardening/API/stdio release passed 26 tests. The remote OAuth flow passed an isolated candidate smoke check. Python dependency audit has no known findings.

## Constraints

No destructive migration or automatic purge. Keep state persistent and separate from the legacy mount. No arbitrary URL fetching or server-path upload tools. Keep all host-specific release information out of this public handover.

See [the authentication workstream card](workstreams/authentication-modes.md) and [the hardening/MCP card](workstreams/hardening-and-mcp.md).
