#!/usr/bin/env bash
set -euo pipefail
sha="$(git rev-parse HEAD)"
mkdir -p build/release
for target in api controller; do
  image="wqs-release-ci:${target}-${sha}"
  docker build --target "$target" --label "org.opencontainers.image.revision=$sha" -t "$image" -f deploy/Dockerfile .
  docker run --rm --network none --tmpfs /data:uid=1000,gid=1000,mode=0700 --tmpfs /tmp --mount "type=bind,src=$PWD/tools/cd/image_smoke.py,dst=/check.py,readonly" --entrypoint python "$image" -c 'exec(open("/check.py").read())' "$target"
  docker run --rm --network none --entrypoint uv "$image" --no-cache pip check --python /app/.venv/bin/python
  python3 tools/cd/bundle.py "$target" "$image" "$sha"
  docker image rm "$image"
done
image="wqs-release-ci:worker-${sha}"
docker build --target worker --label "org.opencontainers.image.revision=$sha" -t "$image" -f deploy/Dockerfile .
bash tests/check_worker_image.sh "$image"
python3 tools/cd/bundle.py worker "$image" "$sha"
docker image rm "$image"
