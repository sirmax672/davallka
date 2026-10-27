# Open development questions

Implemented defaults: rename changes metadata; canonical URL changes preserve the old alias by default; explicit revocation is supported. Deletes are recoverable indefinitely and never purge bytes automatically. Catalog access requires authentication. MCP supports stdio and OAuth-protected Streamable HTTP with bounded uploads, and has no arbitrary filesystem/URL access.

| Future choice | Owner / next action |
| --- | --- |
| Permanent purge and retention window | Product owner: specify retention, recovery and exact-target authorization before implementing physical deletion. |
| Client-side local file helper | Implementer: add only with explicit permitted roots and symlink policy; remote paths are not laptop paths. |
| Orphan/staging reclamation | Implementer: add a conservative private reconciliation workflow; never infer that age means unused. |
