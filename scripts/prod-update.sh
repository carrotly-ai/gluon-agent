#!/usr/bin/env bash
# Pull the latest gluon-agent image from GHCR and restart the production
# container. Idempotent — if the image is already up to date, the container
# is left running.
#
# Usage:
#   scripts/prod-update.sh           # pull + recreate, then exit
#   scripts/prod-update.sh --logs    # pull + recreate, then tail logs

set -euo pipefail
source "$(dirname "$0")/_lib.sh"
require_docker_compose

FOLLOW_LOGS=0
for arg in "$@"; do
  case "${arg}" in
    --logs|-f) FOLLOW_LOGS=1 ;;
    -h|--help) print_help "$0"; exit 0 ;;
    *) error "unknown flag: ${arg}"; exit 2 ;;
  esac
done

if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  warn ".env not found at ${REPO_ROOT}/.env — production compose will fail to start"
  warn "copy .env.example to .env and fill in credentials first"
  exit 1
fi

info "Pulling latest image from GHCR"
dc "${PROD_COMPOSE}" pull

info "Recreating container (will no-op if image unchanged AND config unchanged)"
dc "${PROD_COMPOSE}" up -d

success "Production updated"
container_status "${PROD_CONTAINER}"

if [[ "${FOLLOW_LOGS}" -eq 1 ]]; then
  info "Tailing logs (Ctrl-C to exit)"
  dc "${PROD_COMPOSE}" logs -f --tail=50
fi
