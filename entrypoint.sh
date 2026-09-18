#!/bin/bash
# Runs LTEngine on the loopback and nginx in front of it. If either dies the container exits, so
# docker's restart policy sees the failure instead of the container lingering half-alive.
set -euo pipefail

ltengine --host 127.0.0.1 --port 5051 --cpu -m "${LTENGINE_MODEL}" &
ltengine_pid=$!

nginx -c /etc/nginx/nginx.conf &
nginx_pid=$!

terminate() {
    kill -TERM "${ltengine_pid}" "${nginx_pid}" 2>/dev/null || true
}
trap terminate TERM INT

wait -n
terminate
wait || true
