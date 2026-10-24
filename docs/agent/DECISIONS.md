# Development decisions

## Accepted

- Keep this public handover limited to development. Operational runbooks and deployment-specific information are excluded (owner instruction, 2026-10-24).
- Use `docs/agent/` as the sole live handover home for development of this repository (owner confirmation, 2026-10-24).
- The requested scope is assessment, documentation and a plan including MCP. Application remediation and deployment require a subsequent implementation task.

## Proposed, pending implementation design

- Introduce stable asset IDs and separate display/download names from canonical public URL paths.
- Build a shared service layer and authenticated management API before exposing MCP tools.
- Preserve existing valid public links through an explicit migration and alias policy.
- Keep filesystem blob storage initially; evaluate object storage only when operational requirements justify migration.
