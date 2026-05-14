#!/usr/bin/env bash
# Restart the production container without pulling a new image.
# Useful after editing .env or volume-mounted config.
#
# Usage:
#   scripts/prod-restart.sh

set -euo pipefail
source "$(dirname "$0")/_lib.sh"
require_docker_compose

info "Restarting ${PROD_CONTAINER}"
dc "${PROD_COMPOSE}" up -d --force-recreate

success "Production restarted"
container_status "${PROD_CONTAINER}"
