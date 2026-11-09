from __future__ import annotations

from valgebra_planning.contracts import Contract, read_contract, read_task
from valgebra_planning.planner import plan
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
