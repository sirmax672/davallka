# Current development state

## Goal

Harden the existing file service, establish a supported management API and add an MCP server for image workflows.

## Priority workstreams

1. Security: fail-closed authentication, storage path containment, CSRF, safe content serving and dependency updates.
2. Reliability: atomic mutations, explicit error responses, resource limits and reproducible application packaging.
3. Asset management API: stable identifiers, names, canonical paths, compatibility aliases and recoverable deletion.
4. MCP: upload, retrieve URL, rename, change URL and remove old images through the supported API.

Assessment is complete; implementation and deployment have not started. See [the development card](workstreams/hardening-and-mcp.md) for findings, ordering and acceptance criteria.
