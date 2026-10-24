# Open development questions

| Question | Recommended starting point | Owner / next action |
| --- | --- | --- |
| Does changing a URL preserve the old URL? | Default to an alias; allow explicit revocation. Never reuse revoked aliases silently. | Product owner: accept behavior before API implementation. |
| Does renaming mean display name or download filename? | Update both metadata values without changing the public URL; URL changes use a separate operation. | Product owner: confirm semantics with examples. |
| How should deletion and retention work? | Soft delete, restore window and explicit scheduled purge; reject deleting an unexpected version. | Product owner: choose retention window and purge authority. |
| Should the catalog be public? | Keep direct assets public and require authorization for inventory and management. | Implementer: document intentional API compatibility change. |
| Which upload sources are needed? | First support bounded byte uploads and client-side local-file upload. Leave arbitrary URL fetching disabled. | Implementer: verify actual client capabilities. |
| Which MCP transports are needed? | Start with a local stdio bridge; add remote HTTP only with an agreed authorization design. | Implementer: test intended clients before choosing SDK/transport versions. |
