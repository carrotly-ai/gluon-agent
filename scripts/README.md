# Convenience scripts

Shortcut wrappers around `docker compose` for managing gluon-agent
deployments. Two flavours:

- **prod-*** — production: pulls `ghcr.io/carrotly-ai/gluon-agent:latest`
  via `docker-compose.yml`, container `gluon-agent`, reads `.env`.
- **dev-*** — local development: builds from source via
  `docker-compose.dev.yml`, container `gluon-agent-dev`, reads `.env.local`.

All scripts can be invoked from any working directory — they resolve
paths relative to the repo root themselves.

## Production (`.env` required)

| Script | What it does |
|---|---|
| `prod-update.sh` | `docker compose pull && docker compose up -d` — pull latest image from GHCR and recreate if changed |
| `prod-update.sh --logs` | Same as above, then tail logs |
| `prod-restart.sh` | Restart without pulling (use after editing `.env`) |
| `prod-stop.sh` | `docker compose down` |
| `prod-logs.sh [N]` | Tail last N lines (default 100), follow |

## Development (`.env.local` required)

| Script | What it does |
|---|---|
| `dev-rebuild.sh` | Cached build + restart, populates VITE version args from git |
| `dev-rebuild.sh --no-cache` | Full rebuild (slow — use after dep/Dockerfile changes) |
| `dev-rebuild.sh --logs` | Rebuild + restart + tail logs |
| `dev-restart.sh` | Restart without rebuilding (use after editing `.env.local`) |
| `dev-stop.sh` | `docker compose -f docker-compose.dev.yml down` |
| `dev-logs.sh [N]` | Tail last N lines (default 100), follow |

## Typical workflows

**Updating production to the newest GHCR release:**
```bash
scripts/prod-update.sh --logs
```

**Iterating on local code:**
```bash
scripts/dev-rebuild.sh        # fast, cached
scripts/dev-logs.sh
```

**After bumping a dependency in `pyproject.toml` or editing the Dockerfile:**
```bash
scripts/dev-rebuild.sh --no-cache
```
