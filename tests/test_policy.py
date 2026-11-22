from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from valgebra_planning._valgebra import Sampler
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.planner import plan
from valgebra_planning.policy import Monitor, read_policy
from valgebra_planning.program import render
from valgebra_planning.run import ToolError, blame, run
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


def test_a_call_that_answered_with_an_error_holds_the_policy_in_either_state() -> None:
    monitor = Monitor(FREIGHT)
    assert not monitor.observe("approve_freight", {"parcel_id": "pcl_1"})
    monitor.errored()
    assert monitor.held() == {"start", "approved"}
    assert monitor.observe("book_freight_return", {"parcel_id": "pcl_1"})
    assert monitor.violations == ["freight approved first: book_freight_return reached violated"]
    again = Monitor(FREIGHT)
    assert not again.observe("approve_freight", {"parcel_id": "pcl_1"})
    again.errored()
    assert not again.observe("approve_freight", {"parcel_id": "pcl_1"})
    assert again.held() == {"approved"}
    assert not again.observe("book_freight_return", {"parcel_id": "pcl_1"})


BOOK = (
    "book_freight_return(parcel_id=c1.parcel.parcel_id, state=c1.parcel.state, "
    "weight_g=c1.parcel.weight_g)"
)
ERRORED_APPROVAL = f"""\
c1 = find_parcel(tracking=tracking)
try:
    c2 = approve_freight(parcel_id=c1.parcel.parcel_id)
except ToolError:
    c3 = {BOOK}
    return c3.label_id
c4 = {BOOK}
return c4.label_id
"""


def test_a_run_refuses_the_call_an_approval_that_answered_with_an_error_would_allow() -> None:
    program = read_program(ERRORED_APPROVAL)
    verdicts = _policy_links(program)
    assert verdicts.pop("error:approve_freight") == "undecided"
    made: list[str] = []

    def unanswered(**_: Any) -> Any:
        made.append("approve_freight")
        raise ToolError

    def book(**_: Any) -> Any:
        made.append("book_freight_return")
        return {"label_id": "lbl_1"}

    tools = {
        "find_parcel": lambda **_: {"parcel": {**PARCEL, "weight_g": 25000}},
        "approve_freight": unanswered,
        "book_freight_return": book,
    }
    ended = run(program, TOOLS, SETTLE, {"tracking": "AB123456789IT"}, tools, policies=[FREIGHT])
    assert ended.refused == "freight approved first: book_freight_return reached violated"
    assert made == ["approve_freight"]


def _transitions(*transitions: dict[str, Any]) -> dict[str, Any]:
    return {"name": "approved", "initial": "start", "bad": ["violated"], "transitions": transitions}


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        (
            _transitions({"from": "start", "tool": "book", "argument": {}, "to": "violated"}),
            "'argument' is no key it reads",
        ),
        ({**_transitions(), "pending_states": ["owed"]}, "'pending_states' is no key it reads"),
        (
            _transitions({"from": "start", "tool": "book", "hint": "destructiveHint", "to": "ok"}),
            "names a tool or a hint, one of the two",
        ),
        (_transitions({"from": "start", "to": "ok"}), "names a tool or a hint, one of the two"),
    ],
    ids=["argument", "pending_states", "tool-and-hint", "neither"],
)
def test_a_policy_means_what_its_keys_say_or_is_refused(spec: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        read_policy(spec)


#: Robot skills: a policy names a confirmation and two motions, and the task needs one motion.
OBJECT_ID = {"type": "string", "pattern": "^obj_[0-9]+$"}
DISTANCE = {"unit": {"const": "m"}, "value": {"type": "number", "minimum": 0, "maximum": 20}}
SKILLS = {
    "detect_object": (
        {"name": {"type": "string"}},
        {
            "status": {"enum": ["found", "not_found"]},
            "object_id": OBJECT_ID,
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        {"readOnlyHint": True},
    ),
    "estimate_pose": (
        {"object_id": OBJECT_ID},
        {
            "pose_status": {"enum": ["ok", "lost"]},
            "distance": {
                "type": "object",
                "properties": DISTANCE,
                "required": ["unit", "value"],
                "additionalProperties": False,
            },
        },
        {"readOnlyHint": True},
    ),
    "grasp": (
        {
            "object_id": OBJECT_ID,
            "distance": {
                "type": "object",
                "properties": {
                    **DISTANCE,
                    "value": {"type": "number", "minimum": 0.2, "maximum": 0.6},
                },
                "required": ["unit", "value"],
            },
        },
        {"grasp_id": {"type": "string", "pattern": "^grs_[0-9]+$"}},
        {"destructiveHint": True},
    ),
    "confirm_with_user": ({"question": {"type": "string"}}, {"confirmed": {"type": "boolean"}}, {}),
    "walk_to": (
        {"object_id": OBJECT_ID},
        {"arrived": {"type": "boolean"}},
        {"destructiveHint": True},
    ),
}
MOTION_CONFIRMED = read_policy(
    {
        "name": "motion confirmed first",
        "initial": "start",
        "bad": ["violated"],
        "transitions": [
            {"from": "start", "tool": "confirm_with_user", "to": "ok"},
            {"from": "start", "tool": "grasp", "to": "violated"},
            {"from": "start", "tool": "walk_to", "to": "violated"},
        ],
    }
)


def _skills(order: tuple[str, ...]) -> dict[str, Any]:
    def spec(name: str) -> dict[str, Any]:
        inputs, outputs, annotations = SKILLS[name]
        return {
            "name": name,
            "inputSchema": {"type": "object", "properties": inputs, "required": sorted(inputs)},
            # Closed results: an open one is any record, and the whole of it fills an argument.
            "outputSchema": {
                "type": "object",
                "properties": outputs,
                "required": sorted(outputs),
                "additionalProperties": False,
            },
            "annotations": annotations,
        }

    return {name: read_contract(spec(name)) for name in order}


def _most_on_a_path(block: Any, tool: str) -> int:
    here = sum(call.tool == tool for call in block.calls)
    end = block.end
    if hasattr(end, "then"):
        return here + max(_most_on_a_path(end.then, tool), _most_on_a_path(end.otherwise, tool))
    return here


@pytest.mark.parametrize(
    "order",
    [
        ("estimate_pose", "confirm_with_user", "detect_object", "grasp", "walk_to"),
        ("grasp", "walk_to", "confirm_with_user", "detect_object", "estimate_pose"),
    ],
)
def test_a_call_a_policy_names_and_nothing_reads_is_left_out_where_the_program_needs_none(
    order: tuple[str, ...],
) -> None:
    skills = _skills(order)
    task = read_task("pick", {"name": {"type": "string"}}, {"type": "string", "pattern": "^grs_"})
    found = plan(skills, task, depth=7, policies=[MOTION_CONFIRMED])
    assert found.program is not None
    assert _most_on_a_path(found.program, "confirm_with_user") == 1
    assert "walk_to(" not in render(found.program)
    checked = verify(found.program, skills, task, policies=[MOTION_CONFIRMED])
    assert checked.count("proven") == len(checked.links)
