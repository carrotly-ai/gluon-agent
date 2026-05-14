#!/usr/bin/env bash
# Restart the dev container using the existing image — no rebuild.
# Useful after editing .env.local or mounted config.
#
# Usage:
#   scripts/dev-restart.sh

set -euo pipefail
source "$(dirname "$0")/_lib.sh"
require_docker_compose

info "Restarting ${DEV_CONTAINER}"
dc "${DEV_COMPOSE}" up -d --force-recreate

success "Dev restarted"
container_status "${DEV_CONTAINER}"
