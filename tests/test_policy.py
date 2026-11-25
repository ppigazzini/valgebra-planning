from __future__ import annotations

import itertools
import json
import math
import random
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from valgebra_planning._valgebra import Sampler, compile_schema
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
    for contract in map(
        read_contract, json.loads((EXAMPLE / "tools.json").read_text(encoding="utf-8"))["tools"]
    )
}
SETTLE = next(
    read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    if spec["name"] == "settle a parcel"
)
FREIGHT = read_policy(
    json.loads((EXAMPLE / "policies.json").read_text(encoding="utf-8"))["policies"][0]
)


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
    assert verdicts.pop("then.then") == "refuted"
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
    assert verdicts.pop("then.then") == "undecided"
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
        for spec in json.loads((EXAMPLE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
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


#: A call stepped when it is made, and two transitions reading its result: one from the state the
#: call leaves, and one, to a bad state, from the state before it.
READ_AFTER = read_policy(
    {
        "name": "a result read after the call",
        "initial": "idle",
        "bad": ["violated"],
        "transitions": [
            {"from": "idle", "tool": "approve_freight", "to": "asked"},
            {"from": "asked", "tool": "approve_freight", "result": True, "to": "granted"},
            {"from": "idle", "tool": "approve_freight", "result": True, "to": "violated"},
        ],
    }
)


def test_a_call_whose_outcome_is_not_read_holds_the_call_s_whole_image() -> None:
    monitor = Monitor(READ_AFTER)
    assert not monitor.observe("approve_freight", {"parcel_id": "pcl_1"})
    assert not monitor.unread("approve_freight", {"parcel_id": "pcl_1"}, frozenset())
    # The states before the call, the state it left, and the state a result reaches from there;
    # no result of the call is read from the state before it.
    assert monitor.held() == {"idle", "asked", "granted"}
    assert monitor.violations == []
    once = Monitor(FREIGHT)
    assert not once.observe("approve_freight", {"parcel_id": "pcl_1"})
    assert not once.unread("approve_freight", {"parcel_id": "pcl_1"}, frozenset())
    assert once.held() == {"start", "approved"}


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


#: A motion follows a confirmation that answered yes: a transition reading the result.
ANSWERED_YES = read_policy(
    {
        "name": "motion after a yes",
        "initial": "start",
        "bad": ["violated"],
        "transitions": [
            {
                "from": "start",
                "tool": "confirm_with_user",
                "result": {"properties": {"confirmed": {"const": True}}},
                "to": "ok",
            },
            {"from": "start", "tool": "walk_to", "to": "violated"},
            {"from": "start", "tool": "grasp", "to": "violated"},
        ],
    }
)


def test_a_transition_reading_the_result_is_taken_when_the_result_arrives() -> None:
    no = Monitor(ANSWERED_YES)
    assert not no.observe("confirm_with_user", {"question": "may I walk?"})
    assert not no.returned("confirm_with_user", {"question": "may I walk?"}, {"confirmed": False})
    assert no.observe("walk_to", {"object_id": "obj_1"})
    yes = Monitor(ANSWERED_YES)
    assert not yes.observe("confirm_with_user", {"question": "may I walk?"})
    assert not yes.returned("confirm_with_user", {"question": "may I walk?"}, {"confirmed": True})
    assert not yes.observe("walk_to", {"object_id": "obj_1"})
    # A call answering with an error returns nothing for the transition to read.
    errored = Monitor(ANSWERED_YES)
    assert not errored.observe("confirm_with_user", {"question": "may I walk?"})
    errored.errored()
    assert errored.observe("walk_to", {"object_id": "obj_1"})


IGNORED = (
    "c1 = detect_object(name=name)\n"
    "c2 = confirm_with_user(question=name)\n"
    "c3 = walk_to(object_id=c1.object_id)\n"
    "return c3.arrived\n"
)


@pytest.mark.parametrize("answer", [False, True])
def test_a_run_moves_only_after_the_answer_the_policy_reads(*, answer: bool) -> None:
    walked = answer
    skills = _skills(("detect_object", "confirm_with_user", "walk_to"))
    task = read_task("walk", {"name": {"type": "string"}}, {"type": "boolean"})
    made: list[str] = []
    tools = {
        "detect_object": lambda **_: {"status": "found", "object_id": "obj_1", "confidence": 0.9},
        "confirm_with_user": lambda **_: {"confirmed": answer},
        "walk_to": lambda **_: made.append("walk_to") or {"arrived": True},
    }
    ended = run(
        read_program(IGNORED), skills, task, {"name": "cup"}, tools, policies=[ANSWERED_YES]
    )
    assert (made == ["walk_to"]) is walked
    assert ended.ok is walked


def test_a_walk_splits_at_a_call_a_result_guard_reads_and_refutes_the_case_it_breaks() -> None:
    # One case holds the yes and walks under it; the other walks after a no.
    skills = _skills(("detect_object", "confirm_with_user", "walk_to"))
    task = read_task("walk", {"name": {"type": "string"}}, {"type": "boolean"})
    checked = verify(read_program(IGNORED), skills, task, policies=[ANSWERED_YES])
    verdicts = {
        link.where: link.verdict
        for link in checked.links
        if link.claim == "policy motion after a yes holds"
    }
    assert verdicts == {"case:c2:0": "proven", "case:c2:1": "refuted"}


GUARDED = (
    "c1 = detect_object(name=name)\n"
    "c2 = confirm_with_user(question=name)\n"
    "if c2.confirmed == True:\n"
    "    c3 = walk_to(object_id=c1.object_id)\n"
    "    return c3.arrived\n"
    "stop(c2.confirmed)\n"
)


def test_a_program_moving_only_under_the_answer_its_policy_reads_is_proven() -> None:
    skills = _skills(("detect_object", "confirm_with_user", "walk_to"))
    task = read_task("walk", {"name": {"type": "string"}}, {"type": "boolean"})
    checked = verify(read_program(GUARDED), skills, task, policies=[ANSWERED_YES])
    assert checked.verified
    # Each case reaches the one branch its answer meets, so the stop is the no's alone.
    assert [gap.where for gap in checked.gaps] == ["case:c2:1.else"]
    held = sorted(
        link.where for link in checked.links if link.claim == "policy motion after a yes holds"
    )
    assert held == ["case:c2:0.then", "case:c2:1.else"]


EACH_CONFIRMED = read_policy(
    {
        "name": "each object is confirmed before it is grasped",
        "initial": "start",
        "bad": ["violated"],
        "per": "object_id",
        "transitions": [
            {
                "from": "start",
                "tool": "confirm_with_user",
                "result": {"properties": {"confirmed": {"const": True}}},
                "to": "ok",
            },
            {"from": "start", "tool": "grasp", "to": "violated"},
            {"from": "ok", "tool": "reset", "to": "start"},
        ],
    }
)
YES = {"confirmed": True}


def test_a_policy_per_value_holds_an_instance_for_each_value_its_argument_takes() -> None:
    monitor = Monitor(EACH_CONFIRMED)
    asked = {"question": "may I grasp it?", "object_id": "obj_1"}
    assert not monitor.observe("confirm_with_user", asked)
    assert not monitor.returned("confirm_with_user", asked, YES)
    assert not monitor.observe("grasp", {"object_id": "obj_1"})
    assert monitor.observe("grasp", {"object_id": "obj_2"})
    assert monitor.violations == [
        f'{EACH_CONFIRMED.name} for object_id "obj_2": grasp reached violated'
    ]
    assert monitor.state == "start"


def test_a_call_carrying_no_value_steps_every_instance_and_the_ones_to_come() -> None:
    monitor = Monitor(EACH_CONFIRMED)
    asked = {"question": "may I grasp it?", "object_id": "obj_1"}
    monitor.observe("confirm_with_user", asked)
    monitor.returned("confirm_with_user", asked, YES)
    assert not monitor.observe("reset", {})
    assert monitor.observe("grasp", {"object_id": "obj_1"})


def test_a_refused_call_restores_every_instance() -> None:
    monitor = Monitor(EACH_CONFIRMED)
    asked = {"question": "may I grasp it?", "object_id": "obj_1"}
    monitor.observe("confirm_with_user", asked)
    monitor.returned("confirm_with_user", asked, YES)
    kept = monitor.snapshot()
    monitor.observe("grasp", {"object_id": "obj_2"})
    monitor.restore(kept)
    assert monitor.held() == frozenset({"start", "ok"})
    assert not monitor.observe("grasp", {"object_id": "obj_1"})


PERSONAL = {
    "confirm_with_user": read_contract(
        {
            "name": "confirm_with_user",
            "inputSchema": {
                "type": "object",
                "properties": {"question": {"type": "string"}, "object_id": OBJECT_ID},
                "required": ["question", "object_id"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {"confirmed": {"type": "boolean"}},
                "required": ["confirmed"],
                "additionalProperties": False,
            },
        }
    ),
    "grasp": read_contract(
        {
            "name": "grasp",
            "inputSchema": {
                "type": "object",
                "properties": {"object_id": OBJECT_ID},
                "required": ["object_id"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {"grasp_id": {"type": "string"}},
                "required": ["grasp_id"],
            },
        }
    ),
}
SWAPPED = (
    "c1 = confirm_with_user(question=question, object_id=first)\n"
    "c2 = grasp(object_id=second)\n"
    "return c2.grasp_id\n"
)


@pytest.mark.parametrize(("second", "refused"), [("obj_1", False), ("obj_2", True)])
def test_a_run_refuses_a_grasp_of_an_object_no_confirmation_was_for(
    second: str, *, refused: bool
) -> None:
    task = read_task(
        "grasp",
        {"question": {"type": "string"}, "first": OBJECT_ID, "second": OBJECT_ID},
        {"type": "string"},
    )
    made: list[str] = []
    tools = {
        "confirm_with_user": lambda **_: {"confirmed": True},
        "grasp": lambda object_id: made.append(object_id) or {"grasp_id": "grs_1"},
    }
    inputs = {"question": "may I?", "first": "obj_1", "second": second}
    ended = run(read_program(SWAPPED), PERSONAL, task, inputs, tools, policies=[EACH_CONFIRMED])
    assert (ended.outcome == "refused") is refused
    assert made == ([] if refused else ["obj_1"])
    # The walk holds no value's identity, so it proves no link of such a policy either way.
    checked = verify(read_program(SWAPPED), PERSONAL, task, policies=[EACH_CONFIRMED])
    verdicts = {link.verdict for link in checked.links if link.claim.startswith("policy")}
    assert verdicts == {"undecided"}


def test_a_policy_names_its_per_argument_by_name() -> None:
    with pytest.raises(TypeError, match="'per' is an argument's name"):
        read_policy({"name": "p", "initial": "a", "bad": [], "transitions": [], "per": 1})


def _interval(low: int, width: int) -> dict[str, Any]:
    """Return the records whose ``d`` lies in ``[low, low + width]``."""
    return {
        "type": "object",
        "properties": {"d": {"type": "number", "minimum": low, "maximum": low + width}},
        "required": ["d"],
        "additionalProperties": False,
    }


INTERVALS = st.builds(_interval, st.integers(0, 5), st.integers(1, 3))
#: A transition from `s0` on `f`, reading the call's record, its result, or both.
TRANSITIONS = st.builds(
    lambda target, arguments, result: {
        "from": "s0",
        "tool": "f",
        "to": target,
        **({"arguments": arguments} if arguments else {}),
        **({"result": result} if result else {}),
    },
    st.sampled_from(["a", "b", "c"]),
    st.one_of(st.none(), INTERVALS),
    st.one_of(st.none(), INTERVALS),
)
POLICIES = st.lists(TRANSITIONS, min_size=2, max_size=4).map(
    lambda transitions: read_policy(
        {"name": "p", "initial": "s0", "bad": ["c"], "transitions": transitions}
    )
)
GRID = [0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8]


def _first_match(policy: Any, value: float, outcome: float) -> str:
    """Return the state the monitor takes ``s0`` to on ``f(d=value)`` returning ``outcome``."""
    monitor = Monitor(policy)
    made = monitor.stepped("s0", "f", {"d": value}, frozenset())
    return monitor.stepped(made, "f", {"d": value}, frozenset(), {"d": outcome})


@settings(max_examples=100, deadline=None)
@given(POLICIES, st.sampled_from(GRID), st.sampled_from(GRID))
def test_the_walk_on_one_value_holds_the_monitor_s_first_match_alone(
    policy: Any, value: float, outcome: float
) -> None:
    record = compile_schema({"const": {"d": value}})
    result = compile_schema({"const": {"d": outcome}})
    walked = policy.step(frozenset({"s0"}), "f", record, result=result)
    assert walked == {_first_match(policy, value, outcome)}


@settings(max_examples=100, deadline=None)
@given(POLICIES, INTERVALS, INTERVALS)
def test_the_walk_on_a_set_holds_every_state_the_monitor_takes_a_value_of_it_to(
    policy: Any, records: dict[str, Any], results: dict[str, Any]
) -> None:
    walked = policy.step(
        frozenset({"s0"}), "f", compile_schema(records), result=compile_schema(results)
    )
    bounds = [
        (schema["properties"]["d"]["minimum"], schema["properties"]["d"]["maximum"])
        for schema in (records, results)
    ]
    for value in [v for v in GRID if bounds[0][0] <= v <= bounds[0][1]]:
        for outcome in [w for w in GRID if bounds[1][0] <= w <= bounds[1][1]]:
            assert _first_match(policy, value, outcome) in walked


#: A walk is allowed near the object alone, the rule written first-match: a pose within a
#: meter to `near`, any pose to `far`, and a walk from `far` a violation.
NEAR_FIRST = read_policy(
    {
        "name": "walk only when near",
        "initial": "start",
        "bad": ["violated"],
        "transitions": [
            {
                "from": "start",
                "tool": "estimate_pose",
                "result": {"properties": {"distance_m": {"maximum": 1}}},
                "to": "near",
            },
            {"from": "start", "tool": "estimate_pose", "result": True, "to": "far"},
            {"from": "far", "tool": "walk_to", "to": "violated"},
        ],
    }
)


def _robot_tool(name: str, inputs: dict[str, Any], outputs: dict[str, Any]) -> Any:
    return read_contract(
        {
            "name": name,
            "inputSchema": {
                "type": "object",
                "properties": inputs,
                "required": sorted(inputs),
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": outputs,
                "required": sorted(outputs),
                "additionalProperties": False,
            },
        }
    )


def test_a_rule_written_first_match_is_proven_as_the_monitor_reads_it() -> None:
    object_id = {"type": "string", "pattern": "^obj_[0-9]+$"}
    reach = {"type": "number", "minimum": 0.2, "maximum": 0.6}
    tools = {
        "estimate_pose": _robot_tool(
            "estimate_pose",
            {"object_id": object_id},
            {"distance_m": {"type": "number", "minimum": 0, "maximum": 10}},
        ),
        "grasp": _robot_tool("grasp", {"object_id": object_id, "distance_m": reach}, {}),
        "walk_to": _robot_tool(
            "walk_to", {"object_id": object_id}, {"arrived": {"type": "boolean"}}
        ),
    }
    task = read_task(
        "walk near", {"o": object_id}, {"type": "boolean"}, stops={"estimate_pose": True}
    )
    source = (
        "c1 = estimate_pose(object_id=o)\n"
        "if c1.distance_m in grasp.distance_m:\n"
        "    c2 = walk_to(object_id=o)\n"
        "    return c2.arrived\n"
        "stop(c1.distance_m)\n"
    )
    checked = verify(read_program(source), tools, task, policies=[NEAR_FIRST])
    assert all(link.verdict == "proven" for link in checked.links), checked.links


def test_a_refusal_names_the_calls_that_move_the_policy_on() -> None:
    monitor = Monitor(FREIGHT)
    assert monitor.ways_out("book_freight_return") == [
        "from start, approve_freight leads to approved"
    ]
    # The refused call is no way out of the state it was refused in.
    assert monitor.ways_out("approve_freight") == []
    consent = read_policy(
        {
            "name": "a motion follows a yes",
            "initial": "start",
            "bad": ["violated"],
            "transitions": [
                {"from": "start", "tool": "confirm", "result": {"const": True}, "to": "ok"},
                {"from": "start", "hint": "destructiveHint", "to": "violated"},
                {"from": "ok", "hint": "destructiveHint", "to": "start"},
            ],
        }
    )
    assert Monitor(consent).ways_out("walk_to", frozenset({"destructiveHint"})) == [
        "from start, confirm, by its result, leads to ok"
    ]


def test_a_policy_names_its_scope_shared_or_principal() -> None:
    spec = {"name": "p", "initial": "a", "bad": [], "transitions": []}
    assert read_policy(spec).scope == "shared"
    assert read_policy(spec | {"scope": "principal"}).scope == "principal"
    with pytest.raises(ValueError, match="'scope' is 'shared' or 'principal', not 'everyone'"):
        read_policy(spec | {"scope": "everyone"})


def test_a_way_out_names_its_result_as_a_test_and_leads_out() -> None:
    """A refused walk names the call that admits it, by the answer it needs.

    The confirmation's yes leads out and is named as the test a program writes; its no leads
    back to where the walk is refused, and is left out.
    """
    consent = read_policy(
        {
            "name": "a walk needs a yes",
            "initial": "unasked",
            "bad": ["refused"],
            "transitions": [
                {"from": "unasked", "tool": "walk", "to": "refused"},
                {
                    "from": "unasked",
                    "tool": "confirm",
                    "result": {"properties": {"status": {"const": "yes"}}},
                    "to": "yes",
                },
                {
                    "from": "unasked",
                    "tool": "confirm",
                    "result": {"properties": {"status": {"const": "no"}}},
                    "to": "unasked",
                },
            ],
        }
    )
    assert Monitor(consent).ways_out("walk") == [
        "from unasked, confirm with status 'yes' leads to yes"
    ]
    # Where no call leads out at once, each call that moves the policy on is named.
    twice = read_policy(
        {
            "name": "a walk needs two checks",
            "initial": "unchecked",
            "bad": ["refused"],
            "transitions": [
                {"from": "unchecked", "tool": "walk", "to": "refused"},
                {"from": "once", "tool": "walk", "to": "refused"},
                {"from": "unchecked", "tool": "check", "to": "once"},
                {"from": "once", "tool": "check", "to": "twice"},
            ],
        }
    )
    assert Monitor(twice).ways_out("walk") == ["from unchecked, check leads to once"]


def test_a_result_no_value_is_in_is_walked_as_one_case() -> None:
    # The set is empty, so it splits into no case on the guard that reads it: the walk goes on as
    # where none does, every transition on the result possible and none sure.
    specs: list[dict[str, Any]] = [
        {
            "name": "look",
            "inputSchema": {"type": "object"},
            "outputSchema": {"type": "object", "properties": {"v": False}, "required": ["v"]},
        },
        {"name": "act", "inputSchema": {"type": "object"}, "outputSchema": {"type": "object"}},
    ]
    tools = {contract.name: contract for contract in map(read_contract, specs)}
    policy = read_policy(
        {
            "name": "looked",
            "initial": "start",
            "bad": ["violated"],
            "transitions": [
                {
                    "from": "start",
                    "tool": "look",
                    "result": {"properties": {"v": {"const": 1}}},
                    "to": "seen",
                },
                {"from": "start", "tool": "act", "to": "violated"},
            ],
        }
    )
    for text, verdict in (
        ("c0 = look()\nreturn c0\n", "proven"),
        ("c0 = look()\nc1 = act()\nreturn c1\n", "undecided"),
    ):
        links = verify(read_program(text), tools, read_task("t", {}, True), policies=[policy]).links
        assert [link.verdict for link in links if link.claim == "policy looked holds"] == [verdict]


class _Clock:
    """A clock a test sets."""

    def __init__(self, now: float = 0.0) -> None:
        self.now, self.read = now, 0

    def __call__(self) -> float:
        self.read += 1
        return self.now


def _lapsing(*transitions: dict[str, Any], **keys: Any) -> dict[str, Any]:
    """Return a policy's document: a yes is asked, lapses, and a walk without one is refused."""
    asked = {"from": "unasked", "tool": "confirm", "result": {"const": "yes"}, "to": "yes"}
    again = {"from": "yes", "tool": "confirm", "result": {"const": "yes"}, "to": "yes"}
    walk = {"from": "unasked", "tool": "walk", "to": "violated"}
    return {
        "name": "consent",
        "initial": "unasked",
        "bad": ["violated"],
        "transitions": [asked, again, walk, *transitions],
        **keys,
    }


#: A yes held for 120 seconds.
CONSENT = _lapsing({"from": "yes", "after_s": 120, "to": "unasked"})


def _consented(clock: _Clock, spec: dict[str, Any] = CONSENT, **arguments: Any) -> Monitor:
    """Return a monitor that read a yes at the clock's time."""
    monitor = Monitor(read_policy(spec), clock=clock)
    assert not monitor.observe("confirm", arguments)
    assert not monitor.returned("confirm", arguments, "yes")
    return monitor


@pytest.mark.parametrize(
    ("spec", "says"),
    [
        (_lapsing({"from": "yes", "after_s": 0, "to": "unasked"}), "positive number of seconds"),
        (_lapsing({"from": "yes", "after_s": -1, "to": "unasked"}), "positive number of seconds"),
        (_lapsing({"from": "yes", "after_s": True, "to": "unasked"}), "positive number of seconds"),
        (_lapsing({"from": "yes", "after_s": "120", "to": "no"}), "positive number of seconds"),
        (_lapsing({"from": "yes", "after_s": math.inf, "to": "no"}), "positive number of seconds"),
        (_lapsing({"from": "yes", "after_s": math.nan, "to": "no"}), "positive number of seconds"),
        (_lapsing({"from": "yes", "after_s": 1, "to": "no", "tool": "walk"}), "'tool' is no key"),
        (_lapsing({"from": "violated", "after_s": 1, "to": "no"}), "a bad state is never left"),
        (_lapsing({"from": "yse", "after_s": 1, "to": "unasked"}), "no transition enters it"),
        (_lapsing({"from": "yes", "after_s": 1, "to": "yes"}), "lead back, yes to yes"),
        (
            _lapsing(
                {"from": "yes", "after_s": 1, "to": "no"},
                {"from": "yes", "after_s": 2, "to": "old"},
            ),
            "a state lapses once, and it lapses to 'no'",
        ),
        (
            _lapsing(
                {"from": "yes", "after_s": 1, "to": "no"}, {"from": "no", "after_s": 2, "to": "yes"}
            ),
            "lead back, yes to no to yes",
        ),
    ],
)
def test_a_lapse_no_monitor_could_hold_is_refused(spec: dict[str, Any], says: str) -> None:
    with pytest.raises(ValueError, match="consent") as refused:
        read_policy(spec)
    assert says in str(refused.value)


@pytest.mark.parametrize(
    ("seconds", "held"), [(0, "yes"), (119, "yes"), (120, "violated"), (121, "violated")]
)
def test_a_yes_lapses_once_it_was_held_its_seconds(seconds: float, held: str) -> None:
    clock = _Clock(1000.0)
    monitor = _consented(clock)
    clock.now += seconds
    assert monitor.observe("walk", {}) == (held == "violated")
    assert monitor.held() == {held}


def test_a_yes_given_again_is_held_from_the_last_one() -> None:
    clock = _Clock()
    monitor = _consented(clock)
    clock.now = 100
    monitor.observe("confirm", {})
    monitor.returned("confirm", {}, "yes")
    clock.now = 219
    assert monitor.held() == {"yes"}
    clock.now = 220
    assert monitor.held() == {"unasked"}


def test_a_chain_of_lapses_is_read_in_the_order_it_came_due() -> None:
    chain = _lapsing(
        {"from": "yes", "after_s": 10, "to": "stale"},
        {"from": "stale", "after_s": 5, "to": "unasked"},
    )
    for now, state in ((9, "yes"), (10, "stale"), (14, "stale"), (15, "unasked"), (900, "unasked")):
        clock = _Clock()
        monitor = _consented(clock, chain)
        clock.now = now
        assert monitor.held() == {state}


def test_a_state_a_call_may_have_left_lapses_beside_the_one_it_reached() -> None:
    # The second confirmation answered with an error, so the run may be at either yes.
    clock = _Clock()
    left = _lapsing(
        {"from": "yes", "after_s": 120, "to": "unasked"},
        {"from": "yes", "tool": "walk", "to": "walked"},
    )
    monitor = _consented(clock, left)
    clock.now = 60
    monitor.observe("walk", {})
    monitor.errored()
    assert monitor.held() == {"yes", "walked"}
    clock.now = 120
    assert monitor.held() == {"unasked", "walked"}
    assert monitor.observe("walk", {})


def test_a_result_not_read_may_have_entered_its_state_at_any_time_since_the_call() -> None:
    clock = _Clock()
    stale = _lapsing({"from": "yes", "after_s": 120, "to": "stale"})
    monitor = Monitor(read_policy(stale), clock=clock)
    monitor.observe("confirm", {})
    clock.now = 50
    monitor.unread("confirm", {}, frozenset())
    for now, held in (
        (50, {"unasked", "yes"}),
        (119, {"unasked", "yes"}),
        (120, {"unasked", "yes", "stale"}),
        (169, {"unasked", "yes", "stale"}),
        (170, {"unasked", "stale"}),
    ):
        clock.now = now
        assert monitor.held() == held


def test_the_run_s_end_reads_the_clock() -> None:
    owed = _lapsing({"from": "yes", "after_s": 30, "to": "unasked"}, pending=["yes"])
    clock = _Clock()
    monitor = _consented(clock, owed)
    clock.now = 29
    assert monitor.ended()
    monitor = _consented(clock, owed)
    clock.now += 30
    assert not monitor.ended()


def test_a_lapse_into_a_bad_state_refuses_the_call_that_finds_it() -> None:
    due = _lapsing({"from": "yes", "after_s": 30, "to": "violated"})
    clock = _Clock()
    monitor = _consented(clock, due)
    clock.now = 30
    assert monitor.observe("look", {})
    assert monitor.violations[-1] == "consent: a state lapsed into violated"
    clock.now = 0
    monitor = _consented(clock, due)
    clock.now = 30
    assert monitor.ended()


def test_each_value_s_instance_lapses_by_its_own_yes() -> None:
    clock = _Clock()
    each = _lapsing({"from": "yes", "after_s": 120, "to": "unasked"}, per="object_id")
    monitor = _consented(clock, each, object_id="a")
    clock.now = 60
    monitor.observe("confirm", {"object_id": "b"})
    monitor.returned("confirm", {"object_id": "b"}, "yes")
    clock.now = 125
    assert not monitor.observe("walk", {"object_id": "b"})
    assert monitor.observe("walk", {"object_id": "a"})


def test_a_refused_call_s_restore_keeps_when_each_state_was_entered() -> None:
    clock = _Clock()
    monitor = _consented(clock)
    kept = monitor.snapshot()
    clock.now = 100
    monitor.observe("confirm", {})
    monitor.returned("confirm", {}, "yes")
    monitor.restore(kept)
    assert Monitor.at(monitor.policy, kept, clock).snapshot() == kept
    clock.now = 120
    assert monitor.held() == {"unasked"}


def test_a_policy_with_no_lapse_reads_no_clock() -> None:
    clock = _Clock()
    monitor = _consented(clock, _lapsing())
    monitor.held()
    monitor.ended()
    monitor.observe("walk", {})
    assert clock.read == 0


def test_a_way_out_names_the_time_that_leads_out() -> None:
    cooling = {
        "name": "cool down",
        "initial": "free",
        "bad": ["violated"],
        "transitions": [
            {"from": "free", "tool": "fire", "to": "hot"},
            {"from": "hot", "tool": "fire", "to": "violated"},
            {"from": "hot", "after_s": 2.5, "to": "free"},
        ],
    }
    clock = _Clock()
    monitor = Monitor(read_policy(cooling), clock=clock)
    assert not monitor.observe("fire", {})
    assert monitor.ways_out("fire") == ["from hot, 2.5 s lead to free"]
    clock.now = 2.5
    assert not monitor.observe("fire", {})


STATES = ["s", "t", "u", "v"]


@st.composite
def _timed(draw: st.DrawFn) -> tuple[dict[str, Any], list[tuple[str, str, Any, int]]]:
    """Draw a policy with lapses and a timed trace of calls, results and errors."""
    order = draw(st.permutations(STATES))
    lapses = [
        {"from": a, "after_s": draw(st.integers(1, 6)), "to": b}
        for a, b in itertools.pairwise(order)
        if draw(st.booleans())
    ]
    moves = [
        {"from": draw(st.sampled_from(STATES)), "tool": tool, "to": draw(st.sampled_from(STATES))}
        | ({"result": {"const": answer}} if answer is not None else {})
        for tool in ("f", "g")
        for answer in (None, 1)
        for _ in range(draw(st.integers(0, 2)))
    ]
    while True:
        # No lapse is out of a state nothing enters.
        entered = {order[0], *(t["to"] for t in (*lapses, *moves))}
        left = [t for t in lapses if t["from"] in entered]
        if left == lapses:
            break
        lapses = left
    spec = {"name": "p", "initial": order[0], "bad": [], "transitions": [*lapses, *moves]}
    if draw(st.booleans()):
        spec["per"] = "k"
    trace = draw(
        st.lists(
            st.tuples(
                st.sampled_from(["f", "g"]),
                st.sampled_from(["result", "other", "error", "silent"]),
                st.sampled_from([None, 1, 2]),
                st.integers(0, 4),
            ),
            max_size=8,
        )
    )
    return spec, trace


class _Reference:
    """The timed automaton read one configuration at a time: a state and when it was entered."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self.lapses = {t["from"]: t for t in spec["transitions"] if "after_s" in t}
        self.calls = [t for t in spec["transitions"] if "after_s" not in t]
        self.per = spec.get("per")
        self.own: set[tuple[str, int]] = {(spec["initial"], 0)}
        self.values: dict[Any, set[tuple[str, int]]] = {}

    def lapse(self, configurations: set[tuple[str, int]], now: int) -> set[tuple[str, int]]:
        moved = set()
        for configuration in configurations:
            state, entered = configuration
            while state in self.lapses and entered + self.lapses[state]["after_s"] <= now:
                entered += self.lapses[state]["after_s"]
                state = self.lapses[state]["to"]
            moved.add((state, entered))
        return moved

    def at(self, now: int) -> None:
        self.own = self.lapse(self.own, now)
        self.values = {key: self.lapse(held, now) for key, held in self.values.items()}

    def step(self, tool: str, key: Any, now: int, answer: Any, *, returned: bool) -> None:
        def after(configuration: tuple[str, int]) -> tuple[str, int]:
            for t in self.calls:
                if t["from"] != configuration[0] or t["tool"] != tool:
                    continue
                if ("result" in t) == returned and (not returned or t["result"]["const"] == answer):
                    return t["to"], now
            return configuration

        self.at(now)
        if self.per and key is not None:
            held = self.values.setdefault(key, set(self.own))
            self.values[key] = set(map(after, held))
        else:
            self.own = set(map(after, self.own))
            self.values = {k: set(map(after, held)) for k, held in self.values.items()}

    def states(self) -> set[str]:
        return {state for held in (self.own, *self.values.values()) for state, _ in held}


@given(_timed())
@settings(max_examples=400, deadline=None)
def test_a_monitor_holds_the_states_the_timed_automaton_does(
    drawn: tuple[dict[str, Any], list[tuple[str, str, Any, int]]],
) -> None:
    spec, trace = drawn
    clock = _Clock()
    monitor, reference = Monitor(read_policy(spec), clock=clock), _Reference(spec)
    now = 0
    for tool, outcome, key, waited in trace:
        arguments = {} if key is None else {"k": key}
        now += waited
        clock.now = now
        reference.at(now)
        own, values = set(reference.own), {k: set(v) for k, v in reference.values.items()}
        monitor.observe(tool, arguments)
        reference.step(tool, key, now, None, returned=False)
        assert monitor.held() == reference.states()
        now += 1
        clock.now = now
        if outcome == "error":
            monitor.errored()
            if reference.per and key is not None:
                reference.values[key] |= values.get(key, own)
            else:
                reference.own |= own
                reference.values = {
                    k: held | values.get(k, set()) for k, held in reference.values.items()
                }
        elif outcome != "silent":
            answer = 1 if outcome == "result" else 0
            monitor.returned(tool, arguments, answer)
            reference.step(tool, key, now, answer, returned=True)
        reference.at(now)
        assert monitor.held() == reference.states()
        snapshot = monitor.snapshot()
        assert Monitor.at(monitor.policy, snapshot, clock).snapshot() == snapshot
    now += 30
    clock.now = now
    reference.at(now)
    assert monitor.held() == reference.states()


#: Tools of no argument, and what each may answer.
ROBOT = {
    contract.name: contract
    for contract in map(
        read_contract,
        [
            {
                "name": name,
                "inputSchema": {"type": "object"},
                "outputSchema": {
                    "type": "object",
                    "properties": {"status": {"enum": answers}, "items": {"type": "array"}},
                    "required": ["status", "items"],
                },
            }
            for name, answers in (("confirm", ["yes"]), ("look", ["ok"]), ("walk", ["walked"]))
        ],
    )
}


def _timed_links(
    spec: dict[str, Any], text: str, **rules: Any
) -> list[tuple[str, tuple[str, ...]]]:
    """Return the verdict of each policy link of a program, and what it assumes."""
    links = verify(
        read_program(text),
        ROBOT,
        read_task("t", {"xs": {"type": "array"}}, True),
        policies=[read_policy(spec)],
        **rules,
    ).links
    return [(link.verdict, link.assumed) for link in links if link.claim.startswith("policy")]


#: A yes by its status, held for 120 seconds.
STATUS_CONSENT: dict[str, Any] = {
    "name": "consent",
    "initial": "unasked",
    "bad": ["violated"],
    "transitions": [
        {
            "from": "unasked",
            "tool": "confirm",
            "result": {"properties": {"status": {"const": "yes"}}},
            "to": "yes",
        },
        {"from": "unasked", "tool": "walk", "to": "violated"},
        {"from": "yes", "after_s": 120, "to": "unasked"},
    ],
}
WITHIN = "the run reaches walk within 120 s of entering yes"


@pytest.mark.parametrize(
    ("text", "links"),
    [
        ("c1 = confirm()\nc2 = walk()\nreturn c2\n", [("proven", (WITHIN,))]),
        ("c1 = confirm()\nc2 = look()\nc3 = walk()\nreturn c3\n", [("proven", (WITHIN,))]),
        ("c1 = confirm()\nc2 = look()\nreturn c2\n", [("proven", ())]),
        ("c1 = walk()\nreturn c1\n", [("refuted", ())]),
        (
            "c1 = confirm()\nfor x in xs:\n    c2 = walk()\nreturn c1\n",
            [("proven", (WITHIN,)), ("proven", ())],
        ),
        (
            "c1 = confirm()\nif c1.status == 'yes':\n    c2 = walk()\n    return c2\nreturn c1\n",
            [("proven", (WITHIN,))],
        ),
    ],
    ids=[
        "asks-then-walks",
        "looks-between",
        "never-walks",
        "never-asks",
        "walks-in-a-loop",
        "tests",
    ],
)
def test_a_policy_link_names_the_timing_it_rests_on_and_no_other_does(
    text: str, links: list[tuple[str, tuple[str, ...]]]
) -> None:
    assert sorted(_timed_links(STATUS_CONSENT, text)) == sorted(links)


@pytest.mark.parametrize(
    ("text", "links"),
    [
        ("c2 = look()\nexcept ToolError:\n    c3 = walk()\n    return c3\n", [(WITHIN,), ()]),
        ("c2 = look()\nexcept ToolError:\n    return c0\n", [(WITHIN,), (WITHIN,)]),
    ],
)
def test_an_error_block_names_the_calls_before_it_and_its_own(
    text: str, links: list[tuple[str, ...]]
) -> None:
    walked = "c0 = walk()\n" if "c0" in text else ""
    body, _, handler = text.partition("\n")
    program = f"c1 = confirm()\n{walked}try:\n    {body}\n{handler}return c2\n"
    assert _timed_links(STATUS_CONSENT, program) == [("proven", named) for named in links]


def test_a_lapse_that_only_admits_more_is_no_assumption() -> None:
    # A cool-down read as never coming due is the stricter reading: nothing rests on it.
    cooling = {
        "name": "cool down",
        "initial": "free",
        "bad": ["violated"],
        "transitions": [
            {"from": "free", "tool": "walk", "to": "hot"},
            {"from": "hot", "tool": "walk", "to": "violated"},
            {"from": "hot", "after_s": 2, "to": "free"},
        ],
    }
    assert _timed_links(cooling, "c1 = walk()\nc2 = look()\nreturn c2\n") == [("proven", ())]
    assert _timed_links(cooling, "c1 = walk()\nc2 = walk()\nreturn c2\n") == [("refuted", ())]


def test_an_end_past_a_lapse_into_an_obligation_or_a_violation_is_named() -> None:
    for spec in ({"pending": ["late"], "bad": []}, {"bad": ["late"]}):
        due = {
            "name": "due",
            "initial": "free",
            "transitions": [
                {"from": "free", "tool": "walk", "to": "warm"},
                {"from": "warm", "after_s": 9, "to": "late"},
            ],
            **spec,
        }
        assert _timed_links(due, "c1 = walk()\nreturn c1\n") == [
            ("proven", ("the run ends within 9 s of entering warm",))
        ]
        # With no call at all, the state it starts in is the one that lapses.
        assert _timed_links({**due, "initial": "warm"}, "return xs\n") == [
            ("proven", ("the run ends within 9 s of entering warm",))
        ]


def test_a_result_read_past_its_call_s_lapse_is_named() -> None:
    # The call enters a state that lapses, and its result is read from there.
    asked = {
        "name": "answered in time",
        "initial": "idle",
        "bad": ["violated"],
        "transitions": [
            {"from": "idle", "tool": "confirm", "to": "asking"},
            {"from": "asking", "tool": "confirm", "result": True, "to": "answered"},
            {"from": "asking", "after_s": 30, "to": "unanswered"},
            {"from": "unanswered", "tool": "walk", "to": "violated"},
        ],
    }
    assert _timed_links(asked, "c1 = confirm()\nc2 = walk()\nreturn c2\n") == [
        ("proven", ("the run reaches the result of confirm within 30 s of entering asking",))
    ]


def test_a_chain_of_lapses_is_read_to_its_end() -> None:
    chain = {
        **STATUS_CONSENT,
        "transitions": [
            *STATUS_CONSENT["transitions"][:2],
            {"from": "yes", "after_s": 120, "to": "stale"},
            {"from": "stale", "after_s": 5, "to": "unasked"},
        ],
    }
    assert _timed_links(chain, "c1 = confirm()\nc2 = walk()\nreturn c2\n") == [
        ("proven", (WITHIN,))
    ]


def test_a_plan_names_the_timing_its_policies_rest_on() -> None:
    lapsing = json.loads((EXAMPLE / "policies.json").read_text(encoding="utf-8"))["policies"][0]
    lapsing = {
        **lapsing,
        "transitions": [
            *lapsing["transitions"],
            {"from": "approved", "after_s": 600, "to": lapsing["initial"]},
        ],
    }
    planned = plan(TOOLS, SETTLE, policies=[read_policy(lapsing)])
    untimed = plan(TOOLS, SETTLE, policies=[FREIGHT])
    assert planned.program == untimed.program
    assert planned.timing == (
        "the run reaches book_freight_return within 600 s of entering approved",
    )
    assert untimed.timing == ()
