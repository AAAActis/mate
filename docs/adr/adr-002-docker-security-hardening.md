# ADR-002: Docker Security Hardening

## Status
Accepted

## Context
The Mate application runs in a Docker container with strict security requirements: read-only filesystem, dropped capabilities, no new privileges, and minimal attack surface.

## Decision
Use the multi-stage `Dockerfile` (`python:3.12-slim`) with these security measures:

1. **Non-root user**: `useradd -u 1654 -M -s /usr/sbin/nologin app` creates user 1654; `USER 1654` at the end drops privileges.
2. **Read-only filesystem**: `read_only: true` in docker-compose; critical paths `/data` (volume) and `/tmp` (tmpfs) are writable.
3. **Capability dropping**: `cap_drop: [ALL]` and `security_opt: [no-new-privileges:true]` in docker-compose.
4. **Healthcheck without wget/curl**: Use `python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=3).status == 200 else 1)"` since slim images don't have wget/curl.
5. **Single uvicorn worker**: `--workers 1` to avoid scheduler/SSE issues with multiple workers.
6. **SQLite in /data**: Database path `/data/mate.db` mounted as volume; only `/data` and `/tmp` are writable in read-only mode.
7. **Logging**: `json-file` driver with `max-size: 10m` and `max-file: 3` to prevent disk exhaustion.
8. **Network**: `arch-net` external network + `default`; no host port exposure beyond `127.0.0.1:5003:8080`.

## Consequences
- **Positive**: Hardened container suitable for production; defense-in-depth approach.
- **Negative**: Debugging inside read-only container is harder; user 1654 must have write access to /data and /tmp.

## Dependencies
- `healthcheck.py` with urllib.request (already created).
- `docker-compose.prod.yml` with all security settings (already created).

---
*This ADR codifies the Docker security configuration for the Mate production deployment.*