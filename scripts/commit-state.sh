#!/usr/bin/env bash
set -euo pipefail
state_path="${1:-state/resources_queue.json}"
case "$state_path" in
  state/resources_queue.json|state/profile_applied.json) ;;
  *) exit 1 ;;
esac
if test -z "$(git status --porcelain -- "$state_path")"; then exit 0; fi
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git add "$state_path"
git commit -m 'chore: persist verified resource automation state'
for attempt in 1 2 3; do
  if git push origin HEAD:main; then exit 0; fi
  git fetch origin main
  if ! git rebase origin/main; then git rebase --abort; exit 1; fi
  sleep "$attempt"
done
exit 1
