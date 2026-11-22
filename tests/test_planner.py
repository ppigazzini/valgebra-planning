from __future__ import annotations

import itertools
import random
from typing import Any

import pytest

from valgebra_planning import planner
from valgebra_planning._valgebra import Sampler
from valgebra_planning.cells import abstract
from valgebra_planning.contracts import Contract, read_contract, read_task
from valgebra_planning.flows import read_flow
from valgebra_planning.mining import mine
from valgebra_planning.planner import plan, predicates
from valgebra_planning.program import Branch, Return, render, steps
from valgebra_planning.verify import verify

ORDER_ID = {"type": "string", "pattern": "^ord_[0-9]+$"}
CLAIM = {"type": "string", "pattern": "^cl_[0-9]+$"}


def _settle(name: str, status: str) -> dict[str, object]:
    return {
        "name": name,
        "inputSchema": {
            "type": "object",
            "properties": {"order_id": ORDER_ID, "status": {"const": status}},
            "required": ["order_id", "status"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"claim_id": CLAIM},
            "required": ["claim_id"],
        },
    }


FIND: dict[str, object] = {
    "name": "find_order",
    "inputSchema": {
        "type": "object",
        "properties": {"order_id": ORDER_ID},
        "required": ["order_id"],
    },
    "outputSchema": {
        "type": "object",
        "properties": {
            "order": {
                "type": "object",
                "properties": {"status": {"enum": ["open", "shipped", "lost"]}},
                "required": ["status"],
            }
        },
        "required": ["order"],
    },
}
SETTLE = [_settle("void_order", "open"), _settle("refund", "shipped"), _settle("trace", "lost")]
TASK = read_task("settle an order", {"order_id": ORDER_ID}, CLAIM)


def _library(*specs: dict[str, object]) -> dict[str, Contract]:
    return {contract.name: contract for contract in map(read_contract, specs)}


def test_a_strong_program_branches_on_every_status_and_verifies() -> None:
    tools = _library(FIND, *SETTLE)
    found = plan(tools, TASK)
    assert found.strong
    assert found.program is not None
    assert found.uncovered == ()
    checked = verify(found.program, tools, TASK)
    assert checked.proven, render(found.program)
    assert checked.count("proven") == len(checked.links)
    text = render(found.program)
    assert text.count("if c1.order.status in ") == 2
    # The last status is settled by a direct call once the other two are tested away.
    assert steps(found.program) == 4


def test_a_weak_program_names_the_status_no_tool_settles() -> None:
    tools = _library(FIND, *SETTLE[:2])
    found = plan(tools, TASK)
    assert not found.strong
    assert found.program is not None
    assert [(gap.ref, gap.examples, gap.undecided) for gap in found.uncovered] == [
        ("c1.order.status", ("lost",), 0)
    ]
    checked = verify(found.program, tools, TASK)
    assert checked.count("refuted") == checked.count("undecided") == 0
    assert len(checked.gaps) == 1


def test_no_program_reaches_a_goal_no_tool_returns() -> None:
    tools = _library(FIND, *SETTLE)
    unreachable = read_task("nothing returns it", {"order_id": ORDER_ID}, {"const": "zz"})
    found = plan(tools, unreachable)
    assert found.program is None
    assert not found.strong


def test_a_value_already_held_inside_the_goal_is_returned_at_once() -> None:
    tools = _library(FIND)
    held = read_task("echo", {"order_id": ORDER_ID}, {"type": "string"})
    found = plan(tools, held)
    assert found.program is not None
    assert isinstance(found.program.end, Return)
    assert found.program.calls == ()


def test_the_depth_bounds_the_calls_and_tests_on_a_path() -> None:
    tools = _library(FIND, *SETTLE)
    assert plan(tools, TASK, depth=3, weak=False).program is None
    found = plan(tools, TASK, depth=4, weak=False)
    assert found.program is not None
    assert isinstance(found.program.end, Branch)


def _arrays(*keys: tuple[str, str]) -> dict[str, Any]:
    record = {
        "type": "object",
        "properties": {name: {"type": kind} for name, kind in keys},
        "required": [name for name, _ in keys],
    }
    return {"type": "array", "items": record}


def _passing(name: str, takes: dict[str, Any], gives: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "inputSchema": {"type": "object", "properties": {"x": takes}, "required": ["x"]},
        "outputSchema": {"type": "object", "properties": {"y": gives}, "required": ["y"]},
    }


