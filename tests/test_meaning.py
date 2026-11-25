"""Each contract wider than its keywords suggest is found, and its narrowed control is not."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.library import Library, Problem, read_library
from valgebra_planning.meaning import findings
from valgebra_planning.policy import read_policy

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


def test_a_result_any_string_fills_is_found() -> None:
    noted = _tool("note", {"object_id": OBJECT_ID}, {"text": {"type": "string"}}, READ_ONLY)
    finding = "tool note: note.text may return any string: an agent reads it unbounded"
    assert finding in _found([DETECT, noted])
    worded = _tool("note", {"object_id": OBJECT_ID}, {"text": {"enum": ["near", "far"]}}, READ_ONLY)
    assert not any("may return any string" in f for f in _found([DETECT, worded]))


_S = {"type": "string"}


def _noted(output: dict[str, Any]) -> list[str]:
    tool = read_contract(
        {
            "name": "read",
            "inputSchema": {"type": "object", "properties": {}},
            "outputSchema": output,
        }
    )
    return [
        f.finding.removesuffix(" may return any string: an agent reads it unbounded")
        for f in findings(Library({"read": tool}, ()))
        if "any string" in f.finding
    ]


def _down(depth: int, leaf: dict[str, Any]) -> dict[str, Any]:
    for name in reversed("wxyz"[:depth]):
        leaf = {"type": "object", "properties": {name: leaf}, "required": [name]}
    return leaf


@pytest.mark.parametrize(
    ("output", "noted"),
    [
        ({"type": "object", "properties": {"a": _S}}, ["read.a"]),
        (
            _down(4, {"type": "object", "properties": {"a": _S}, "required": ["a"]}),
            ["read.w.x.y.z.a"],
        ),
        ({"type": "object", "properties": {"a": {"type": "array", "items": _S}}}, ["read.a.[]"]),
        (
            {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"_id": _S, "action": _S}},
                    }
                },
            },
            ["read.actions.[]._id", "read.actions.[].action"],
        ),
        ({"type": "object", "additionalProperties": _S}, ["read.*"]),
        ({"type": "object", "patternProperties": {"^x": _S}}, ["read.*"]),
        ({"type": "object", "properties": {"a": {**_S, "maxLength": 64}}}, []),
        (
            {
                "type": "object",
                "allOf": [{"properties": {"a": _S}}, {"properties": {"a": {"maxLength": 5}}}],
            },
            [],
        ),
        (
            {
                "type": "object",
                "anyOf": [{"properties": {"a": {"const": "x"}}}, {"properties": {"a": _S}}],
            },
            ["read.a"],
        ),
        (
            {
                "$defs": {
                    "node": {
                        "type": "object",
                        "properties": {"label": _S, "next": {"$ref": "#/$defs/node"}},
                    }
                },
                "$ref": "#/$defs/node",
            },
            ["read.label"],
        ),
    ],
    ids=[
        "optional",
        "four-objects-down",
        "an-array",
        "item-fields",
        "additional",
        "patterned",
        "bounded",
        "all-of-bounds-it",
        "any-of-frees-it",
        "recursive-ref-read-once",
    ],
)
def test_the_string_note_reads_every_place_a_string_may_sit(
    output: dict[str, Any], noted: list[str]
) -> None:
    """A checked result carries to an agent every string its schema admits, wherever it sits.

    Every place the output schema puts a value is read, required or not, at any depth, in an
    item or under a key no property names; an ``allOf`` branch bounds a place the others name,
    an ``anyOf`` branch frees it, and a reference is followed once.
    """
    assert _noted(output) == noted


@pytest.mark.parametrize(
    ("after", "pending", "noted"),
    [
        ("free", ["holding"], True),
        ("late", ["holding", "late"], False),
        ("free", ["late"], False),
    ],
)
def test_a_lapse_that_ends_an_obligation_is_noted(
    after: str, pending: list[str], *, noted: bool
) -> None:
    policy = read_policy(
        {
            "name": "release what you grasp",
            "initial": "free",
            "bad": [],
            "pending": pending,
            "transitions": [
                {"from": "free", "tool": "grasp", "to": "holding"},
                {"from": "holding", "tool": "release", "to": "free"},
                {"from": "holding", "after_s": 30, "to": after},
            ],
        }
    )
    found = findings(Library({}, (), {policy.name: policy}))
    said = "the lapse out of the pending holding into free ends an obligation after 30 s"
    assert [f.finding for f in found] == [f"{said}, with no call that meets it"] * noted


def test_the_example_libraries_hold_no_lapse_that_ends_an_obligation() -> None:
    for name in ("parcels", "robot-skills"):
        library = read_library(Path(__file__).parent.parent / "examples" / name)
        assert not [f for f in findings(library) if "ends an obligation" in f.finding]
