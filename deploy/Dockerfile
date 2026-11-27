FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM python:3.12-slim-bookworm AS app-base
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV USER=qsim LOGNAME=qsim PATH="/app/.venv/bin:$PATH" QSIM_DATA=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
FROM app-base AS api
COPY deploy/api/pyproject.toml deploy/api/uv.lock ./deploy/api/
RUN UV_PROJECT_ENVIRONMENT=/app/.venv uv sync --locked --no-dev --project deploy/api
COPY qsim ./qsim
COPY deploy/sky-config.yaml /app/deploy/sky-config.yaml
USER 1000:1000
CMD ["uvicorn", "qsim.api:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

FROM app-base AS controller
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client rsync && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-dev --extra controller
COPY qsim ./qsim
COPY deploy/sky-config.yaml /app/deploy/sky-config.yaml
ENV HOME=/data/controller-home
USER 1000:1000
CMD ["python", "-m", "qsim.controller"]

FROM python:3.12-bookworm@sha256:dbbe4ceb97851e2e5fa83798b239811f871cb743b259ba3563737349f6bcfaa0 AS josim-build
RUN printf 'cmake==3.28.3 --hash=sha256:d3bc42bf54ea3d64e5d81eb31275076817507cf4a6aa07a49ffc01985cae1f09\n' > /tmp/cmake.txt \
    && python -m pip install --no-cache-dir --no-deps --require-hashes --only-binary=:all: \
         --index-url https://pypi.org/simple -r /tmp/cmake.txt
COPY LICENSE /build/WQS-LICENSE
COPY deploy/physics/josim-build.sh vendor/josim/bounded-history.patch /build/
RUN bash /build/josim-build.sh /out
FROM josim-build AS physics-policy
COPY qsim/settings.py /build/physics-settings.py
RUN python3 -I -c "import json,runpy; p=runpy.run_path('/build/physics-settings.py')['_certified_physics_profiles']; open('/out/share/physics-policy.json','w').write(json.dumps(p,sort_keys=True))"

FROM python:3.12-bookworm@sha256:dbbe4ceb97851e2e5fa83798b239811f871cb743b259ba3563737349f6bcfaa0 AS worker-base
COPY --from=josim-build /out/ /opt/josim/
RUN useradd --uid 1000 --create-home --shell /bin/bash qsim
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server rsync sudo curl fuse && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/qsim
COPY pyproject.toml uv.lock ./
RUN UV_PROJECT_ENVIRONMENT=/opt/qsim-venv uv sync --frozen --no-dev --extra worker
COPY qsim/__init__.py qsim/worker.py qsim/qasm_adapter.py qsim/aer_adapter.py /opt/qsim/qsim/
COPY qsim/physics_results.py qsim/lindblad_models.py qsim/lindblad_model.py qsim/lindblad_adapter.py \
     qsim/scqubits_models.py qsim/scqubits_adapter.py qsim/josim_models.py \
     qsim/josim_validation.py qsim/josim_adapter.py /opt/qsim/qsim/
COPY --from=physics-policy /out/share/physics-policy.json /opt/josim/share/physics-policy.json
RUN /opt/qsim-venv/bin/python -I -c "import hashlib,json,pathlib; p=pathlib.Path('/opt/josim/share/physics-policy.json'); m=json.loads(p.read_text()); m['source_sha256']={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(pathlib.Path('/opt/qsim/qsim').glob('*.py'))}; p.write_text(json.dumps(m,sort_keys=True))"
COPY --chmod=755 deploy/worker/tail /usr/local/bin/tail
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
CMD ["sleep", "infinity"]

FROM nvidia/cuda:12.8.1-devel-ubuntu22.04@sha256:6617a625f4090c76c545a0e7d63f2e441718ef9af7f4efe7dd1242a29e289fd7 AS qsim-cuda-toolkit
FROM python:3.12-bookworm@sha256:dbbe4ceb97851e2e5fa83798b239811f871cb743b259ba3563737349f6bcfaa0 AS qsim-cuda-build
COPY --from=qsim-cuda-toolkit /usr/local/cuda-12.8 /usr/local/cuda-12.8
ENV PATH="/usr/local/cuda-12.8/bin:$PATH" CUDAARCHS=86 CMAKE_BUILD_PARALLEL_LEVEL=2 CPATH=/usr/local/include/python3.12
RUN apt-get update && apt-get install -y --no-install-recommends git g++ make && rm -rf /var/lib/apt/lists/*
COPY deploy/qsim-cuda/build.lock /build/requirements.lock
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /build/requirements.lock
RUN git init /build/qsim \
    && git -C /build/qsim fetch --depth=1 https://github.com/quantumlib/qsim.git 8d7150a7f796e8f7ebbf7587e67e80ed2674a5d0 \
    && git -C /build/qsim checkout --detach FETCH_HEAD \
    && test "$(git -C /build/qsim rev-parse HEAD)" = 8d7150a7f796e8f7ebbf7587e67e80ed2674a5d0
RUN printf 'find_package(pybind11 2.13.6 EXACT CONFIG REQUIRED)\n' > /build/qsim/pybind_interface/GetPybind11.cmake
RUN --network=none python -m pip wheel --no-build-isolation --no-deps --no-index -w /wheels /build/qsim \
    && python -m zipfile -e /wheels/qsimcirq-0.22.1-*.whl /tmp/qsim-wheel \
    && cuobjdump --list-elf /tmp/qsim-wheel/qsimcirq/qsim_cuda*.so | grep -q 'sm_86'

FROM worker-base AS worker-qsim-cuda
LABEL org.opencontainers.image.qsim-source=8d7150a7f796e8f7ebbf7587e67e80ed2674a5d0
COPY qsim/qsim_cuda_canary.py /opt/qsim/qsim/
COPY deploy/qsim-cuda/qualify.py /opt/qsim/
RUN --mount=type=bind,from=qsim-cuda-build,source=/wheels,target=/wheels,readonly \
    uv pip install --python /opt/qsim-venv/bin/python --no-index --no-deps --reinstall /wheels/qsimcirq-0.22.1-*.whl \
    && uv pip check --python /opt/qsim-venv/bin/python

FROM worker-base AS worker
