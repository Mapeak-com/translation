# syntax=docker/dockerfile:1
# LTEngine - a LibreTranslate-compatible translation API backed by a local LLM, with a caching
# nginx in front of it so the service is self-contained: anything proxying to port 5050 gets the
# translation cache for free. See nginx.conf for the caching rules.
#
# Built CPU-only on purpose, so the one image runs both on an arm64 Mac and on a GPU-less server.
# Two faster alternatives, both documented in docker-compose.yml:
#   * CUDA host -> use ghcr.io/libretranslate/ltengine:main, which is built FROM nvidia/cuda
#   * macOS     -> build on the host with --features metal; Docker Desktop has no GPU passthrough
ARG RUST_VERSION=1.96
FROM rust:${RUST_VERSION}-bookworm AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends clang cmake libssl-dev pkg-config git \
    && rm -rf /var/lib/apt/lists/*

# Pinned so a rebuild of the same tag cannot silently change how things translate. Upstream has
# only one release (v0.1.0, May 2025), so a commit is the only thing worth pinning to.
ARG LTENGINE_REF=9b748ddd84c02da9569bd96db3cf33c53e4665fa
RUN git clone --recursive https://github.com/LibreTranslate/LTEngine /build \
    && cd /build \
    && git checkout "${LTENGINE_REF}"

WORKDIR /build
RUN cargo build --locked --release -p ltengine \
    && install -Dm755 -s target/release/ltengine /out/ltengine

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libssl3 libgomp1 curl nginx bash \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --no-create-home --shell /usr/sbin/nologin ltengine \
    && mkdir -p /models /var/cache/nginx/translation \
    && chown -R ltengine:ltengine /models /var/cache/nginx

COPY --from=builder /out/ltengine /usr/local/bin/ltengine
COPY nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

# The model is downloaded into /models on first start, so keep that volume between runs.
# Mount a volume on /var/cache/nginx too if the cache should survive recreating the container.
ENV HF_HOME=/models \
    LTENGINE_MODEL=gemma3-4b

VOLUME ["/models"]
EXPOSE 5050
USER ltengine

# start-period has to cover downloading the model on first run - gemma3-12b is about 7 GB, and until
# it has loaded nginx answers 502, so a short grace period would mark a healthy container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=3600s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5050/languages -o /dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
