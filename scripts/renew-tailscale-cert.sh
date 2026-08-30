#!/usr/bin/env bash
# Renew the Tailscale-issued Let's Encrypt certificate used by the web
# dashboard's HTTPS, then restart the container so uvicorn re-reads it.
#
# Tailscale certs are 90-day Let's Encrypt certs with no auto-renewal, so
# run this before expiry (a monthly cron is a good idea). The cert files
# live in ~/.gluon/ssl/ and are referenced by GLUON_SSL_CERTFILE /
# GLUON_SSL_KEYFILE (container paths) in .env / .env.local — see docs/DOCKER.md.
#
# The MagicDNS name is auto-detected from the local tailscaled, so the same
# script works unmodified on every box (e.g. ix.tail101c2a.ts.net on one host,
# hwi.tail101c2a.ts.net on another). Pass a domain to override detection.
#
# One-time setup so renewal needs no sudo:
#   sudo tailscale set --operator=$USER
#
# Usage:
#   scripts/renew-tailscale-cert.sh                # detect domain, renew, restart dev container
#   scripts/renew-tailscale-cert.sh --prod         # restart the prod container instead
#   scripts/renew-tailscale-cert.sh --no-restart   # renew the cert only, leave containers alone
#   scripts/renew-tailscale-cert.sh <domain>       # force a specific MagicDNS name

set -euo pipefail
source "$(dirname "$0")/_lib.sh"

SSL_DIR="${HOME}/.gluon/ssl"
TARGET="dev"
RESTART=1
DOMAIN=""

for arg in "$@"; do
  case "${arg}" in
    --prod) TARGET="prod" ;;
    --dev) TARGET="dev" ;;
    --no-restart) RESTART=0 ;;
    -h|--help) print_help "$0"; exit 0 ;;
    -*) error "unknown flag: ${arg}"; exit 2 ;;
    *) DOMAIN="${arg}" ;;
  esac
done

# Resolve the tailscale CLI. On Linux it's a normal binary in PATH; on macOS
# the GUI app ships the CLI at a fixed path that isn't on PATH for
# non-interactive shells (it's usually exposed only via a shell alias).
TS="$(command -v tailscale || true)"
if [[ -z "${TS}" ]]; then
  for candidate in \
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale" \
    "/usr/local/bin/tailscale" \
    "/opt/homebrew/bin/tailscale"; do
    if [[ -x "${candidate}" ]]; then TS="${candidate}"; break; fi
  done
fi
if [[ -z "${TS}" ]]; then
  error "tailscale CLI not found — install Tailscale first"
  error "(macOS: expected /Applications/Tailscale.app/Contents/MacOS/Tailscale)"
  exit 1
fi

# Auto-detect the MagicDNS name from tailscale's own usage hint, which prints
#   For domain, use "<host>.<tailnet>.ts.net".
# This avoids a jq dependency and is exactly what `tailscale cert` expects.
if [[ -z "${DOMAIN}" ]]; then
  DOMAIN="$("${TS}" cert 2>&1 | grep -oE '[a-z0-9-]+\.[a-z0-9-]+\.ts\.net' | head -1 || true)"
  if [[ -z "${DOMAIN}" ]]; then
    error "could not auto-detect the tailnet MagicDNS name — pass it explicitly:"
    error "  scripts/renew-tailscale-cert.sh <host>.<tailnet>.ts.net"
    exit 1
  fi
  info "Detected MagicDNS name: ${C_BOLD}${DOMAIN}${C_RESET}"
fi

mkdir -p "${SSL_DIR}"
info "Renewing cert for ${DOMAIN} into ${SSL_DIR}"
step "tailscale cert ${DOMAIN}"
if ! (cd "${SSL_DIR}" && "${TS}" cert "${DOMAIN}"); then
  error "cert renewal failed"
  error "if this was 'Access denied', run once: ${C_BOLD}sudo tailscale set --operator=\$USER${C_RESET}"
  exit 1
fi

CRT="${SSL_DIR}/${DOMAIN}.crt"
KEY="${SSL_DIR}/${DOMAIN}.key"
chmod 600 "${KEY}"
success "Wrote ${CRT}"
success "Wrote ${KEY} (0600)"

# Surface the new validity window so cron logs show when the next renewal is due.
if command -v openssl >/dev/null 2>&1; then
  NOT_AFTER="$(openssl x509 -in "${CRT}" -noout -enddate 2>/dev/null | cut -d= -f2 || true)"
  [[ -n "${NOT_AFTER}" ]] && info "Valid until: ${C_BOLD}${NOT_AFTER}${C_RESET}"
fi

if [[ "${RESTART}" -eq 0 ]]; then
  warn "Skipping container restart (--no-restart) — the running dashboard keeps the old cert until restarted"
  exit 0
fi

require_docker_compose
if [[ "${TARGET}" == "prod" ]]; then
  COMPOSE="${PROD_COMPOSE}"; CONTAINER="${PROD_CONTAINER}"
else
  COMPOSE="${DEV_COMPOSE}"; CONTAINER="${DEV_CONTAINER}"
fi

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  warn "container ${CONTAINER} is not running — cert renewed, nothing to restart"
  exit 0
fi

info "Restarting ${CONTAINER} to reload the cert"
dc "${COMPOSE}" restart
success "Cert renewed and ${CONTAINER} restarted"
container_status "${CONTAINER}"
