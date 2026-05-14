#!/usr/bin/env bash
# Tail the production container's logs.
#
# Usage:
#   scripts/prod-logs.sh           # follow, last 100 lines
#   scripts/prod-logs.sh 500       # follow, last 500 lines

set -euo pipefail
source "$(dirname "$0")/_lib.sh"
require_docker_compose

TAIL="${1:-100}"
dc "${PROD_COMPOSE}" logs -f --tail="${TAIL}"
