from __future__ import annotations

import random
from typing import Any

from valgebra_planning._valgebra import Sampler
from valgebra_planning.cells import abstract
from valgebra_planning.contracts import Contract, read_contract, read_task
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
