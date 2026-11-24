"""Hold the changelog to each public name changed since the last release tag.

griffe reads every module's public names at the last tag and in the tree, and lists each
removal and change a caller meets. The releasing page asks each to stand under Changed or
Removed among the changelog's entries since that tag, before any dispatch: this prints every
change, and exits 1 where one stands under neither. griffe is the dev group's, and the
`api` job of `ci.yml` runs the script as

    uv run --no-sync python tools/api_changes.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "valgebra_planning"

#: The headings of the changelog's subsections a change of a public name stands under.
RECORDING = ("Changed", "Removed")


def entries_since(changelog: str, version: str) -> str:
    """Return the Changed and Removed entries of every section above ``version``'s."""
    above = changelog.split(f"\n## [{version}]", 1)[0]
    kept: list[str] = []
    keep = False
    for line in above.splitlines():
        if line.startswith("## "):
            keep = False
        elif line.startswith("### "):
            keep = line.removeprefix("### ").strip() in RECORDING
        elif keep:
            kept.append(line)
    return "\n".join(kept)


def is_recorded(entries: str, name: str) -> bool:
    """Return whether ``entries`` name ``name`` in code, alone or as an attribute's last part."""
    return re.search(rf"`(?:[\w.]+\.)?{re.escape(name)}(?:\(\))?`", entries) is not None


def _names(breakage: Any) -> tuple[str, str]:
    """Return a change's object below its module, and the name the changelog writes for it.

    A parameter's change is named by the parameter, a removed or added one by the side that
    holds it; any other change by the object.
    """
    below = breakage.obj.path.removeprefix(f"{breakage.obj.module.path}.")
    if breakage.kind.name.startswith("PARAMETER_"):
        return below, (breakage.old_value or breakage.new_value).name
    return below, below


def main() -> int:
    """Print each public name changed since the last tag; return 1 where one is unrecorded."""
    import griffe  # noqa: PLC0415  # the dev group's; the suite imports this module without it

    tag = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", "--match", "v*"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="locale",
        check=True,
    ).stdout.strip()
    old = griffe.load_git(PACKAGE, ref=tag, repo=ROOT)
    new = griffe.load(PACKAGE, search_paths=[ROOT])
    entries = entries_since((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), tag[1:])
    missing = 0
    for breakage in griffe.find_breaking_changes(old, new):
        below, name = _names(breakage)
        recorded = is_recorded(entries, name)
        missing += not recorded
        level = "notice" if recorded else "error"
        where = "under Changed or Removed" if recorded else "under neither Changed nor Removed"
        print(f"::{level} file=CHANGELOG.md::{below}: {breakage.kind.value}, `{name}` {where}")
    print(f"{missing} change(s) since {tag} the changelog does not record")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
