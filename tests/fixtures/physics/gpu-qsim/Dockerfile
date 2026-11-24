# Discovery image only. Supply a pinned base that already contains Python 3.12.
ARG DISCOVERY_BASE
FROM nvidia/cuda:12.8.1-devel-ubuntu22.04@sha256:6617a625f4090c76c545a0e7d63f2e441718ef9af7f4efe7dd1242a29e289fd7 AS cuda
FROM ${DISCOVERY_BASE} AS build
USER root
COPY --from=cuda /usr/local/cuda-12.8 /usr/local/cuda-12.8
ENV PATH=/usr/local/cuda-12.8/bin:/opt/discovery/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin CUDAARCHS=70;80;86;89
RUN apt-get update && apt-get install -y --no-install-recommends git cmake g++ make unzip && rm -rf /var/lib/apt/lists/*
RUN git init /tmp/qsim && git -C /tmp/qsim fetch --depth=1 https://github.com/quantumlib/qsim.git 8d7150a7f796e8f7ebbf7587e67e80ed2674a5d0 \
 && git -C /tmp/qsim checkout --detach FETCH_HEAD \
 && test "$(git -C /tmp/qsim rev-parse HEAD)" = 8d7150a7f796e8f7ebbf7587e67e80ed2674a5d0 \
 && /opt/discovery/bin/python -m ensurepip \
 && /opt/discovery/bin/python -m pip install cmake==4.2.3 'pybind11[global]==3.0.2' setuptools==80.9.0 wheel==0.46.3 \
 && /opt/discovery/bin/python -m pip wheel --no-build-isolation --no-deps -w /wheels /tmp/qsim \
 && test -n "$(unzip -Z1 /wheels/qsimcirq-*.whl | awk '/qsim_cuda.*\.so$/')"

FROM build AS custatevec
RUN /opt/discovery/bin/python -m pip install --no-deps custatevec-cu12==1.15.1 \
 && root=/opt/discovery/lib/python3.12/site-packages/cuquantum \
 && test -f "$root/include/custatevec.h" \
 && ln -sf "$(basename "$(find "$root/lib" -name 'libcustatevec.so.*' -type f | head -1)")" "$root/lib/libcustatevec.so" \
 && rm -rf /tmp/qsim/build /wheels/*.whl \
 && CUQUANTUM_ROOT="$root" LIBRARY_PATH=/usr/local/cuda-12.8/lib64 /opt/discovery/bin/python -m pip wheel --no-build-isolation --no-deps -w /wheels /tmp/qsim \
 && test -n "$(unzip -Z1 /wheels/qsimcirq-*.whl | awk '/qsim_custatevec.*\.so$/')"

FROM ${DISCOVERY_BASE} AS discovery
USER root
ENTRYPOINT []
CMD ["sleep", "infinity"]
COPY --from=custatevec /wheels/ /wheels/
COPY --from=custatevec /opt/discovery/lib/python3.12/site-packages/cuquantum/lib/ /opt/cuquantum/lib/
RUN apt-get update \
 && apt-get install -y --no-install-recommends sudo openssh-server netcat-openbsd pciutils fuse3 rsync wget curl gcc patch \
 && rm -rf /var/lib/apt/lists/* \
 && uv pip install --python /opt/discovery/bin/python 'cirq-core==1.7.0' 'ply==3.11' 'contourpy==1.3.2' /wheels/qsimcirq-*.whl \
 && uv pip check --python /opt/discovery/bin/python \
 && uv pip freeze --python /opt/discovery/bin/python > /opt/wqs-discovery-packages.txt
ENV PYTHONPATH=/opt/wqs-discovery \
    LD_LIBRARY_PATH=/opt/cuquantum/lib:/opt/discovery/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/discovery/lib/python3.12/site-packages/nvidia/cuda_runtime/lib:/usr/local/nvidia/lib64
COPY qsim/qasm_adapter.py /opt/wqs-discovery/qsim/qasm_adapter.py
# The only production adapter change in this disposable copy is its qubit guard.
RUN test "$(grep -Ec '(quantum > 26|quantum <= 26)' /opt/wqs-discovery/qsim/qasm_adapter.py)" = 2 \
 && sed -i -e 's/quantum > 26/quantum > 30/' -e 's/quantum <= 26/quantum <= 30/' /opt/wqs-discovery/qsim/qasm_adapter.py
COPY tests/fixtures/physics/gpu-qsim/probe.py tests/fixtures/physics/gpu-qsim/cases.json /opt/wqs-discovery/qsim-probe/
COPY tests/fixtures/physics/gpu-scqubits/probe.py tests/fixtures/physics/gpu-scqubits/cases.json /opt/wqs-discovery/scqubits-probe/
