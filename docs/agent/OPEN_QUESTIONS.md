# Open development questions

Implemented defaults: rename changes metadata; canonical URL changes preserve the old alias by default; explicit revocation is supported. Deletes are recoverable indefinitely and never purge bytes automatically. Catalog access requires authentication. MCP uses stdio and bounded byte uploads, with no arbitrary filesystem/URL access.

| Future choice | Owner / next action |
| --- | --- |
| Permanent purge and retention window | Product owner: specify retention, recovery and exact-target authorization before implementing physical deletion. |
| Remote HTTP MCP | Product owner and implementer: select clients and an OAuth/resource authorization design before exposing HTTP transport. |
| Client-side local file helper | Implementer: add only with explicit permitted roots and symlink policy; remote paths are not laptop paths. |
| Orphan/staging reclamation | Implementer: add a conservative private reconciliation workflow; never infer that age means unused. |
