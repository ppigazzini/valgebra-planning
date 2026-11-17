from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from valgebra_planning._valgebra import Sampler
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.planner import plan
from valgebra_planning.policy import Monitor, read_policy
from valgebra_planning.program import render
from valgebra_planning.run import blame, run
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


def test_a_rule_about_one_parcel_reads_as_a_rule_about_any_two_calls() -> None:
    # A policy carries no value from one call to another: an approval of one parcel lets the
    # booking of another through, where a parametric monitor would keep one instance a parcel.
    monitor = Monitor(FREIGHT)
    assert not monitor.observe("approve_freight", {"parcel_id": "pcl_1"})
    assert not monitor.observe("book_freight_return", {"parcel_id": "pcl_2"})
    assert monitor.violations == []


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


#: Every booking is followed by a notice: a liveness rule, decided where each path ends.
NOTIFIED = read_policy(
    {
        "name": "returns are notified",
        "initial": "clear",
        "bad": [],
        "pending": ["owed"],
        "transitions": [
            {"from": "clear", "tool": "book_return", "to": "owed"},
            {"from": "clear", "tool": "book_freight_return", "to": "owed"},
            {"from": "owed", "tool": "notify", "to": "clear"},
        ],
    }
)
PARCEL = {
    "parcel_id": "pcl_1",
    "state": "delivered",
    "weight_g": 900,
    "country": "IT",
    "contact": "ann@shop.example",
}
TOOLS_ANSWERING = {
    "find_parcel": lambda **_: {"parcel": PARCEL},
    "book_return": lambda **_: {"label_id": "lbl_1"},
    "notify": lambda **_: {"notice_id": "ntc_1"},
}


def test_a_bad_state_is_never_left() -> None:
    left = read_policy(
        {
            "name": "contacts are never verified",
            "initial": "ok",
            "bad": ["verified"],
            "transitions": [
                {"from": "ok", "tool": "verify_contact", "to": "verified"},
                {"from": "verified", "tool": "notify", "to": "ok"},
            ],
        }
    )
    task = next(
        read_task(spec["name"], spec["inputs"], spec["goal"])
        for spec in json.loads((EXAMPLE / "tasks.json").read_text())["tasks"]
        if spec["name"] == "notify a parcel's contact"
    )
    program = read_program(
        "c1 = find_parcel(tracking=tracking)\n"
        "c2 = verify_contact(email=c1.parcel.contact)\n"
        "c3 = notify(email=c2.email)\n"
        "return c3.notice_id\n"
    )
    checked = verify(program, TOOLS, task, policies=[left])
    assert [link.verdict for link in checked.links if link.claim.startswith("policy")] == [
        "refuted"
    ]
    monitor = Monitor(left)
    assert monitor.observe("verify_contact", {"email": "ann@shop.example"})
    assert not monitor.observe("notify", {"email": "ann@shop.example"})
    assert monitor.state == "verified"
    assert monitor.violations == ["contacts are never verified: verify_contact reached verified"]


def test_a_path_ending_with_an_obligation_open_is_refuted() -> None:
    plain = plan(TOOLS, SETTLE).program
    assert plain is not None
    verdicts = _policy_links(plain, NOTIFIED)
    assert verdicts.pop("then.then") == "refuted"
    assert verdicts.pop("then.else") == "refuted"
    assert set(verdicts.values()) == {"proven"}


def test_the_planner_settles_every_obligation_and_pads_no_path() -> None:
    found = plan(TOOLS, SETTLE, policies=[NOTIFIED, FREIGHT])
    assert found.strong
    assert found.program is not None
    text = render(found.program)
    assert text.count("notify(") == 2
    assert text.count("approve_freight(") == 1
    checked = verify(found.program, TOOLS, SETTLE, policies=[NOTIFIED, FREIGHT])
    assert checked.count("proven") == len(checked.links)


def test_a_run_ending_with_an_obligation_open_breaks() -> None:
    plain = plan(TOOLS, SETTLE).program
    notified = plan(TOOLS, SETTLE, policies=[NOTIFIED]).program
    assert plain is not None
    assert notified is not None
    inputs = {"tracking": "AB123456789IT"}
    ended = run(plain, TOOLS, SETTLE, inputs, TOOLS_ANSWERING, policies=[NOTIFIED])
    assert ended.refused == "returns are notified: the run ended in owed"
    assert ended.value is None
    assert blame(ended, TOOLS, proven=False) == "a policy no link of the program proves broke"
    settled = run(notified, TOOLS, SETTLE, inputs, TOOLS_ANSWERING, policies=[NOTIFIED])
    assert (settled.value, settled.broken) == ("lbl_1", ())