#: Arrays of records beside arrays of strings, whose meets valgebra leaves partly undecided: cut
#: in their listing order, the 120 orders count 6 to 9 undecided cells, this one 8, its reverse 6.
STRINGS: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
ARRAYS: list[dict[str, Any]] = [
    _passing("t0", STRINGS | {"minItems": 1}, _arrays(("c", "string")) | {"minItems": 1}),
    _passing("t1", _arrays(("a", "string"), ("b", "integer")), _arrays(("b", "integer"))),
    _passing("t2", _arrays(("c", "string")) | {"minItems": 1}, _arrays(("a", "string"))),
    _passing("t3", STRINGS, STRINGS | {"minItems": 1}),
    _passing("t4", _arrays(("b", "integer")), STRINGS | {"minItems": 1}),
]


def test_the_cut_is_the_same_whatever_order_the_tools_are_listed_in() -> None:
    task = read_task("a string", {"text": {"type": "string"}}, {"type": "string"})
    cuts = set()
    for order in (ARRAYS, ARRAYS[::-1]):
        cut = abstract(predicates({spec["name"]: read_contract(spec) for spec in order}, task))
        names = [predicate.names for predicate in cut.predicates]
        cuts.add(
            frozenset(
                (cell.kind, frozenset(names[i] for i in cell.inside), cell.status)
                for cell in cut.cells
            )
        )
    assert len(cuts) == 1


def test_a_cell_is_asked_in_the_form_valgebra_decides() -> None:
    # Asked as a meet built up cut by cut, the cut of these keeps 22 cells, 8 undecided: valgebra
    # proves some meets empty only with the complement of a predicate the cell lies outside by a
    # relation, and some only without the complement of one a predicate inside it excludes.
    task = read_task("a string", {"text": {"type": "string"}}, {"type": "string"})
    found = predicates({spec["name"]: read_contract(spec) for spec in ARRAYS}, task)
    cut = abstract(found)
    assert len(cut.cells) <= 19
    assert sum(cell.status == "undecided" for cell in cut.cells) <= 5
    # A part dropped as empty holds no value: every value drawn lies in exactly one cell.
    rng = random.Random(0)
    drawn = [v for p in found for v in Sampler(p.root, rng).draw(p.schema, 20)]
    assert drawn
    assert all(sum(cell.values.is_valid(v) for cell in cut.cells) == 1 for v in drawn)


def test_a_cell_of_many_overlapping_ranges_is_decided() -> None:
    # Ranges sharing one value overlap, so no relation settles a cut. Asked only as its kind
    # with the complement of every range it lies outside, a cell is a meet too wide for valgebra
    # to decide, and the cut of these keeps 143 cells, 15 undecided; asked as the meet its cell
    # holds with the new range first, it keeps 135, each decided.
    rng = random.Random(2)

    def quota() -> dict[str, Any]:
        low, high = rng.randrange(10000, 15000), rng.randrange(15000, 20000)
        return {"type": "integer", "minimum": low, "maximum": high}

    specs = [_passing(f"t{i}", quota(), quota()) for i in range(32)]
    held = {"x": {"type": "integer", "minimum": 15000, "maximum": 15000}}
    task = read_task("a quota", held, {"type": "integer"})
    found = predicates({spec["name"]: read_contract(spec) for spec in specs}, task)
    cut = abstract(found)
    assert all(cell.status == "inhabited" for cell in cut.cells)
    # A part dropped as empty holds no value: every value drawn lies in exactly one cell.
    draw = random.Random(0)
    drawn = [v for p in found for v in Sampler(p.root, draw).draw(p.schema, 20)]
    assert drawn
    assert all(sum(cell.values.is_valid(v) for cell in cut.cells) == 1 for v in drawn)


def test_two_spellings_of_one_set_are_one_predicate() -> None:
    spelled = [
        _passing("u", {"type": "string", "description": "a code"}, {"type": "integer"}),
        _passing("v", {"type": "string", "title": "Code"}, {"type": "integer", "default": 0}),
    ]
    task = read_task("an integer", {"text": {"type": "string"}}, {"type": "integer"})
    found = predicates({spec["name"]: read_contract(spec) for spec in spelled}, task)
    by_name = {name: predicate for predicate in found for name in predicate.names}
    assert by_name["argument:u.x"] is by_name["argument:v.x"]
    assert by_name["result:u.y"] is by_name["result:v.y"]


#: Two tools returning one set, and a call whose argument a rule lets one of them fill.
TWINS = {
    "other": {"name": "other", "inputSchema": {"type": "object"}, "outputSchema": {}},
    "ask": {"name": "ask", "inputSchema": {"type": "object"}, "outputSchema": {}},
    "act": {
        "name": "act",
        "inputSchema": {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "pattern": "^id_"}},
            "required": ["id"],
        },
    },
}
TWIN_RESULT = {
    "type": "object",
    "properties": {"v": {"type": "string"}},
    "required": ["v"],
    "additionalProperties": False,
}
ACTED = read_task("act", {}, {"type": "string", "pattern": "^id_"})


