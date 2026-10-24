# Current development state

## Goal

Deliver hardened file sharing, an authenticated image API and stdio MCP while preserving legacy bytes and links.

## State

Implementation is complete. The service uses a transactional catalog over read-only legacy assets and separate managed blobs. Existing configured administrator credentials remain supported; unsafe session secrets fail startup. API/MCP support upload, metadata rename, URL changes with aliases, recoverable deletion and restoration.

Windows validation: 26 tests pass, including a real stdio subprocess and API/MCP lifecycle. The Linux container suite and an isolated restored-data candidate also pass. Python dependency audit has no known findings. Release verification is complete; operational details and recovery state are tracked privately.

## Constraints

No destructive migration or automatic purge. Keep state persistent and separate from the legacy mount. No arbitrary URL fetching or server-path upload tools. Remote HTTP MCP authorization and shared infrastructure upgrades are separate future work.

See [the workstream card](workstreams/hardening-and-mcp.md).
