# syntax=docker/dockerfile:1
# A LibreTranslate-compatible translation API backed by Google Cloud Translation, with a caching
# nginx in front of it so the service is self-contained: anything proxying to port 5050 gets the
# translation cache for free. See nginx.conf for the caching rules and adapter.py for the API.
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl nginx python3 bash \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --no-create-home --shell /usr/sbin/nologin translation \
    && mkdir -p /var/cache/nginx/translation \
    && chown -R translation:translation /var/cache/nginx

COPY adapter.py /usr/local/bin/adapter.py
COPY nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

# Every translation is paid for once and then served from here, so keep this on a volume - a
# container recreated without it starts cold and pays Google again for everything.
VOLUME ["/var/cache/nginx"]
EXPOSE 5050
USER translation

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5050/health -o /dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
