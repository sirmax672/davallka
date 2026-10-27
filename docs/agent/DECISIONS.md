# Development decisions

## Accepted

- Keep this public handover limited to development. Operational runbooks and deployment-specific information are excluded (owner instruction, 2026-10-24).
- Use `docs/agent/` as the sole live handover home for development of this repository (owner confirmation, 2026-10-24).
- The owner authorized implementation of the hardening/API/MCP plan; deployment-specific authorization and procedures remain private.

## Implemented design

- Introduce stable asset IDs and separate display/download names from canonical public URL paths.
- Build a shared service layer and authenticated management API before exposing MCP tools.
- Preserve existing valid public links through an explicit migration and alias policy.
- Keep filesystem blob storage initially; evaluate object storage only when operational requirements justify migration.

- Preserve legacy bytes in place using a read-only mount; store mutations in a separate transactional catalog and new blob directory.
- Keep both stdio MCP and remote Streamable HTTP MCP. Protect remote access with OAuth authorization code + PKCE, resource-bound tokens, `images:manage`, and consent through the existing administrator login; do not create a second user store.
- No automatic purge; URL changes preserve the previous alias by default.
