FROM docker:cli@sha256:6fae2214c3121a28d5aa639d09032f61310b7c9c75ffbad1025f261a1b621800 AS docker
FROM registry.k8s.io/kubectl@sha256:6e4fce3c83651edb91b74bc67701c5cd263dd8aa3cd4254b1798d6425a5ab789 AS kubectl
FROM ghcr.io/actions/actions-runner@sha256:e5496277be5d09bc968b3d64911b74e219ac4a3f2edce956a3ecf9271bea1ef4
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3.12 gh ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
COPY --chmod=755 tools/cd/runner-entrypoint.sh /usr/local/bin/wqs-deployment-runner
USER 1001:1001
ENTRYPOINT ["/usr/local/bin/wqs-deployment-runner"]
