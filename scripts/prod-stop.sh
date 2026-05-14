#!/usr/bin/env bash
# Stop the production container.
#
# Usage:
#   scripts/prod-stop.sh

set -euo pipefail
source "$(dirname "$0")/_lib.sh"
require_docker_compose

info "Stopping ${PROD_CONTAINER}"
dc "${PROD_COMPOSE}" down

success "Production stopped"
