from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from valgebra_planning.critic import critique
from valgebra_planning.library import read_library
from valgebra_planning.mining import shape
from valgebra_planning.planner import plan
from valgebra_planning.program import render
from valgebra_planning.run import run
from valgebra_planning.verify import verify

REF = {"type": "string", "pattern": "^r_[0-9]+$"}
TOOLS = [
    {
        "name": "lookup",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "book",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
        "outputSchema": {"type": "object", "properties": {"ref": REF}, "required": ["ref"]},
    },
]
OBSERVED = [
    {"tool": "lookup", "arguments": {"name": "ann"}, "result": {"id": 1, "tier": "gold"}},
    {"tool": "lookup", "arguments": {"name": "bob"}, "result": {"id": 2}},
]


def _library(tmp_path: Path, *, observed: bool) -> Path:
    task = {"name": "book", "inputs": {"name": {"type": "string"}}, "goal": REF}
    if observed:
        task["types"] = "observed.json"
        (tmp_path / "observed.json").write_text(json.dumps({"calls": OBSERVED}), encoding="utf-8")
    (tmp_path / "tools.json").write_text(json.dumps({"tools": TOOLS}), encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
    return tmp_path


def test_a_shape_is_each_kind_seen_and_the_keys_every_object_holds() -> None:
    assert shape([{"id": "a", "n": 1}, {"id": "b", "n": 2.5}]) == {
        "type": "object",
        "properties": {"id": {"type": "string"}, "n": {"type": "number"}},
        "required": ["id", "n"],
    }
    assert shape([[1], [], None]) == {
        "anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "null"}]
    }


def test_an_inferred_output_feeds_a_program_and_every_binding_names_it(tmp_path: Path) -> None:
    library = read_library(_library(tmp_path, observed=True))
    assert library.tools["lookup"].inferred
    problem = library.problems[0]
    found = plan(library.tools, problem.task, types=problem.types)
    assert found.program is not None
    assert render(found.program) == ("c1 = lookup(name=name)\nc2 = book(id=c1.id)\nreturn c2.ref")
    assert "c1.id -> book.id, from the output of lookup as observed" in found.assumed
    links = {link.claim: link for link in verify(found.program, library.tools, problem.task).links}
    assert links["c1.id <= book.id"].verdict == "proven"
    assert links["c1.id <= book.id"].assumed == ("the output of lookup as observed",)
    notes = critique(render(found.program), library.tools, problem.task).notes
    assert notes == ("assumed: the output of lookup as observed",)


def test_without_observed_calls_the_bare_output_feeds_nothing(tmp_path: Path) -> None:
    library = read_library(_library(tmp_path, observed=False))
    assert not library.tools["lookup"].inferred
    assert plan(library.tools, library.problems[0].task).program is None


def test_a_result_outside_the_inferred_schema_is_refused_naming_the_assumption(
    tmp_path: Path,
) -> None:
    library = read_library(_library(tmp_path, observed=True))
    problem = library.problems[0]
    found = plan(library.tools, problem.task, types=problem.types)
    assert found.program is not None
    tools: dict[str, Any] = {
        "lookup": lambda **_: {"name": "no id"},
        "book": lambda **_: {"ref": "r_1"},
    }
    result = run(found.program, library.tools, problem.task, {"name": "ann"}, tools)
    assert result.refused is not None
    assert "outside the output schema inferred from observed calls" in result.refused
