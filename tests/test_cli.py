from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from valgebra_planning.cli import main

PARCELS = str(Path(__file__).parent.parent / "examples" / "parcels")
NAIVE = """\
c1 = find_parcel(tracking=tracking)
c2 = book_return(parcel_id=c1.parcel.parcel_id, state=c1.parcel.state, weight_g=c1.parcel.weight_g)
return c2.label_id
"""


def test_plan_prints_every_task_and_its_links(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", PARCELS, "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 8
    assert all(row["program"] is not None for row in rows)
    assert all(link["verdict"] == "proven" for row in rows for link in row["links"])
    weak = next(row for row in rows if row["task"] == "return a parcel")
    assert weak["strong"] is False
    assert weak["uncovered"][0]["examples"] == ["in_transit", "lost", "returned"]


def test_plan_reads_one_task_by_name_as_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", PARCELS, "--task", "customs for a parcel"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("## customs for a parcel\n")
    assert "weak; 3 of 3 links proven" in out


def test_an_unknown_task_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", PARCELS, "--task", "no such task"]) == 2
    assert "no task named 'no such task'" in capsys.readouterr().err


def test_critique_refuses_the_naive_chain_and_accepts_the_planned_program(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    naive = tmp_path / "naive.py"
    naive.write_text(NAIVE, encoding="utf-8")
    assert main(["critique", PARCELS, "--task", "return a parcel", str(naive)]) == 1
    out = capsys.readouterr().out
    assert "refuted: c1.parcel.state <= book_return.state" in out
    assert main(["plan", PARCELS, "--task", "notify a parcel's contact", "--json"]) == 0
    planned = tmp_path / "planned.py"
    planned.write_text(json.loads(capsys.readouterr().out)[0]["program"], encoding="utf-8")
    assert main(["critique", PARCELS, "--task", "notify a parcel's contact", str(planned)]) == 0
    assert capsys.readouterr().out.endswith("accepted\n")


def test_a_program_the_reader_refuses_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("import os\n", encoding="utf-8")
    assert main(["critique", PARCELS, "--task", "return a parcel", str(bad)]) == 2
    assert str(bad) in capsys.readouterr().err


SERVER = f"{sys.executable} {Path(__file__).parent / 'parcel_server.py'}"


def _command(*argv: str) -> subprocess.CompletedProcess[str]:
    """Run the command line in a process of its own, as a user does, its server a child of it."""
    entry = "from valgebra_planning.cli import main; raise SystemExit(main())"
    return subprocess.run(  # noqa: S603 - this interpreter, this package
        [sys.executable, "-c", entry, *argv], capture_output=True, text=True, check=False
    )


def test_run_plans_and_runs_a_task_against_a_live_server() -> None:
    inputs = '{"tracking": "AB123456789IT"}'
    argv = ["run", PARCELS, "--task", "settle a parcel", "--inputs", inputs, "--server", SERVER]
    ran = _command(*argv, "--json")
    assert ran.returncode == 0, ran.stderr
    row = json.loads(ran.stdout)
    assert row["strong"] is True
    assert row["refused"] is None
    assert row["broken"] == []
    assert row["value"].startswith("lbl_")


def test_plan_reads_the_tools_of_a_live_server(capsys: pytest.CaptureFixture[str]) -> None:
    live = _command("plan", PARCELS, "--task", "return a parcel", "--server", SERVER, "--json")
    assert live.returncode == 0, live.stderr
    assert main(["plan", PARCELS, "--task", "return a parcel", "--json"]) == 0
    assert json.loads(live.stdout) == json.loads(capsys.readouterr().out)


def test_check_finds_no_vacuous_specification_in_the_parcels(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["check", PARCELS]) == 0
    assert capsys.readouterr().out == "no specification holds vacuously\n"


def test_privileges_exits_1_on_an_expansion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text(json.dumps({"send": [[1, 0, {"to": {"const": "ann"}}, 0]]}), encoding="utf-8")
    new.write_text(json.dumps({"send": [[1, 0, {}, 0]]}), encoding="utf-8")
    assert main(["privileges", str(new), str(old)]) == 0
    assert capsys.readouterr().out == "send: narrowing\n"
    assert main(["privileges", str(old), str(new)]) == 1
    assert capsys.readouterr().out.startswith("send: expansion, allowing ")
