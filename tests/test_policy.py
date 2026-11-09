from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from valgebra_planning._valgebra import Sampler
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.planner import plan
from valgebra_planning.policy import Monitor, read_policy
from valgebra_planning.program import render
from valgebra_planning.run import run
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
FREIGHT = read_policy(json.loads((EXAMPLE / "policies.json").read_text())["policies"][0])


def _policy_links(program: Any, policy: Any = FREIGHT) -> dict[str, str]:
    checked = verify(program, TOOLS, SETTLE, policies=[policy])
    return {link.where: link.verdict for link in checked.links if link.claim.startswith("policy")}


def test_the_monitor_reads_a_trace_call_by_call() -> None:
    approved = Monitor(FREIGHT)
    assert not approved.observe("approve_freight", {"parcel_id": "pcl_1"})
    assert not approved.observe("book_freight_return", {"parcel_id": "pcl_1"})
    unapproved = Monitor(FREIGHT)
    assert not unapproved.observe("find_parcel", {"tracking": "x"})
    assert unapproved.observe("book_freight_return", {"parcel_id": "pcl_1"})
    assert unapproved.violations == ["freight approved first: book_freight_return reached violated"]


def test_a_program_planned_without_the_policy_is_refuted_on_its_freight_path_alone() -> None:
    found = plan(TOOLS, SETTLE)
    assert found.program is not None
    verdicts = _policy_links(found.program)
    assert verdicts.pop("then.else") == "refuted"
    assert set(verdicts.values()) == {"proven"}


def test_a_program_planned_under_the_policy_approves_first_and_is_proven() -> None:
    found = plan(TOOLS, SETTLE, policies=[FREIGHT])
    assert found.strong
    assert found.program is not None
    text = render(found.program)
    assert text.index("approve_freight(") < text.index("book_freight_return(")
    assert text.count("approve_freight(") == 1
    assert set(_policy_links(found.program).values()) == {"proven"}


def test_a_guard_the_arguments_only_overlap_leaves_the_policy_undecided() -> None:
    heavy = read_policy(
        {
            "name": "no freight over 25 kg",
            "initial": "ok",
            "bad": ["violated"],
            "transitions": [
                {
                    "from": "ok",
                    "tool": "book_freight_return",
                    "arguments": {"properties": {"weight_g": {"minimum": 25001}}},
                    "to": "violated",
                }
            ],
        }
    )
    found = plan(TOOLS, SETTLE)
    assert found.program is not None
    verdicts = _policy_links(found.program, heavy)
    assert verdicts.pop("then.else") == "undecided"
    assert set(verdicts.values()) == {"proven"}
    assert plan(TOOLS, SETTLE, policies=[heavy], weak=False).program is None


def test_a_run_under_the_monitor_reports_what_the_static_check_refutes() -> None:
    plain = plan(TOOLS, SETTLE).program
    policed = plan(TOOLS, SETTLE, policies=[FREIGHT]).program
    assert plain is not None
    assert policed is not None
    rng = random.Random(4)

    def answer(name: str) -> Any:
        whole = TOOLS[name].result()
        return lambda **_: rng.choice(Sampler(whole.root, rng).draw(whole.schema, 8))

    tools = {name: answer(name) for name in TOOLS if TOOLS[name].returns is not None}
    tracking = SETTLE.held["tracking"][0]
    plain_broken, policed_broken = [], []
    for _ in range(80):
        inputs = {"tracking": rng.choice(Sampler(tracking.root, rng).draw(tracking.schema, 4))}
        plain_broken += run(plain, TOOLS, SETTLE, inputs, tools, policies=[FREIGHT]).broken
        policed_broken += run(policed, TOOLS, SETTLE, inputs, tools, policies=[FREIGHT]).broken
    assert plain_broken
    assert set(plain_broken) == {"freight approved first: book_freight_return reached violated"}
    assert policed_broken == []


def test_an_enforced_run_never_makes_the_call_a_policy_forbids() -> None:
    plain = plan(TOOLS, SETTLE).program
    assert plain is not None
    rng = random.Random(4)
    made: list[str] = []

    def answer(name: str) -> Any:
        whole = TOOLS[name].result()

        def tool(**_: Any) -> Any:
            made.append(name)
            return rng.choice(Sampler(whole.root, rng).draw(whole.schema, 8))

        return tool

    tools = {name: answer(name) for name in TOOLS if TOOLS[name].returns is not None}
    tracking = SETTLE.held["tracking"][0]
    refused = set()
    for _ in range(80):
        inputs = {"tracking": rng.choice(Sampler(tracking.root, rng).draw(tracking.schema, 4))}
        refused.add(run(plain, TOOLS, SETTLE, inputs, tools, policies=[FREIGHT]).refused)
    assert refused == {None, "freight approved first: book_freight_return reached violated"}
    assert "book_freight_return" not in made
