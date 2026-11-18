"""The Python on the pages runs as written, and prints what the page says it prints."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = ("docs/02-api.md", "docs/03-code-mode.md", "docs/19-recipes.md")
#: A Python block, and the text block that follows it, if one does, holding what it prints.
BLOCK = re.compile(r"```python\n(.*?)```(?:\s*```text\n(.*?)```)?", re.DOTALL)


def _examples() -> list[object]:
    examples = []
    for page in PAGES:
        text = (ROOT / page).read_text(encoding="utf-8")
        for number, found in enumerate(BLOCK.finditer(text), 1):
            examples.append(pytest.param(found.group(1), found.group(2), id=f"{page}#{number}"))
    return examples


@pytest.mark.parametrize(("source", "printed"), _examples())
def test_a_page_example_runs_as_written(
    source: str,
    printed: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    # Run from the repository root, as each page says, wherever the suite runs from.
    monkeypatch.chdir(ROOT)
    exec(compile(source, "<page>", "exec"), {"__name__": "__page__"})  # noqa: S102
    if printed is not None:
        assert capfd.readouterr().out == printed
