#!/usr/bin/env bash
# Tail the dev container's logs.
#
# Usage:
#   scripts/dev-logs.sh           # follow, last 100 lines
#   scripts/dev-logs.sh 500       # follow, last 500 lines

set -euo pipefail
source "$(dirname "$0")/_lib.sh"
require_docker_compose

TAIL="${1:-100}"
dc "${DEV_COMPOSE}" logs -f --tail="${TAIL}"
