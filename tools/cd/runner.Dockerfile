FROM docker:cli@sha256:6fae2214c3121a28d5aa639d09032f61310b7c9c75ffbad1025f261a1b621800 AS docker
FROM registry.k8s.io/kubectl@sha256:6e4fce3c83651edb91b74bc67701c5cd263dd8aa3cd4254b1798d6425a5ab789 AS kubectl
FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM ghcr.io/actions/actions-runner@sha256:e5496277be5d09bc968b3d64911b74e219ac4a3f2edce956a3ecf9271bea1ef4
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3.12 python3.12-venv gh ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
RUN uv venv --python /usr/bin/python3.12 /opt/wqs-hooks && uv pip install --python /opt/wqs-hooks/bin/python numpy==2.5.4 jsonschema==4.26.0 cryptography==50.0.2 boto3==1.43.114 bech32==1.2.0
COPY tools/ci/install-age.sh /tmp/install-age.sh
RUN WQS_AGE_CACHE=/opt/wqs-age bash /tmp/install-age.sh && rm /tmp/install-age.sh
ENV PATH="/opt/wqs-hooks/bin:/opt/wqs-age/age:${PATH}"
COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
COPY --chmod=755 tools/cd/runner-entrypoint.sh /usr/local/bin/wqs-deployment-runner
USER 1001:1001
ENTRYPOINT ["/usr/local/bin/wqs-deployment-runner"]
