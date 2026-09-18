#!/usr/bin/env bash
# Start the terminal for a hosted preview: an e2b sandbox, a Codespace, or
# anything else that reaches the engine through a proxy on a name the operator
# did not choose.
#
# Why this exists: the engine refuses to serve a non-loopback host unless that
# host is named, and it refuses to bind beyond loopback without one -- see
# SECURITY.md. In a sandbox the name is derived from the platform, so derive it
# here rather than making whoever is looking at the preview copy an id around
# and get an unexplained 403 when they get it wrong.
#
#   tools/preview.sh [port]              # default 7787
#   LSET_BIN=.venv/bin/lset tools/preview.sh
#
# Serves read and data endpoints. Code execution, the shell, workspace writes
# and broker actions stay refused, because a preview URL is not a trusted
# client. To serve those too, opt in explicitly and knowingly:
#
#   LSE_PREVIEW_ARGS=--allow-remote-exec tools/preview.sh
set -euo pipefail

PORT="${1:-7787}"
SANDBOX_ID="${E2B_SANDBOX_ID:-}"
LSET_BIN="${LSET_BIN:-lset}"

if [ -z "$SANDBOX_ID" ]; then
    echo "tools/preview.sh: no E2B_SANDBOX_ID in the environment." >&2
    echo >&2
    echo "  Outside a sandbox, start the engine directly and name the host" >&2
    echo "  your clients or proxy will use:" >&2
    echo >&2
    echo "    lset --host 0.0.0.0 --no-browser --trusted-host <that host>" >&2
    exit 2
fi

PREVIEW_HOST="${PORT}-${SANDBOX_ID}.e2b.app"

if ! command -v "$LSET_BIN" >/dev/null 2>&1 && [ ! -x "$LSET_BIN" ]; then
    echo "tools/preview.sh: cannot find '$LSET_BIN'." >&2
    echo "  Set LSET_BIN to the executable you installed, e.g." >&2
    echo "  LSET_BIN=.venv/bin/lset tools/preview.sh" >&2
    exit 2
fi

echo "LSE Terminal preview -> https://${PREVIEW_HOST}"

# shellcheck disable=SC2086  # LSE_PREVIEW_ARGS is deliberately word-split.
exec "$LSET_BIN" --host 0.0.0.0 --port "$PORT" --no-browser \
     --trusted-host "$PREVIEW_HOST" ${LSE_PREVIEW_ARGS:-}
