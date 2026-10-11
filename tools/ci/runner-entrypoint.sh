#!/usr/bin/env bash
set -euo pipefail
umask 077
cp -a --no-preserve=ownership /home/runner/. /runner/
cd /runner
if [[ ! -f .runner ]]; then
  : "${RUNNER_REPOSITORY:?}" "${RUNNER_REGISTRATION_TOKEN:?}"
  ./config.sh --unattended --url "$RUNNER_REPOSITORY" \
    --token "$RUNNER_REGISTRATION_TOKEN" --name wqs-local \
    --labels wqs-local --work /runner/_work --disableupdate
fi
unset RUNNER_REGISTRATION_TOKEN
exec ./run.sh
