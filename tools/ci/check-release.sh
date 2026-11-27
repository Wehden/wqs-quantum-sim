#!/usr/bin/env bash
set -euo pipefail
sha="$(git rev-parse HEAD)"
mkdir -p build/release
for target in api controller; do
  image="wqs-release-ci:${target}-${sha}"
  docker build --target "$target" --label "org.opencontainers.image.revision=$sha" -t "$image" -f deploy/Dockerfile .
  docker run --rm --network none --tmpfs /data:uid=1000,gid=1000,mode=0700 --tmpfs /tmp --mount "type=bind,src=$PWD/tools/cd/image_smoke.py,dst=/check.py,readonly" --entrypoint python "$image" -c 'exec(open("/check.py").read())' "$target"
  docker run --rm --network none --entrypoint uv "$image" --no-cache pip check --python /app/.venv/bin/python
  python3 -m tools.cd.bundle "$target" "$image" "$sha"
  docker image rm "$image"
done
image="wqs-release-ci:worker-${sha}"
docker build --target worker --label "org.opencontainers.image.revision=$sha" -t "$image" -f deploy/Dockerfile .
bash tests/check_worker_image.sh "$image"
python3 -m tools.cd.bundle worker "$image" "$sha"
docker image rm "$image"

gpu_canary=$(python3 -m tools.cd.release gpu-plan --sha "$sha")
if [[ "$gpu_canary" == true ]]; then
  target=worker-qsim-cuda
  image="wqs-release-ci:${target}-${sha}"
  docker build --target "$target" --platform linux/amd64 --provenance=false \
    --label "org.opencontainers.image.revision=$sha" -t "$image" -f deploy/Dockerfile .
  docker run --rm --network none --user 1000:1000 "$image" \
    /opt/qsim-venv/bin/python /opt/qsim/qualify.py --check-package
  native_sha=$(docker run --rm --network none --user 1000:1000 "$image" \
    /opt/qsim-venv/bin/python -c 'import hashlib,qsimcirq; from pathlib import Path; print(hashlib.sha256(Path(qsimcirq.qsim_gpu.__file__).read_bytes()).hexdigest())')
  python3 -m tools.cd.bundle "$target" "$image" "$sha" "$native_sha"
  docker image rm "$image"
fi