def _twins(order: tuple[str, ...]) -> dict[str, Contract]:
    specs = {name: {**TWINS[name]} for name in order}
    for name in ("other", "ask"):
        specs[name]["outputSchema"] = TWIN_RESULT
    return {name: read_contract(spec) for name, spec in specs.items()}


@pytest.mark.parametrize("order", list(itertools.permutations(TWINS)))
def test_a_strong_program_a_flow_admits_is_found_at_its_depth_whatever_the_order(
    order: tuple[str, ...],
) -> None:
    only = read_flow({"name": "the ask's", "sources": ["ask"], "sink": "act.x", "only": True})
    found = plan(_twins(order), ACTED, depth=2, flows=(only,))
    assert found.strong
    assert found.program is not None
    assert render(found.program) == "c1 = ask()\nc2 = act(x=c1.v)\nreturn c2.id"


@pytest.mark.parametrize("order", list(itertools.permutations(TWINS)))
def test_a_strong_program_mined_types_admit_is_found_at_its_depth_whatever_the_order(
    order: tuple[str, ...],
) -> None:
    calls = [
        {"tool": "ask", "arguments": {}, "result": {"v": "k1"}},
        {"tool": "act", "arguments": {"x": "k1"}, "result": {"id": "id_1"}},
        {"tool": "other", "arguments": {}, "result": {"v": "z9"}},
    ]
    contracts = _twins(order)
    found = plan(contracts, ACTED, depth=2, types=mine(calls, contracts))
    assert found.strong


def test_a_plan_with_no_program_says_whether_the_bound_cut_a_path() -> None:
    tools = _twins(("other", "ask", "act"))
    shallow = plan(tools, ACTED, depth=1)
    assert shallow.program is None
    assert shallow.reason is not None
    assert shallow.reason.startswith("no program within the bound of 1 calls and tests, which cut")
    unreachable = plan(tools, read_task("never", {}, {"type": "string", "pattern": "^zz_"}))
    assert unreachable.program is None
    assert unreachable.reason == (
        "no program: no path reached the bound of 6, so no deeper search finds one from the "
        "first 16 bindings of each call the search tries"
    )
    assert plan(tools, ACTED).reason is None


def test_render_takes_a_program_and_refuses_none() -> None:
    with pytest.raises(TypeError, match="render takes a program, a Block, not NoneType"):
        render(None)  # ty: ignore[invalid-argument-type]


OBJECT = {"type": "string", "pattern": "^obj_[0-9]+$"}
GRASPED = {"type": "string", "pattern": "^grs_[0-9]+$"}


def _robot(pose: str = "estimate_pose") -> list[Contract]:
    """Return a pick's tools, the pose tool under ``pose``, as a listing in that order."""

    def listed(name: str, arguments: dict[str, Any], result: dict[str, Any]) -> Contract:
        return read_contract(
            {
                "name": name,
                "inputSchema": {
                    "type": "object",
                    "properties": arguments,
                    "required": [*arguments],
                },
                "outputSchema": {"type": "object", "properties": result, "required": [*result]},
                "annotations": {"readOnlyHint": True},
            }
        )

    return [
        listed(
            "detect_object",
            {"name": {"type": "string"}},
            {"object_id": OBJECT, "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
        ),
        listed(pose, {"object_id": OBJECT}, {"distance_m": {"type": "number", "minimum": 0}}),
        listed(
            "grasp",
            {"object_id": OBJECT, "distance_m": {"type": "number", "minimum": 0.2, "maximum": 0.6}},
            {"grasp_id": GRASPED},
        ),
        listed("say", {"text": {"type": "string"}}, {"spoken": {"type": "boolean"}}),
    ]


PICK = read_task("pick", {"name": {"type": "string"}}, GRASPED)


def test_the_program_is_the_same_whatever_order_the_tools_are_listed_in() -> None:
    programs = {
        render(found.program)
        for order in itertools.permutations(_robot())
        if (found := plan({tool.name: tool for tool in order}, PICK)).program is not None
    }
    assert len(programs) == 1


def test_the_weak_program_leaving_the_fewest_cells_uncovered_is_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Tried first by name, the pose tool's own weak program stops on more cells than the
    # detection's, which the ranking returns.
    tools = {tool.name: tool for tool in _robot("a_estimate_pose")}
    ranked = plan(tools, PICK)
    assert ranked.program is not None
    assert render(ranked.program).startswith("c1 = detect_object(name=name)")
    monkeypatch.setattr(planner, "RANKING", 0)
    first = plan(tools, PICK)
    assert first.program is not None
    assert render(first.program).startswith("if name in a_estimate_pose.object_id:")

    def cells(found: Any) -> int:
        return sum(len(gap.examples) + gap.undecided for gap in found.uncovered)

    assert (cells(ranked), cells(first)) == (2, 4)
