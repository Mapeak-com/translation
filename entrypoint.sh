#!/bin/bash
# Runs the Google Translate adapter on the loopback and nginx in front of it. If either dies the
# container exits, so docker's restart policy sees the failure instead of the container lingering
# half-alive.
set -euo pipefail

if [ -z "${GOOGLE_API_KEY:-}" ]; then
    echo "entrypoint: GOOGLE_API_KEY is not set" >&2
    exit 1
fi

python3 -u /usr/local/bin/adapter.py &
adapter_pid=$!

nginx -c /etc/nginx/nginx.conf &
nginx_pid=$!

terminate() {
    kill -TERM "${adapter_pid}" "${nginx_pid}" 2>/dev/null || true
}
trap terminate TERM INT

wait -n
terminate
wait || true
