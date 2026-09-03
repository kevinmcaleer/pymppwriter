#!/usr/bin/env bash
# Create the GitHub repo, push, create a GitHub Project (v2) and populate it with
# epics + features from scripts/roadmap.json.  Requires: gh (authenticated), jq, git.
#
#   ./scripts/bootstrap_github.sh kevinmcaleer pymppwriter
set -euo pipefail
OWNER=${1:?owner}; REPO=${2:?repo}
cd "$(dirname "$0")/.."

echo "== repo"
git init -q 2>/dev/null || true
git add -A && git commit -qm "Initial import: template-based MPP14 writer (spike)" || true
gh repo view "$OWNER/$REPO" >/dev/null 2>&1 || \
  gh repo create "$OWNER/$REPO" --public --source=. --remote=origin --description \
    "Write Microsoft Project .mpp files from pure Python" --push
git push -u origin HEAD 2>/dev/null || true

echo "== labels"
for l in "epic:6f42c1" "feature:0e8a16" "format:1d76db" "bug:d73a4a"; do
  gh label create "${l%%:*}" --color "${l##*:}" -R "$OWNER/$REPO" --force >/dev/null
done

echo "== project"
PROJ_NUM=$(gh project list --owner "$OWNER" --format json | jq -r '.projects[] | select(.title=="pymppwriter roadmap") | .number' | head -1)
if [ -z "$PROJ_NUM" ]; then
  PROJ_NUM=$(gh project create --owner "$OWNER" --title "pymppwriter roadmap" --format json | jq -r .number)
fi
echo "project #$PROJ_NUM"

echo "== issues"
jq -c '.epics[]' scripts/roadmap.json | while read -r epic; do
  title=$(jq -r .title <<<"$epic"); body=$(jq -r .body <<<"$epic")
  # features first so the epic body can link them
  links=""
  while read -r feat; do
    [ -z "$feat" ] && continue
    url=$(gh issue create -R "$OWNER/$REPO" --title "$feat" --label feature --body "Part of: $title" )
    gh project item-add "$PROJ_NUM" --owner "$OWNER" --url "$url" >/dev/null
    links+="- [ ] $url"$'\n'
  done < <(jq -r '.features[]' <<<"$epic")
  eurl=$(gh issue create -R "$OWNER/$REPO" --title "$title" --label epic --body "$body"$'\n\n## Features\n'"$links")
  gh project item-add "$PROJ_NUM" --owner "$OWNER" --url "$eurl" >/dev/null
  echo "  $eurl"
done
echo "done: https://github.com/$OWNER/$REPO"
