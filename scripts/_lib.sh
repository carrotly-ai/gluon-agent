#!/usr/bin/env bash
# Shared helpers for the gluon-agent convenience scripts.
# Sourced by all `prod-*.sh` and `dev-*.sh` scripts in this directory.

set -euo pipefail

# Resolve the repo root regardless of where the script was invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Compose files (relative to repo root).
PROD_COMPOSE="${REPO_ROOT}/docker-compose.yml"
DEV_COMPOSE="${REPO_ROOT}/docker-compose.dev.yml"

# Containers — must match `container_name` in the compose files.
PROD_CONTAINER="gluon-agent"
DEV_CONTAINER="gluon-agent-dev"

# Colors (skip when not on a TTY so log captures stay clean).
if [[ -t 1 ]]; then
  C_BOLD="$(printf '\033[1m')"
  C_DIM="$(printf '\033[2m')"
  C_RED="$(printf '\033[31m')"
  C_GREEN="$(printf '\033[32m')"
  C_YELLOW="$(printf '\033[33m')"
  C_BLUE="$(printf '\033[34m')"
  C_RESET="$(printf '\033[0m')"
else
  C_BOLD="" C_DIM="" C_RED="" C_GREEN="" C_YELLOW="" C_BLUE="" C_RESET=""
fi

info()    { printf "%s==>%s %s\n" "${C_BLUE}${C_BOLD}" "${C_RESET}" "$*"; }
success() { printf "%s✓%s %s\n" "${C_GREEN}" "${C_RESET}" "$*"; }
warn()    { printf "%s!%s %s\n" "${C_YELLOW}" "${C_RESET}" "$*" >&2; }
error()   { printf "%s✗%s %s\n" "${C_RED}" "${C_RESET}" "$*" >&2; }
step()    { printf "%s$%s %s\n" "${C_DIM}" "${C_RESET}" "$*"; }

# Verify `docker compose` is available (v2 plugin syntax).
require_docker_compose() {
  if ! command -v docker >/dev/null 2>&1; then
    error "docker not found in PATH"
    exit 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    error "docker compose (v2 plugin) not available — install Docker Desktop / OrbStack / docker-compose-plugin"
    exit 1
  fi
}

# Print the leading comment block of the calling script as help text.
# Skips the shebang and stops at the first non-comment line.
print_help() {
  local script="${1:-$0}"
  awk '
    NR == 1 && /^#!/ { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "${script}"
}

# Run `docker compose -f <file> <args...>`, echoed first so the user sees it.
dc() {
  local file="$1"; shift
  step "docker compose -f $(basename "${file}") $*"
  docker compose -f "${file}" "$@"
}

# Print a short health summary for a container (status + last image).
container_status() {
  local name="$1"
  if ! docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
    warn "container ${name} does not exist"
    return 0
  fi
  local status image
  status="$(docker inspect -f '{{.State.Status}}' "${name}" 2>/dev/null || echo unknown)"
  image="$(docker inspect -f '{{.Config.Image}}' "${name}" 2>/dev/null || echo unknown)"
  printf "   container: %s\n   status:    %s\n   image:     %s\n" "${name}" "${status}" "${image}"
}
