"""The cases `tools/perf.py` counts run, and the performance page names code and tests that are."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
import perf

ROOT = Path(__file__).parent.parent
PAGE = (ROOT / "docs" / "performance.md").read_text(encoding="utf-8")
#: A private name, or a class's, as the page writes one.
NAMED = re.compile(r"`(_[a-zA-Z_]+(?:\.[a-z_]+)?|[A-Z][A-Za-z]+\.[a-z_]+)`")
#: What cachegrind prints of a run.
PRINTED = "==7== Cachegrind\n==7== \n==7== I refs:        1,234,567\n"


def test_a_count_is_read_off_what_cachegrind_prints() -> None:
    assert perf.instructions(PRINTED) == 1234567
    assert perf.instructions("==7== Process terminating with SIGILL\n") is None


@pytest.mark.parametrize("case", perf.CASES)
def test_each_case_runs(case: str) -> None:
    """A case of a task no program meets, or of no example, would count nothing."""
    source = perf.program(case)
    assert source is not None
    assert perf.workload(case, source)() is not None


def test_the_edited_programs_are_the_same_each_time_and_most_are_refuted() -> None:
    source = perf.program("mistakes:parcels:1") or ""
    assert perf.edited(source, 60) == perf.edited(source, 60)
    verified = perf.workload("mistakes:parcels:1", source)()
    assert isinstance(verified, list)
    refuted = [one for one in verified if one.count("refuted")]
    assert len(verified) > perf.MISTAKES // 2
    assert len(refuted) > len(verified) // 2


def test_a_call_is_the_difference_of_two_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perf, "executed", lambda _python, _case, calls, _source: 900 + 7 * calls)
    here = Path()
    assert perf.counted(here, "verify:parcels:0", here, first=False) == 7
    assert perf.counted(here, "plan:parcels:0", here, first=True) == 7
    monkeypatch.setattr(perf, "executed", lambda *_: None)
    assert perf.counted(here, "verify:parcels:0", here, first=False) is None


def test_a_row_reads_each_environment_against_the_first() -> None:
    assert perf.row("verify:parcels:0", [200, 50, None]).split() == [
        "verify:parcels:0",
        "200",
        "0.25",
        "no",
        "count",
    ]


def test_each_case_named_is_counted_and_printed_in_turn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def executed(python: Path, case: str, calls: int, source: Path) -> int | None:
        assert source.read_text(encoding="utf-8") == perf.program(case)
        if case == "stubs:bookings:0":
            return None
        return 100 * calls if tmp_path in python.parents else 50 * calls

    monkeypatch.setattr(perf.shutil, "which", lambda _: "valgrind")
    monkeypatch.setattr(perf, "executed", executed)
    cases = ["verify:parcels:1", "render:bookings:0"]
    assert perf.main(["--against", str(tmp_path), *cases]) == 0
    head, *lines = capsys.readouterr().out.splitlines()
    assert head.split() == ["case", tmp_path.name, "the", "tree"]
    assert [line.split() for line in lines] == [[case, "100", "0.50"] for case in cases]
    assert perf.main(["--jobs", "1", "stubs:bookings:0", "verify:parcels:1"]) == 2
    assert "stubs:bookings:0 could not be counted" in capsys.readouterr().out


def test_without_valgrind_no_count_is_taken(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(perf.shutil, "which", lambda _: None)
    assert perf.main([]) == 2
    assert "valgrind is not on PATH" in capsys.readouterr().out


def _sources(folder: str) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((ROOT / folder).glob("*.py"))
    )


def test_every_test_the_performance_page_names_is_one() -> None:
    named = set(re.findall(r"`(test_\w+)`", PAGE))
    assert named
    assert named <= set(re.findall(r"^(?:async )?def (test_\w+)", _sources("tests"), re.MULTILINE))


def test_every_name_the_performance_page_gives_is_defined() -> None:
    source = _sources("valgebra_planning")
    named = set(NAMED.findall(PAGE))
    assert {"_Walk.holds", "Slot.below", "_PAIRS"} <= named
    for name in named:
        owner, _, last = name.rpartition(".")
        assert not owner or re.search(rf"^class {owner}\b", source, re.MULTILINE), name
        defined = rf"^\s*(?:(?:async )?def|class) {last}\b|^\s*{last}\b\s*(?::|=)"
        assert re.search(defined, source, re.MULTILINE), name
