from __future__ import annotations

import synthetic

from valgebra_planning.contracts import Contract, read_contract, read_task
from valgebra_planning.planner import _reachable, plan
from valgebra_planning.policy import read_policy

TASK = read_task(
    "reach the end",
    {"start": synthetic.identifier("k0")},
    synthetic.identifier(f"k{synthetic.CHAIN}"),
)


def _library(size: int) -> dict[str, Contract]:
    return {c.name: c for c in map(read_contract, synthetic.library(size, 0))}


def test_a_tool_no_held_value_can_call_and_one_reaching_nothing_are_dropped() -> None:
    tools = _library(12)
    kept = _reachable(tools, TASK, frozenset())
    assert sorted(kept) == [f"step{i}" for i in range(synthetic.CHAIN)]
    assert any(name.startswith("stray") for name in tools)
    assert any(name.startswith("dead") for name in tools)


def test_a_tool_a_policy_reads_is_kept_when_it_can_be_called() -> None:
    tools = _library(12)
    dead = next(name for name in tools if name.startswith("dead"))
    stray = next(name for name in tools if name.startswith("stray"))
    kept = _reachable(tools, TASK, frozenset({dead, stray}))
    assert dead in kept
    assert stray not in kept


def test_the_cells_stay_fixed_as_distractors_are_added() -> None:
    sizes = {len(plan(_library(size), TASK).abstraction.cells) for size in (8, 32)}
    assert sizes == {12}


def test_a_policy_reading_a_dead_tool_still_plans() -> None:
    tools = _library(8)
    dead = next(name for name in tools if name.startswith("dead"))
    harmless = read_policy(
        {
            "name": "watch",
            "initial": "a",
            "bad": [],
            "transitions": [{"from": "a", "tool": dead, "to": "a"}],
        }
    )
    found = plan(tools, TASK, policies=[harmless])
    assert found.strong
