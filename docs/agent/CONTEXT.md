# d'Avallka development context

## Purpose

A small Flask file-sharing application: authenticated file management and public direct file links.

## Architecture and setup

- `app.py`: hardened browser routes, environment-based administrator login and scoped image API.
- `storage.py`: transactional SQLite catalog, immutable legacy tree and separate managed blobs.
- `mcp_server.py`: official SDK stdio adapter calling the management API.
- `templates/`: Jinja browser interface; static branding under `static/`.
- `requirements.txt`: Python dependencies; `Dockerfile` and `docker-compose.yml`: example packaging.
- Local development: create a virtual environment, install `requirements.txt`, set non-example credentials and a random session secret, then run `python app.py` on an unprivileged local port.
- Existing content URLs: `/view/<path>` and `/download/<path>`; `/api/files` now requires browser login and lists only root-level files.
- The existing public-link behavior is part of the product contract. Public assets must not contain confidential data.

## Documentation boundary

This is a public repository. These handover files contain only development context, code findings and implementation plans. Never add deployment hostnames, operator accounts, infrastructure paths, live configuration, production security observations, credentials or operational runbooks here. Operational knowledge is maintained separately in private memory.

## Working rules

- Read `CURRENT.md`, `WORKSTREAMS.md` and the relevant card before resuming work.
- Keep workstream state concise and current; source code and tests belong in the repository.
- Validate file mutations using isolated temporary fixtures, never deployment data.
- See README for required secure configuration, API/MCP contracts and validation commands.
