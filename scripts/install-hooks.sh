#!/bin/bash
# install-hooks.sh — installs this repo's git hooks into the local .git/hooks/.
#
# Hooks live (committed, reviewable) under scripts/hooks/ and are copied into the
# local, un-tracked .git/hooks/ directory. Git does not version .git/hooks/, so
# every fresh clone needs to run this once to enable the hooks.
#
#   Usage:  scripts/install-hooks.sh
#   Safe to re-run any time (overwrites the installed copies).

set -e
cd "$(dirname "$0")/.."
repo_root="$(git rev-parse --show-toplevel)"
src="$repo_root/scripts/hooks"
dst="$repo_root/.git/hooks"

if [ ! -d "$src" ]; then
  echo "✗ no scripts/hooks/ directory found — nothing to install"; exit 1
fi
mkdir -p "$dst"

n=0
for hook in "$src"/*; do
  [ -f "$hook" ] || continue
  name="$(basename "$hook")"
  cp "$hook" "$dst/$name"
  chmod +x "$dst/$name"
  echo "  ✓ installed $name"
  n=$((n + 1))
done

if [ "$n" -eq 0 ]; then
  echo "✗ scripts/hooks/ is empty — nothing installed"; exit 1
fi
echo "✓ $n hook(s) installed into .git/hooks/ — pre-push audit is now active."
