# Disposable discovery only. Override only with an immutable approved base.
ARG DISCOVERY_BASE=python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
FROM ${DISCOVERY_BASE} AS numerical
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && if ! command -v uv; then python3.12 -m pip install --no-cache-dir uv==0.11.6; fi
RUN uv venv --python "$(command -v python3.12)" /opt/discovery \
 && uv pip install --python /opt/discovery/bin/python \
    numpy==2.5.4 scipy==1.18.1 qutip==5.3.1 scqubits==4.3.1 \
    'cupy-cuda12x[ctk]==14.2.0' cuda-pathfinder==1.8.4 \
    cuda-toolkit==12.9.2.0 nvidia-cublas-cu12==12.9.2.10 \
    nvidia-cuda-nvrtc-cu12==12.9.86 nvidia-cuda-runtime-cu12==12.9.79 \
    nvidia-cufft-cu12==11.4.1.4 nvidia-curand-cu12==10.3.10.19 \
    nvidia-cusolver-cu12==11.7.5.82 nvidia-cusparse-cu12==12.5.10.65 \
    nvidia-nvjitlink-cu12==12.9.86 \
    'git+https://github.com/qutip/qutip-cupy.git@bd471ca0581fcd9a84e613775092bca418d0b66a' \
 && uv pip check --python /opt/discovery/bin/python \
 && uv pip freeze --python /opt/discovery/bin/python > /opt/discovery-packages.txt
COPY spec/physics-engines/probe_gpu.py /opt/probe_gpu.py
RUN if ! getent passwd 1001; then useradd --uid 1001 --user-group --create-home --home-dir /home/discovery discovery; fi
ENV PATH=/opt/discovery/bin:/usr/local/bin:/usr/bin:/bin \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    CUPY_CACHE_DIR=/tmp/cupy-cache
USER 1001:1001
ENTRYPOINT ["/opt/discovery/bin/python", "/opt/probe_gpu.py"]

# SkyPilot schedules this command-line probe; no alternate provider transport.
FROM numerical AS probe
