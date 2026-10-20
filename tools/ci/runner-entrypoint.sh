#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${RUNNER_NAME:?Set a distinct name for each CI slot}"
[[ "$RUNNER_NAME" =~ ^wqs-ci-[01]$ ]] || { echo "Invalid CI slot name" >&2; exit 1; }
cp -a --no-preserve=ownership /home/runner/. /runner/
cd /runner
if [[ -f .runner ]]; then
  # A slot must never reuse another runner's credential-bearing state.
  python3 -c 'import json,os,sys; sys.exit(0 if json.load(open(".runner"))["agentName"] == os.environ["RUNNER_NAME"] else "Runner state belongs to another slot")'
else
  : "${RUNNER_REPOSITORY:?}" "${RUNNER_REGISTRATION_TOKEN:?}"
  ./config.sh --unattended --url "$RUNNER_REPOSITORY" \
    --token "$RUNNER_REGISTRATION_TOKEN" --name "$RUNNER_NAME" \
    --labels wqs-ci-local --work /runner/_work --disableupdate
fi
unset RUNNER_REGISTRATION_TOKEN
exec ./run.sh
