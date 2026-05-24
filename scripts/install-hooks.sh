#!/bin/sh
# One-time setup: point git at the versioned hooks/ directory.
#
# `.git/hooks/` is local to a clone and never versioned, so a shared
# hook needs to live somewhere git tracks (`hooks/` at the repo root)
# and be wired up via `git config core.hooksPath`. This script does
# the wiring and prints a confirmation.
#
# Run once per clone:
#
#     ./scripts/install-hooks.sh
#
# Idempotent — re-running just resets the config to the same value.

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

git config core.hooksPath hooks

# Belt-and-braces: ensure the executable bit is set locally. Git tracks
# it in the index, but a fresh checkout on Windows / a filesystem that
# strips modes may need this.
chmod +x hooks/* 2>/dev/null || true

echo "Hooks installed: core.hooksPath -> hooks/"
echo "Active hooks:"
ls -1 hooks/ | sed 's/^/  - /'
