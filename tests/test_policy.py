from __future__ import annotations

import json
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
