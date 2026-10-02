#!/usr/bin/env bash
# Writes `df -H` for the drive holding WORK_DIR into the "Runner disk usage"
# section of the tracking dataset's README, and commits it there. Inside a
# GitHub Actions step it also sets the outputs `percent` (used, as a bare
# number) and `summary` (one line for notifications).
#
#   update-disk-usage.sh TRACKING_DIR WORK_DIR
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 TRACKING_DIR WORK_DIR" >&2
  exit 2
fi
tracking="$1"
work="$2"

read -r source size used avail percent target < <(
  df -H --output=source,size,used,avail,pcent,target "$work" | tail -n 1
)
percent="${percent%\%}"
updated="$(date -u '+%Y-%m-%d %H:%M UTC')"

section="$(cat <<SECTION
## Runner disk usage

Updated by every ingest run, from \`df -H\` on the drive holding the runner's work directory (\`$work\`). Last updated $updated.

| Filesystem | Size | Used | Avail | Use% | Mounted on |
| --- | ---: | ---: | ---: | ---: | --- |
| \`$source\` | $size | $used | $avail | $percent% | \`$target\` |
SECTION
)"

SECTION="$section" python3 - "$tracking/README.md" <<'PY'
import os
import sys
from pathlib import Path

start, end = "<!-- runner-disk-usage:start -->", "<!-- runner-disk-usage:end -->"
readme = Path(sys.argv[1])
text = readme.read_text()
block = f"{start}\n{os.environ['SECTION']}\n{end}"
if start in text and end in text:
    before, rest = text.split(start, 1)
    after = rest.split(end, 1)[1]
    text = f"{before}{block}{after}"
else:
    text = f"{text.rstrip()}\n\n{block}\n"
readme.write_text(text)
PY

git -C "$tracking" add README.md
if ! git -C "$tracking" diff --cached --quiet -- README.md; then
  git -C "$tracking" commit -q -m "Updated runner disk usage ($percent% used)" -- README.md
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "percent=$percent"
    echo "summary=$used of $size used ($percent%), $avail free on $target"
  } >> "$GITHUB_OUTPUT"
fi
echo "Runner work drive: $used of $size used ($percent%), $avail free on $target"
