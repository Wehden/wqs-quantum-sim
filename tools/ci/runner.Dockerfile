FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM node:22-bookworm-slim AS node
FROM ghcr.io/actions/actions-runner@sha256:e5496277be5d09bc968b3d64911b74e219ac4a3f2edce956a3ecf9271bea1ef4
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3.12-venv libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --chmod=755 tools/ci/runner-entrypoint.sh /usr/local/bin/wqs-runner
USER 1001:1001
ENTRYPOINT ["/usr/local/bin/wqs-runner"]
