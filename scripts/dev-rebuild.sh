#!/usr/bin/env bash
# Rebuild the local development image from source and restart the dev
# container. The default is a cached build (fast); pass --no-cache for a
# clean rebuild after dependency or Dockerfile changes.
#
# Build args are populated from git so the web dashboard shows the right
# version banner (VITE_APP_VERSION / VITE_APP_FULL_VERSION / build time).
#
# Usage:
#   scripts/dev-rebuild.sh                 # cached build + restart
#   scripts/dev-rebuild.sh --no-cache      # clean rebuild + restart
#   scripts/dev-rebuild.sh --logs          # rebuild + restart + tail logs
#   scripts/dev-rebuild.sh --no-cache -f   # both

set -euo pipefail
source "$(dirname "$0")/_lib.sh"
require_docker_compose

NO_CACHE=0
FOLLOW_LOGS=0
for arg in "$@"; do
  case "${arg}" in
    --no-cache) NO_CACHE=1 ;;
    --logs|-f) FOLLOW_LOGS=1 ;;
    -h|--help) print_help "$0"; exit 0 ;;
    *) error "unknown flag: ${arg}"; exit 2 ;;
  esac
done

if [[ ! -f "${REPO_ROOT}/.env.local" ]]; then
  warn ".env.local not found at ${REPO_ROOT}/.env.local — dev compose will fail to start"
  warn "copy .env.example to .env.local and fill in credentials first"
  exit 1
fi

# Populate the build args the Dockerfile reads. Falls back gracefully if
# git isn't available or this isn't a working tree.
if git -C "${REPO_ROOT}" rev-parse --short HEAD >/dev/null 2>&1; then
  export VITE_APP_VERSION="$(git -C "${REPO_ROOT}" rev-parse --short HEAD)"
  export VITE_APP_FULL_VERSION="$(git -C "${REPO_ROOT}" describe --always --dirty 2>/dev/null || echo "${VITE_APP_VERSION}")"
else
  export VITE_APP_VERSION="dev"
  export VITE_APP_FULL_VERSION="development"
fi
export VITE_APP_BUILD_TIME="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

info "Building image (version=${VITE_APP_FULL_VERSION})"
if [[ "${NO_CACHE}" -eq 1 ]]; then
  dc "${DEV_COMPOSE}" build --no-cache
else
  dc "${DEV_COMPOSE}" build
fi

info "Recreating ${DEV_CONTAINER}"
dc "${DEV_COMPOSE}" up -d --force-recreate

success "Dev rebuilt and restarted"
container_status "${DEV_CONTAINER}"

if [[ "${FOLLOW_LOGS}" -eq 1 ]]; then
  info "Tailing logs (Ctrl-C to exit)"
  dc "${DEV_COMPOSE}" logs -f --tail=50
fi
