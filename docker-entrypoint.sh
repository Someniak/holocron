#!/bin/sh
# Holocron container entrypoint.
#
# When the webhook listener is enabled, ensure a TLS certificate exists so the
# listener can serve HTTPS. This auto-generation is a CONTAINER-ONLY convenience
# (pip installs never run this script); on pip you supply --webhook-cert /
# --webhook-key yourself or run plain HTTP.
#
# The cert lives under HOLOCRON_WEBHOOK_CERT / HOLOCRON_WEBHOOK_KEY, defaulted
# below to /certs/webhook.{crt,key} (a declared VOLUME). To use your OWN
# certificate, mount it there (or point those env vars at it): if the cert file
# already exists it is used as-is and never overwritten/regenerated.
set -eu

# Default the cert/key paths here rather than via Dockerfile ENV (which would
# bake a *_KEY variable into image metadata), and export them so the holocron
# process we exec inherits the same locations. Uses ${VAR-default} (no colon) so
# an explicit empty override (-e HOLOCRON_WEBHOOK_CERT=) still disables TLS.
export HOLOCRON_WEBHOOK_CERT="${HOLOCRON_WEBHOOK_CERT-/certs/webhook.crt}"
export HOLOCRON_WEBHOOK_KEY="${HOLOCRON_WEBHOOK_KEY-/certs/webhook.key}"

CERT="$HOLOCRON_WEBHOOK_CERT"
KEY="$HOLOCRON_WEBHOOK_KEY"

# Is the webhook enabled? Check the env flag and the CLI args ("$@").
webhook_enabled() {
    case "${HOLOCRON_WEBHOOK:-}" in
        [Tt][Rr][Uu][Ee] | 1 | [Yy][Ee][Ss]) return 0 ;;
    esac
    for arg in "$@"; do
        [ "$arg" = "--webhook" ] && return 0
    done
    return 1
}

if webhook_enabled "$@" && [ -n "$CERT" ] && [ -n "$KEY" ]; then
    if [ -f "$CERT" ]; then
        echo "[entrypoint] Using existing webhook TLS certificate at $CERT"
    else
        echo "[entrypoint] No certificate at $CERT; generating a self-signed one..."
        mkdir -p "$(dirname "$CERT")" "$(dirname "$KEY")"

        # SAN so clients that verify (and self-hosted GitHub) accept it. Override
        # the hostname with HOLOCRON_WEBHOOK_CN (defaults to "holocron").
        CN="${HOLOCRON_WEBHOOK_CN:-holocron}"
        openssl req -x509 -newkey rsa:2048 -nodes \
            -keyout "$KEY" -out "$CERT" -days 3650 \
            -subj "/CN=${CN}" \
            -addext "subjectAltName=DNS:${CN},DNS:localhost,IP:127.0.0.1" \
            >/dev/null 2>&1

        chmod 600 "$KEY"
        chmod 644 "$CERT"
        echo "[entrypoint] Generated self-signed certificate (CN=${CN}, valid 10 years)."
    fi
fi

exec holocron "$@"
