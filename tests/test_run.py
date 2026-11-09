from __future__ import annotations

import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from valgebra_planning._valgebra import Sampler
from valgebra_planning.contracts import Contract, Task, read_contract, read_task
from valgebra_planning.planner import plan
from valgebra_planning.program import Block, Call, Ref, Return
from valgebra_planning.run import run
from valgebra_planning.verify import verify

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
TOOLS = {
    contract.name: contract
    for contract in map(read_contract, json.loads((EXAMPLE / "tools.json").read_text())["tools"])
}
TASKS = {
    spec["name"]: read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text())["tasks"]
}
RUNS = 40


def _random_tools(rng: random.Random) -> dict[str, Callable[..., Any]]:
    """Return tools answering with values drawn from their own output schemas."""

    def answering(contract: Contract) -> Callable[..., Any]:
        def tool(**_: Any) -> Any:
            if contract.returns is None:
                return None
            whole = contract.result()
            return rng.choice(Sampler(whole.root, rng).draw(whole.schema, 8))

        return tool

    return {name: answering(contract) for name, contract in TOOLS.items()}


def _inputs(task: Task, rng: random.Random) -> dict[str, Any]:
    drawn = {}
    for var, slots in task.held.items():
        whole = next(slot for slot in slots if slot.path == ())
        drawn[var] = rng.choice(Sampler(whole.root, rng).draw(whole.schema, 8))
    return drawn


def test_a_strong_program_breaks_no_contract_whatever_the_tools_return() -> None:
    task = TASKS["settle a parcel"]
    found = plan(TOOLS, task)
    assert found.strong
    assert found.program is not None
    rng = random.Random(0)
    for _ in range(RUNS):
        result = run(found.program, TOOLS, task, _inputs(task, rng), _random_tools(rng))
        assert result.broken == ()
        assert result.stopped is None
        assert task.goal.is_valid(result.value)


def test_a_weak_program_returns_or_stops_on_a_value_it_names() -> None:
    task = TASKS["return a parcel"]
    found = plan(TOOLS, task)
    assert found.program is not None
    named = {value for gap in found.uncovered for value in gap.examples}
    rng = random.Random(1)
    for _ in range(RUNS):
        result = run(found.program, TOOLS, task, _inputs(task, rng), _random_tools(rng))
        assert result.broken == ()
        if result.stopped is None:
            assert task.goal.is_valid(result.value)
        else:
            assert result.value in named


def test_the_naive_chain_is_refuted_and_breaks_a_contract_when_run() -> None:
    task = TASKS["return a parcel"]
    naive = Block(
        (
            Call("find_parcel", (("tracking", Ref("tracking")),), "c1"),
            Call(
                "book_return",
                (
                    ("parcel_id", Ref("c1", ("parcel", "parcel_id"))),
                    ("state", Ref("c1", ("parcel", "state"))),
                    ("weight_g", Ref("c1", ("parcel", "weight_g"))),
                ),
                "c2",
            ),
        ),
        Return(Ref("c2", ("label_id",))),
    )
    refuted = {link.claim for link in verify(naive, TOOLS, task).links if link.verdict == "refuted"}
    assert refuted == {
        "c1.parcel.state <= book_return.state",
        "c1.parcel.weight_g <= book_return.weight_g",
    }
    rng = random.Random(2)
    broken = [
        run(naive, TOOLS, task, _inputs(task, rng), _random_tools(rng)).broken for _ in range(RUNS)
    ]
    assert any(broken)
