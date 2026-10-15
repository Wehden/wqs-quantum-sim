FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM python:3.12-slim-bookworm AS api
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY qsim ./qsim
COPY deploy/sky-config.yaml /app/deploy/sky-config.yaml
ENV USER=qsim LOGNAME=qsim PATH="/app/.venv/bin:$PATH" QSIM_DATA=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
CMD ["uvicorn", "qsim.api:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

FROM api AS controller
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client rsync && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-dev --extra controller
ENV HOME=/data/controller-home
USER 1000:1000
CMD ["python", "-m", "qsim.controller"]

FROM python:3.12-bookworm AS worker
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server rsync sudo curl fuse && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/qsim
COPY pyproject.toml uv.lock ./
RUN UV_PROJECT_ENVIRONMENT=/opt/qsim-venv uv sync --frozen --no-dev --extra worker
COPY qsim/__init__.py qsim/worker.py qsim/qasm_adapter.py qsim/aer_adapter.py /opt/qsim/qsim/
COPY --chmod=755 deploy/worker/tail /usr/local/bin/tail
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
CMD ["sleep", "infinity"]
