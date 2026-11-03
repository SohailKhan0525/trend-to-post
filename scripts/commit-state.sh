#!/usr/bin/env bash
set -euo pipefail
state_paths=(state/dash_rattle.json)
if test -f state/DRAFTS.md; then state_paths+=(state/DRAFTS.md); fi
if [[ ${1:-} == linkedin && ${2:-} =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  state_paths=("linkedin/$2.txt" linkedin/latest.txt)
fi
if test -z "$(git status --porcelain -- "${state_paths[@]}")"; then exit 0; fi
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git add -- "${state_paths[@]}"
git commit -m 'chore: persist original post state' -- "${state_paths[@]}"
for attempt in 1 2 3; do
  if git push origin HEAD:main; then exit 0; fi
  git fetch origin main
  if ! git rebase origin/main; then git rebase --abort; exit 1; fi
  sleep "$attempt"
done
exit 1
