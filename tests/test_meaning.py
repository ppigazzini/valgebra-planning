"""Each contract wider than its keywords suggest is found, and its narrowed control is not."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.library import Library, Problem, read_library
from valgebra_planning.meaning import findings

OBJECT_ID = {"type": "string", "pattern": "^obj_[0-9]+$"}
GRASP_ID = {"type": "string", "pattern": "^grs_[0-9]+$"}


def _tool(
    name: str, arguments: dict[str, Any], result: dict[str, Any], hints: dict[str, Any] | None
) -> dict[str, Any]:
    listed: dict[str, Any] = {
        "name": name,
        "inputSchema": {"type": "object", "properties": arguments, "required": [*arguments]},
        "outputSchema": {"type": "object", "properties": result, "required": [*result]},
    }
    if hints is not None:
        listed["annotations"] = hints
    return listed


def _found(listing: list[dict[str, Any]], *, assert_format: bool = False) -> set[str]:
    tools = {
        contract.name: contract
        for contract in (read_contract(tool, assert_format=assert_format) for tool in listing)
    }
    task = read_task("pick", {"name": {"type": "string"}}, GRASP_ID)
    return {f"{f.where}: {f.finding}" for f in findings(Library(tools, (Problem(task),), {}))}


READ_ONLY = {"readOnlyHint": True}
DETECT = _tool("detect", {"name": {"type": "string"}}, {"object_id": OBJECT_ID}, READ_ONLY)
GRASP = _tool("grasp", {"object_id": OBJECT_ID}, {"grasp_id": GRASP_ID}, None)


def test_the_parcel_library_holds_no_contract_wider_than_its_keywords() -> None:
    assert findings(read_library(Path(__file__).parent.parent / "examples" / "parcels")) == []


@pytest.mark.parametrize(
    ("argument", "found"),
    [
        ({"type": "string", "format": "uuid"}, True),
        ({"type": "string", "format": "uuid", "pattern": "^[0-9a-f-]{36}$"}, False),
        ({"type": "string"}, False),
    ],
    ids=["format-alone", "with-a-pattern", "no-format"],
)
def test_an_argument_a_format_alone_types_takes_every_string(
    argument: dict[str, Any], *, found: bool
) -> None:
    listing = [DETECT, _tool("track", {"object_id": argument}, {"ok": {"type": "boolean"}}, None)]
    finding = (
        "tool track: track.object_id takes every string: its `format` is read as an annotation"
    )
    assert (finding in _found(listing)) is found
    assert finding not in _found(listing, assert_format=True)


@pytest.mark.parametrize(
    ("argument", "sides"),
    [
        ({"type": "number"}, ["lower", "upper"]),
        ({"type": "number", "minimum": 0}, ["upper"]),
        ({"type": "integer", "maximum": 9}, ["lower"]),
        ({"type": "number", "minimum": 0.2, "maximum": 0.6}, []),
    ],
    ids=["neither", "upper", "lower", "both-bound"],
)
def test_a_numeric_argument_s_open_side_is_found(
    argument: dict[str, Any], sides: list[str]
) -> None:
    listing = [DETECT, _tool("move", {"meters": argument}, {"ok": {"type": "boolean"}}, None)]
    open_sides = {
        side
        for side in ("lower", "upper")
        if f"tool move: move.meters has no {side} bound" in _found(listing)
    }
    assert open_sides == set(sides)


def test_a_const_with_no_type_beside_it_is_found_where_it_is() -> None:
    bare = _tool("say", {"mode": {"const": "loud"}, "text": {"type": "string"}}, {}, None)
    typed = _tool("say", {"mode": {"type": "string", "const": "loud"}}, {}, None)
    assert "tool say: the input schema's `const` at /properties/mode has no `type` beside it" in (
        _found([DETECT, bare])
    )
    assert not any("`const`" in finding for finding in _found([DETECT, typed]))


def test_a_destructive_tool_whose_results_fill_only_a_gate_is_found() -> None:
    gated = _tool(
        "grasp",
        {"object_id": OBJECT_ID, "confirmed": {"type": "boolean", "const": True}},
        {"grasp_id": GRASP_ID},
        None,
    )
    walk = _tool("walk_to", {"object_id": OBJECT_ID}, {"arrived": {"type": "boolean"}}, None)
    finding = (
        "tool walk_to: it is destructive, and its results meet only arguments taking one value or"
        " a boolean, and no goal: a plan calls it only to fill a gate"
    )
    assert finding in _found([DETECT, gated, walk])
    # Read-only, it moves nothing; returning an id an argument takes, it serves more than a gate.
    assert finding not in _found([DETECT, gated, {**walk, "annotations": READ_ONLY}])
    located = _tool("walk_to", {"object_id": OBJECT_ID}, {"at": OBJECT_ID}, None)
    assert not any("walk_to: it is destructive" in f for f in _found([DETECT, gated, located]))
    # The grasp returns the goal itself, so it is no gate's source.
    assert not any("grasp: it is destructive" in f for f in _found([DETECT, gated, walk]))


UNIT = {"const": "m"}
DETECTED = {
    "object_id": OBJECT_ID,
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
}
PICKED = {
    "object_id": OBJECT_ID,
    "distance": {
        "type": "object",
        "properties": {"unit": UNIT, "value": {"type": "number", "minimum": 0.2, "maximum": 0.6}},
        "required": ["unit", "value"],
    },
}


@pytest.mark.parametrize(
    ("output", "closing", "found"),
    [
        ({}, False, True),
        ({"additionalProperties": False}, False, False),
        ({}, True, False),
        ({"properties": {**DETECTED, "unit": UNIT, "value": {"type": "number"}}}, False, False),
    ],
    ids=["open", "declared-closed", "read-closed", "declaring-the-keys"],
)
def test_a_result_meeting_an_argument_through_undeclared_keys_alone_is_found(
    output: dict[str, Any], *, closing: bool, found: bool
) -> None:
    detect = _tool("detect", {"name": {"type": "string"}}, DETECTED, READ_ONLY)
    detect["outputSchema"] |= output
    listing = [detect, _tool("grasp", PICKED, {"grasp_id": GRASP_ID}, None)]
    tools = {
        contract.name: contract
        for contract in (read_contract(tool, closed_results=closing) for tool in listing)
    }
    task = read_task("pick", {"name": {"type": "string"}}, GRASP_ID)
    noted = {f"{f.where}: {f.finding}" for f in findings(Library(tools, (Problem(task),), {}))}
    finding = (
        "tool detect: its result meets grasp.distance only through keys its output schema does not"
        " declare"
    )
    assert (finding in noted) is found
