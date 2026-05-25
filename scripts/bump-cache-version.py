#!/usr/bin/env python3
"""Bump sw.js CACHE_VERSION using the lunapp-vN convention.

Single source of truth for the increment + re-anchor logic. Called by
the GitHub Actions workflow (.github/workflows/scrape.yml). The local
pre-commit hook (hooks/pre-commit) inlines a similar increment-only
variant but does NOT re-anchor — the bot owns chain establishment.

Behavior:
  - Current value matches `<prefix>-v<digits>` → increment N by 1.
  - Current value doesn't match (e.g. a previous bot run wrote a SHA
    before this script existed) → re-anchor to `lunapp-v1` so the
    chain restarts cleanly from here.
  - sw.js missing or CACHE_VERSION line not found → silent no-op
    (exit 0).

Resolves the repo root via `git rev-parse` so the script works
whether called from the repo root, the workflow, or anywhere else.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys


def main() -> int:
    try:
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True
        ).strip()
    except subprocess.CalledProcessError:
        return 0

    sw_path = os.path.join(root, "sw.js")
    if not os.path.isfile(sw_path):
        return 0

    with open(sw_path, encoding="utf-8") as f:
        content = f.read()

    m = re.search(
        r'^const CACHE_VERSION = "([^"]+)";', content, re.MULTILINE
    )
    if not m:
        return 0

    current = m.group(1)
    pattern = re.match(r'^([A-Za-z][A-Za-z0-9_-]*-v)(\d+)$', current)
    if pattern:
        new_val = f"{pattern.group(1)}{int(pattern.group(2)) + 1}"
    else:
        # Previous value doesn't match the convention — re-anchor.
        new_val = "lunapp-v1"

    new_content = content[: m.start(1)] + new_val + content[m.end(1):]
    with open(sw_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"CACHE_VERSION: {current} -> {new_val}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
