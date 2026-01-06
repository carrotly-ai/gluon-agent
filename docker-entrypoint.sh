#!/bin/bash
# Docker entrypoint for gluon-agent
# Registers MCP servers from mounted .mcp.json on first run

set -e

# Source version info if available (set during Docker build)
if [ -f /tmp/version.env ]; then
    set -a
    . /tmp/version.env
    # Convert VITE_ prefixed vars to GLUON_ prefixed for backend
    export GLUON_VERSION="${VITE_APP_VERSION:-}"
    export GLUON_FULL_VERSION="${VITE_APP_FULL_VERSION:-}"
    export GLUON_BUILD_TIME="${VITE_APP_BUILD_TIME:-}"
    set +a
fi

# Configure git to use HTTPS with GH_TOKEN instead of SSH
configure_git_auth() {
    # Configure user identity if provided
    if [ -n "$GIT_USER_EMAIL" ]; then
        git config --global user.email "$GIT_USER_EMAIL"
        echo "Git user.email: $GIT_USER_EMAIL"
    fi
    if [ -n "$GIT_USER_NAME" ]; then
        git config --global user.name "$GIT_USER_NAME"
        echo "Git user.name: $GIT_USER_NAME"
    fi

    if [ -n "$GH_TOKEN" ]; then
        # Rewrite SSH URLs to HTTPS (need separate sections for each pattern)
        git config --global url."https://github.com/".insteadOf "git@github.com:"
        git config --global --add url."https://github.com/".insteadOf "ssh://git@github.com/"

        # Configure credential helper to use GH_TOKEN
        git config --global credential.helper '!f() { echo "username=x-access-token"; echo "password=${GH_TOKEN}"; }; f'

        echo "Git configured to use HTTPS with GH_TOKEN"
    fi
}

# Function to register MCP servers from .mcp.json
register_mcp_servers() {
    local MCP_CONFIG="${HOME}/.claude/.mcp.json"

    # Check if config file exists
    if [ ! -f "$MCP_CONFIG" ]; then
        echo "No MCP config found at $MCP_CONFIG, skipping MCP registration"
        return 0
    fi

    echo "Checking MCP server registration..."

    # Check if servers are already registered
    local registered_servers
    registered_servers=$(claude mcp list 2>/dev/null | grep -E "^\S+:" | cut -d: -f1 || true)

    # Parse .mcp.json and register each server
    # Using python since jq might not be available
    python3 << 'EOF'
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

# Get currently registered servers
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

        # Build command
        cmd = ["claude", "mcp", "add", "--transport", server_type, "--scope", "user", name, url]

        # Add env vars if present
        headers = server_config.get("headers", {})
        for key, value in headers.items():
            cmd.extend(["--env", f"{key}={value}"])

        print(f"  {name}: registering {server_type} server at {url}")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            print(f"  {name}: registered successfully")
        except subprocess.CalledProcessError as e:
            # Check if already exists
            if "already exists" in e.stderr:
                print(f"  {name}: already registered")
            else:
                print(f"  {name}: registration failed - {e.stderr}")
    else:
        print(f"  {name}: skipping (stdio servers not auto-registered)")

print("MCP registration complete")
EOF
}

# Configure git authentication
configure_git_auth

# Register MCP servers on first run
if [ -f "${HOME}/.claude/.mcp.json" ]; then
    register_mcp_servers
fi

# Execute the command passed to docker run
exec "$@"
