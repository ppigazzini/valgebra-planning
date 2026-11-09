from __future__ import annotations

import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from valgebra_planning._valgebra import Sampler
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.planner import plan
from valgebra_planning.program import Block, Call, Ref, Return, render
from valgebra_planning.run import ToolError, run
from valgebra_planning.verify import verify

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
TOOLS = {
    contract.name: contract
    for contract in map(read_contract, json.loads((EXAMPLE / "tools.json").read_text())["tools"])
}
SETTLE = next(
    read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text())["tasks"]
    if spec["name"] == "settle a parcel"
)


def test_a_tool_is_idempotent_where_its_annotations_say_so() -> None:
    assert TOOLS["find_parcel"].idempotent
    assert TOOLS["open_claim"].idempotent
    assert not TOOLS["book_return"].idempotent
    assert not TOOLS["notify"].idempotent


def test_a_retry_is_accepted_only_for_an_idempotent_tool() -> None:
    lookup = Call("find_parcel", (("tracking", Ref("tracking")),), "c1", retry=True)
    program = Block((lookup,), Return(Ref("c1", ("parcel", "parcel_id"))))
    retried = next(link for link in verify(program, TOOLS, SETTLE).links if "retried" in link.claim)
    assert retried.verdict == "proven"
    booking = Call(
        "book_return",
        (
            ("parcel_id", Ref("c1", ("parcel", "parcel_id"))),
            ("state", Ref("c1", ("parcel", "state"))),
            ("weight_g", Ref("c1", ("parcel", "weight_g"))),
        ),
        "c2",
        retry=True,
    )
    program = Block((lookup, booking), Return(Ref("c2", ("label_id",))))
    claims = {link.claim: link.verdict for link in verify(program, TOOLS, SETTLE).links}
    assert claims["book_return may be retried"] == "refuted"


def test_an_error_nothing_handles_is_a_gap_when_tools_may_fail() -> None:
    lookup = Call("find_parcel", (("tracking", Ref("tracking")),), "c1")
    program = Block((lookup,), Return(Ref("c1", ("parcel", "parcel_id"))))
    assert verify(program, TOOLS, SETTLE).gaps == ()
    gaps = verify(program, TOOLS, SETTLE, errors=True).gaps
    assert [(gap.where, gap.ref) for gap in gaps] == [("", "an error of find_parcel")]


def test_when_tools_may_fail_the_plan_retries_or_stops_and_reads_back() -> None:
    assert plan(TOOLS, SETTLE).strong
    found = plan(TOOLS, SETTLE, errors=True)
    assert not found.strong
    assert found.program is not None
    text = render(found.program)
    assert text.count("retry(") == 4
    assert text.count("except ToolError:") == 2
    assert render(read_program(text)) == text
    checked = verify(found.program, TOOLS, SETTLE, errors=True)
    assert checked.count("proven") == len(checked.links)
    assert sorted(gap.where for gap in checked.gaps) == [
        "then.else.error:book_freight_return",
        "then.then.error:book_return",
    ]
    assert sorted(gap.where for gap in found.uncovered) == sorted(gap.where for gap in checked.gaps)


def test_a_run_against_failing_tools_breaks_no_contract() -> None:
    found = plan(TOOLS, SETTLE, errors=True)
    assert found.program is not None
    rng = random.Random(3)

    def failing(name: str) -> Callable[..., Any]:
        contract = TOOLS[name]

        def tool(**_: Any) -> Any:
            if rng.random() < 0.3:
                raise ToolError(name)
            whole = contract.result()
            return rng.choice(Sampler(whole.root, rng).draw(whole.schema, 8))

        return tool

    tools = {name: failing(name) for name in TOOLS if TOOLS[name].returns is not None}
    tracking = SETTLE.held["tracking"][0]
    outcomes = set()
    for _ in range(60):
        inputs = {"tracking": rng.choice(Sampler(tracking.root, rng).draw(tracking.schema, 4))}
        result = run(found.program, TOOLS, SETTLE, inputs, tools)
        assert result.broken == ()
        outcomes.add("stopped" if result.stopped else "returned")
    assert outcomes == {"stopped", "returned"}
