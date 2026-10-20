#!/usr/bin/env bash
set -euo pipefail
# Upstream v1.2.1 linux-amd64 release archive, pinned for repeatable encryption tests.
root="${WQS_AGE_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/wqs-age-1.2.1}"
mkdir -p "$root"
archive="$root/age-v1.2.1-linux-amd64.tar.gz"
expected=7df45a6cc87d4da11cc03a539a7470c15b1041ab2b396af088fe9990f7c79d50
if ! test -f "$archive" || ! echo "$expected  $archive" | sha256sum --check --status; then
  temporary=$(mktemp "$root/download.XXXXXX")
  trap 'rm -f "$temporary"' EXIT
  curl -fsSL --max-time 60 https://github.com/FiloSottile/age/releases/download/v1.2.1/age-v1.2.1-linux-amd64.tar.gz -o "$temporary"
  echo "$expected  $temporary" | sha256sum --check --status
  mv "$temporary" "$archive"
fi
# Re-extract the checked archive; never trust a previously cached executable.
tar -xzf "$archive" -C "$root" age/age age/age-keygen
printf '%s\n' "$root/age"
