#!/usr/bin/env bash
set -euo pipefail
if test -z "$(git status --porcelain -- state/post_queue.json)"; then exit 0; fi
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git add state/post_queue.json
git commit -m 'chore: persist AI quote queue state'
# Never publish after an unsuccessful reservation push. Never rebase conflicting state.
for attempt in 1 2 3; do
  if git push origin HEAD:main; then exit 0; fi
  git fetch origin main
  if ! git rebase origin/main; then git rebase --abort; exit 1; fi
  sleep "$attempt"
done
exit 1
