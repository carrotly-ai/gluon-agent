#!/usr/bin/env bash
# Stop the dev container.
#
# Usage:
#   scripts/dev-stop.sh

set -euo pipefail
source "$(dirname "$0")/_lib.sh"
require_docker_compose

info "Stopping ${DEV_CONTAINER}"
dc "${DEV_COMPOSE}" down

success "Dev stopped"
