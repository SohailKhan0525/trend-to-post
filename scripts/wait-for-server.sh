#!/usr/bin/env bash
set -euo pipefail
for attempt in {1..40}; do
  if python -m app server-status "$@" --release "${RELEASE:?missing release}"; then
    exit 0
  fi
  if (( attempt < 40 )); then sleep 15; fi
done
printf '%s\n' 'Azure did not report the expected worker status within ten minutes.' >&2
exit 1
