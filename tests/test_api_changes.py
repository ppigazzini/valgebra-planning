from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
import api_changes

CHANGELOG = """# Changelog

## [Unreleased]

### Fixed

- `Plan.steps` reads the steps in order.

## [0.0.5] - 2026-11-21

### Changed

- `Contract.arguments` is a tuple.

### Removed

- `Contract.optional`. Without it, `inferred` is one position earlier.

## [0.0.4] - 2026-11-19

### Removed

- `Contract.hints`.
"""


def test_the_entries_since_a_tag_are_changed_and_removed_above_its_section() -> None:
    entries = api_changes.entries_since(CHANGELOG, "0.0.4")
    assert "`Contract.arguments`" in entries
    assert "`Contract.optional`" in entries
    # Fixed records no change of a name, and the tag's own section is the release before.
    assert "`Plan.steps`" not in entries
    assert "`Contract.hints`" not in entries


def test_a_name_is_recorded_alone_or_as_an_attribute_s_last_part() -> None:
    entries = api_changes.entries_since(CHANGELOG, "0.0.4")
    assert api_changes.is_recorded(entries, "Contract.optional")
    assert api_changes.is_recorded(entries, "optional")
    assert api_changes.is_recorded(entries, "inferred")
    # A name inside another or beginning one, or one in prose and not in code, is no record.
    assert not api_changes.is_recorded(entries, "ferred")
    assert not api_changes.is_recorded(entries, "Contract.opt")
    assert not api_changes.is_recorded(entries, "position")
    assert not api_changes.is_recorded(entries, "hints")
