#!/bin/bash
# Docker entrypoint for gluon-agent
# Runs as root, adjusts UID/GID to match host user, then drops to gluon user via gosu.
# Set PUID/PGID environment variables to match your host user's UID/GID.

set -e

# ─── UID/GID adjustment ─────────────────────────────────────────────────────
# Adjust the gluon user's UID/GID to match the host user so bind-mounted
# volumes have correct ownership. Defaults to 1000:1000 if not set.
PUID=${PUID:-1000}
PGID=${PGID:-1000}

CURRENT_UID=$(id -u gluon)
CURRENT_GID=$(id -g gluon)

if [ "$PGID" != "$CURRENT_GID" ]; then
    groupmod -o -g "$PGID" gluon 2>/dev/null || true
fi

if [ "$PUID" != "$CURRENT_UID" ]; then
    usermod -o -u "$PUID" gluon 2>/dev/null || true
fi

# Fix ownership of internal directories that were created at build time with UID 1000
# Only needed when PUID != 1000 (the build-time default)
if [ "$PUID" != "1000" ] || [ "$PGID" != "1000" ]; then
    chown -R gluon:gluon /home/gluon/.local 2>/dev/null || true
    chown gluon:gluon /home/gluon 2>/dev/null || true
    chown -R gluon:gluon /app 2>/dev/null || true
fi

echo "Running as gluon (uid=$(id -u gluon), gid=$(id -g gluon))"

# ─── Ensure data directories exist ──────────────────────────────────────────
# When Docker bind-mounts a host path that doesn't exist, it creates it as root.
for dir in /home/gluon/.gluon /home/gluon/.gluon/logs /home/gluon/.gluon/images \
           /home/gluon/.gluon/worktrees /home/gluon/.claude /home/gluon/.cache/gluon; do
    mkdir -p "$dir" 2>/dev/null || true
    chown gluon:gluon "$dir" 2>/dev/null || true
done

# ─── Source version info ────────────────────────────────────────────────────
if [ -f /tmp/version.env ]; then
    set -a
    . /tmp/version.env
    export GLUON_VERSION="${VITE_APP_VERSION:-}"
    export GLUON_FULL_VERSION="${VITE_APP_FULL_VERSION:-}"
    export GLUON_BUILD_TIME="${VITE_APP_BUILD_TIME:-}"
    set +a
fi

# ─── Git authentication ────────────────────────────────────────────────────
configure_git_auth() {
    if [ -n "$GIT_USER_EMAIL" ]; then
        gosu gluon git config --global user.email "$GIT_USER_EMAIL"
        echo "Git user.email: $GIT_USER_EMAIL"
    fi
    if [ -n "$GIT_USER_NAME" ]; then
        gosu gluon git config --global user.name "$GIT_USER_NAME"
        echo "Git user.name: $GIT_USER_NAME"
    fi

    if [ -n "$GH_TOKEN" ]; then
        gosu gluon git config --global --unset-all url."https://github.com/".insteadOf 2>/dev/null || true
        gosu gluon git config --global url."https://github.com/".insteadOf "git@github.com:"
        gosu gluon git config --global --add url."https://github.com/".insteadOf "ssh://git@github.com/"
        gosu gluon git config --global credential.helper '!f() { echo "username=x-access-token"; echo "password=${GH_TOKEN}"; }; f'
        echo "Git configured to use HTTPS with GH_TOKEN"
    fi
}

# ─── MCP server registration ───────────────────────────────────────────────
register_mcp_servers() {
    local MCP_CONFIG="/home/gluon/.claude/.mcp.json"

    if [ ! -f "$MCP_CONFIG" ]; then
        echo "No MCP config found at $MCP_CONFIG, skipping MCP registration"
        return 0
    fi

    echo "Checking MCP server registration..."

    gosu gluon python3 << 'EOF'
import json
import subprocess
import os

mcp_config_path = os.path.expanduser("~/.claude/.mcp.json")

try:
    with open(mcp_config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"Could not read MCP config: {e}")
    exit(0)

servers = config.get("mcpServers", {})
if not servers:
    print("No MCP servers in config")
    exit(0)

result = subprocess.run(["claude", "mcp", "list"], capture_output=True, text=True)
registered = set()
for line in result.stdout.splitlines():
    if ":" in line and not line.startswith(" "):
        name = line.split(":")[0].strip()
        if name:
            registered.add(name)

for name, server_config in servers.items():
    if name in registered:
        print(f"  {name}: already registered")
        continue

    server_type = server_config.get("type", "stdio")
    url = server_config.get("url", "")

    if server_type in ("http", "sse"):
        if not url:
            print(f"  {name}: skipping (no URL)")
            continue

        cmd = ["claude", "mcp", "add", "--transport", server_type, "--scope", "user", name, url]

        headers = server_config.get("headers", {})
        for key, value in headers.items():
            cmd.extend(["--env", f"{key}={value}"])

        print(f"  {name}: registering {server_type} server at {url}")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            print(f"  {name}: registered successfully")
        except subprocess.CalledProcessError as e:
            if "already exists" in e.stderr:
                print(f"  {name}: already registered")
            else:
                print(f"  {name}: registration failed - {e.stderr}")
    else:
        print(f"  {name}: skipping (stdio servers not auto-registered)")

print("MCP registration complete")
EOF
}

# ─── Start Redis (event bus) ───────────────────────────────────────────────
start_redis() {
    if command -v redis-server &> /dev/null; then
        redis-server --daemonize yes --save "" --appendonly no --loglevel warning \
            --bind 127.0.0.1 --port 6379 --maxmemory 64mb --maxmemory-policy allkeys-lru
        echo "Redis started (in-memory, event bus only)"
    else
        echo "WARNING: redis-server not found — cross-process events disabled"
    fi
}

# ─── Ensure cross-platform CLI tools ──────────────────────────────────────
# Fix missing symlinks for tools installed in /opt but not on PATH
ensure_cli_tools() {
    # bunx: bun installer doesn't always create the bunx symlink
    if ! command -v bunx &>/dev/null && command -v bun &>/dev/null; then
        ln -sf "$(command -v bun)" /usr/local/bin/bunx
        echo "Created bunx symlink"
    fi

    # biome: install globally if not available (projects may only have darwin binary)
    if ! command -v biome &>/dev/null; then
        npm install -g --silent @biomejs/biome 2>/dev/null && echo "Installed biome globally" || true
    fi
}

# ─── Main ──────────────────────────────────────────────────────────────────
start_redis
ensure_cli_tools
configure_git_auth

if [ -f "/home/gluon/.claude/.mcp.json" ]; then
    register_mcp_servers
fi

# Drop privileges and exec the command as the gluon user
exec gosu gluon "$@"
