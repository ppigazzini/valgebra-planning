from __future__ import annotations

import json
from pathlib import Path

import pytest

from valgebra_planning.codemode import repair
from valgebra_planning.contracts import Contract, read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.flows import Flow, read_flow
from valgebra_planning.planner import plan
from valgebra_planning.program import Block, render
from valgebra_planning.verify import verify

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
TOOLS = {
    contract.name: contract
    for contract in map(
        read_contract, json.loads((EXAMPLE / "tools.json").read_text(encoding="utf-8"))["tools"]
    )
}
TASK = next(
    read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    if spec["name"] == "notify a parcel's contact"
)
VERIFIED = read_flow(json.loads((EXAMPLE / "flows.json").read_text(encoding="utf-8"))["flows"][0])
DIRECT = read_program(
    "c1 = find_parcel(tracking=tracking)\n"
    "c2 = notify(email=c1.parcel.contact)\n"
    "return c2.notice_id\n"
)


def _flow_links(program: Block, flow: Flow) -> list[tuple[str, str]]:
    checked = verify(program, TOOLS, TASK, flows=[flow])
    return [(link.claim, link.verdict) for link in checked.links if link.claim.startswith("flow")]


def test_a_value_from_an_untrusted_source_does_not_reach_the_sink() -> None:
    assert VERIFIED.sink == "notify.email"
    assert _flow_links(DIRECT, VERIFIED) == [
        (
            (
                "flow notices go to verified addresses: c1.parcel.contact from find_parcel"
                " reaches notify.email"
            ),
            "refuted",
        )
    ]


def test_the_planner_routes_the_value_through_the_sanitizer() -> None:
    unruled = plan(TOOLS, TASK).program
    assert unruled is not None
    assert render(unruled) == render(DIRECT)
    found = plan(TOOLS, TASK, flows=[VERIFIED])
    assert found.program is not None
    assert "verify_contact(email=c1.parcel.contact)" in render(found.program)
    checked = verify(found.program, TOOLS, TASK, flows=[VERIFIED])
    assert checked.count("proven") == len(checked.links)
    assert _flow_links(found.program, VERIFIED) == []


def test_a_test_narrowing_the_value_into_the_endorsed_set_lets_it_through() -> None:
    endorsed = read_flow(
        {
            "name": "only the operations desk",
            "sources": ["find_parcel"],
            "sink": "notify.email",
            "endorsed": {"enum": ["ops@parcels.example"]},
        }
    )
    [(_, verdict)] = _flow_links(DIRECT, endorsed)
    assert verdict == "refuted"
    narrowed = read_program(
        "c1 = find_parcel(tracking=tracking)\n"
        'if c1.parcel.contact == "ops@parcels.example":\n'
        "    c2 = notify(email=c1.parcel.contact)\n"
        "    return c2.notice_id\n"
        "else:\n"
        "    stop(c1.parcel.contact)\n"
    )
    [(_, verdict)] = _flow_links(narrowed, endorsed)
    assert verdict == "proven"


def test_a_sink_called_under_a_test_on_an_untrusted_value_is_an_implicit_flow() -> None:
    implicit = read_flow(
        {
            **json.loads((EXAMPLE / "flows.json").read_text(encoding="utf-8"))["flows"][0],
            "implicit": True,
        }
    )
    branched = read_program(
        "c1 = find_parcel(tracking=tracking)\n"
        'if c1.parcel.state == "lost":\n'
        "    c2 = verify_contact(email=c1.parcel.contact)\n"
        "    c3 = notify(email=c2.email)\n"
        "    return c3.notice_id\n"
        "else:\n"
        "    stop(c1.parcel.state)\n"
    )
    claims = _flow_links(branched, implicit)
    assert claims == [
        (
            "flow notices go to verified addresses: notify runs under a test on find_parcel",
            "refuted",
        )
    ]
    assert _flow_links(branched, VERIFIED) == []


SETTLE = next(
    read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    if spec["name"] == "settle a parcel"
)
#: Sah et al. 2026's integrity leak: the identity an authentication tool is asked about is the
#: user's, never one the agent makes up.
GIVEN = read_flow(
    {
        "name": "parcels are found by the number the user gave",
        "sources": ["tracking"],
        "sink": "find_parcel.tracking",
        "only": True,
    }
)
FOUND = read_flow(
    {
        "name": "returns are booked for the parcel found",
        "sources": ["find_parcel"],
        "sink": "book_return.parcel_id",
        "only": True,
    }
)
GROUNDED = (
    "c1 = find_parcel(tracking=tracking)\n"
    "c2 = book_return(parcel_id=c1.parcel.parcel_id, state=c1.parcel.state, "
    "weight_g=c1.parcel.weight_g)\n"
    "return c2.label_id\n"
)


def _held(source: str, flow: Flow) -> list[tuple[str, str, tuple[object, ...]]]:
    checked = verify(read_program(source), TOOLS, SETTLE, flows=[flow])
    return [
        (link.claim, link.verdict, link.counterexample)
        for link in checked.links
        if link.claim.startswith("flow")
    ]


def test_only_refutes_an_identifier_written_into_the_program() -> None:
    made_up = GROUNDED.replace("tracking=tracking", 'tracking="AB123456789IT"')
    assert _held(made_up, GIVEN) == [
        (
            (
                "flow parcels are found by the number the user gave: the literal 'AB123456789IT'"
                " reaches find_parcel.tracking"
            ),
            "refuted",
            ("AB123456789IT",),
        )
    ]
    assert _held(GROUNDED, GIVEN) == []


def test_only_refutes_an_input_or_a_tool_it_does_not_name() -> None:
    from_input = GROUNDED.replace("parcel_id=c1.parcel.parcel_id", "parcel_id=tracking")
    [(claim, verdict, _)] = _held(from_input, FOUND)
    assert (claim, verdict) == (
        (
            "flow returns are booked for the parcel found: tracking from tracking reaches"
            " book_return.parcel_id"
        ),
        "refuted",
    )
    approved = GROUNDED.replace(
        "c2 = book_return(parcel_id=c1.parcel.parcel_id",
        "c0 = approve_freight(parcel_id=c1.parcel.parcel_id)\n"
        "c2 = book_return(parcel_id=c0.approval_id",
    )
    [(claim, verdict, _)] = _held(approved, FOUND)
    assert (claim, verdict) == (
        (
            "flow returns are booked for the parcel found: c0.approval_id from approve_freight"
            " reaches book_return.parcel_id"
        ),
        "refuted",
    )
    assert _held(GROUNDED, FOUND) == []


def test_a_task_naming_an_input_as_a_rule_s_source_tool_is_refused() -> None:
    # The input would read as the source's result, and pass a rule holding every other origin.
    posing = read_task(
        "settle the parcel the user names",
        {"find_parcel": {"type": "object", "properties": {"parcel": {"type": "object"}}}},
        SETTLE.goal_schema,
    )
    with pytest.raises(ValueError, match="names an input as a tool: find_parcel"):
        verify(read_program(GROUNDED), TOOLS, posing, flows=[FOUND])


def test_only_lets_a_literal_inside_the_endorsed_set_through() -> None:
    desk = read_flow(
        {
            "name": "notices go to addresses the desk verified",
            "sources": ["verify_contact"],
            "sink": "notify.email",
            "only": True,
            "endorsed": {"enum": ["ops@parcels.example"]},
        }
    )
    source = 'c1 = notify(email="{}")\nreturn c1.notice_id\n'
    [(_, verdict, _)] = _held(source.format("ops@parcels.example"), desk)
    assert verdict == "proven"
    [(_, verdict, found)] = _held(source.format("ann@shop.example"), desk)
    assert (verdict, found) == ("refuted", ("ann@shop.example",))


def test_only_with_implicit_refutes_a_sink_under_a_test_on_any_other_origin() -> None:
    desk = read_flow(
        {
            "name": "notices from verified contacts alone",
            "sources": ["verify_contact"],
            "sink": "notify.email",
            "only": True,
            "implicit": True,
        }
    )
    branched = read_program(
        "c1 = find_parcel(tracking=tracking)\n"
        'if c1.parcel.state == "lost":\n'
        "    c2 = verify_contact(email=c1.parcel.contact)\n"
        "    c3 = notify(email=c2.email)\n"
        "    return c3.notice_id\n"
        "else:\n"
        "    stop(c1.parcel.state)\n"
    )
    assert _flow_links(branched, desk) == [
        (
            "flow notices from verified contacts alone: notify runs under a test on find_parcel",
            "refuted",
        )
    ]


def test_the_planner_grounds_every_identifier_a_rule_names() -> None:
    unruled = plan(TOOLS, SETTLE).program
    found = plan(TOOLS, SETTLE, flows=[GIVEN, FOUND]).program
    assert unruled is not None
    assert found is not None
    assert render(found) == render(unruled)
    assert "find_parcel(tracking=tracking)" in render(found)
    assert "book_return(parcel_id=c1.parcel.parcel_id" in render(found)
    checked = verify(found, TOOLS, SETTLE, flows=[GIVEN, FOUND])
    assert checked.count("proven") == len(checked.links)


def test_a_made_up_identifier_is_repaired_in_code_mode() -> None:
    planned = plan(TOOLS, SETTLE).program
    assert planned is not None
    grounded = render(planned)
    made_up = grounded.replace("tracking=tracking", 'tracking="AB123456789IT"')
    assert made_up != grounded
    told: list[str | None] = []

    def propose(feedback: str | None) -> str:
        told.append(feedback)
        return made_up if feedback is None else grounded

    repaired = repair(propose, TOOLS, SETTLE, flows=[GIVEN])
    assert repaired.accepted
    assert len(repaired.rounds) == 2
    assert told[1] is not None
    assert "the literal 'AB123456789IT' reaches find_parcel.tracking" in told[1]


SPELLED = {"name": "grounded", "sources": ["find_parcel"], "sink": "book_return.parcel_id"}


@pytest.mark.parametrize(
    ("spec", "refused", "match"),
    [
        ({**SPELLED, "Only": True}, ValueError, "'Only' is no key it reads"),
        ({**SPELLED, "onlyy": True}, ValueError, "'onlyy' is no key it reads"),
        ({**SPELLED, "only": "false"}, TypeError, "'only' is true or false, not str"),
        ({**SPELLED, "implicit": 1}, TypeError, "'implicit' is true or false, not int"),
        ({**SPELLED, "sink": "book_return"}, ValueError, "is no tool.argument"),
    ],
    ids=["Only", "onlyy", "only-string", "implicit-int", "sink"],
)
def test_a_flow_rule_means_what_its_keys_say_or_is_refused(
    spec: dict[str, object], refused: type[Exception], match: str
) -> None:
    with pytest.raises(refused, match=match):
        read_flow(spec)
    assert read_flow({**SPELLED, "only": True}).only


OBJECT_ID = {"type": "string", "pattern": "^obj_[0-9]+$"}
REACH = {"type": "number", "minimum": 0.2, "maximum": 0.6}


def _robot(name: str, inputs: dict, outputs: dict, *, read_only: bool) -> Contract:
    return read_contract(
        {
            "name": name,
            "inputSchema": {"type": "object", "properties": inputs, "required": [*inputs]},
            "outputSchema": {
                "type": "object",
                "properties": outputs,
                "required": [*outputs],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": read_only},
        }
    )


ROBOT = {
    "estimate_pose": _robot(
        "estimate_pose", {"object_id": OBJECT_ID}, {"distance_m": REACH}, read_only=True
    ),
    "walk_to": _robot(
        "walk_to", {"object_id": OBJECT_ID}, {"arrived": {"type": "boolean"}}, read_only=False
    ),
    "grasp": _robot(
        "grasp",
        {"object_id": OBJECT_ID, "distance_m": REACH},
        {"grasp_id": {"type": "string", "pattern": "^g_[0-9]+$"}},
        read_only=False,
    ),
}
#: A grasp's distance is the latest one measured for its object, since the last call that may act.
MEASURED = read_flow(
    {
        "name": "the distance is measured for the object",
        "sources": ["estimate_pose"],
        "sink": "grasp.distance_m",
        "only": True,
        "fresh": True,
        "key": "object_id",
    }
)
_SAID = f"flow {MEASURED.name}:"
PICK = read_task(
    "grasp an object",
    {"o": OBJECT_ID, "p": OBJECT_ID},
    {"type": "string", "pattern": "^g_[0-9]+$"},
)


@pytest.mark.parametrize(
    ("spec", "error"),
    [
        ({"fresh": True}, ValueError),
        ({"key": "object_id"}, ValueError),
        ({"only": True, "key": 1}, TypeError),
    ],
    ids=["fresh-without-only", "key-without-only", "key-no-name"],
)
def test_a_rule_reads_fresh_and_key_with_only_alone(spec: dict, error: type[Exception]) -> None:
    base = {"name": "r", "sources": ["estimate_pose"], "sink": "grasp.distance_m"}
    with pytest.raises(error):
        read_flow(base | spec)


@pytest.mark.parametrize(
    ("source", "refuted"),
    [
        (
            (
                "c1 = estimate_pose(object_id=o)\n"
                "c2 = grasp(object_id=o, distance_m=c1.distance_m)\n"
                "return c2.grasp_id\n"
            ),
            None,
        ),
        (
            (
                "c1 = estimate_pose(object_id=o)\n"
                "c2 = walk_to(object_id=o)\n"
                "c3 = grasp(object_id=o, distance_m=c1.distance_m)\n"
                "return c3.grasp_id\n"
            ),
            (
                f"{_SAID} c1.distance_m from estimate_pose was returned after every call before"
                " grasp that may act"
            ),
        ),
        (
            (
                "c1 = estimate_pose(object_id=o)\n"
                "c2 = estimate_pose(object_id=p)\n"
                "c3 = grasp(object_id=o, distance_m=c2.distance_m)\n"
                "return c3.grasp_id\n"
            ),
            f"{_SAID} c2.distance_m from estimate_pose was returned for the object_id grasp takes",
        ),
    ],
    ids=["measured-for-the-object", "measured-before-a-walk", "measured-for-another"],
)
def test_a_fresh_keyed_rule_admits_the_latest_reading_of_the_same_object(
    source: str, refuted: str | None
) -> None:
    checked = verify(read_program(source), ROBOT, PICK, flows=[MEASURED])
    assert [link.claim for link in checked.links if link.verdict != "proven"] == (
        [] if refuted is None else [refuted]
    )


def test_the_planner_grasps_at_the_distance_measured_for_its_object() -> None:
    planned = plan(ROBOT, PICK, flows=[MEASURED])
    assert planned.program is not None
    checked = verify(planned.program, ROBOT, PICK, flows=[MEASURED])
    assert all(link.verdict == "proven" for link in checked.links)
    called = render(planned.program)
    assert "estimate_pose(object_id=o)" in called
    assert "grasp(object_id=o," in called


def test_a_fresh_rule_lets_a_value_inside_the_endorsed_set_through_after_an_action() -> None:
    endorsed = read_flow(
        {
            "name": "a short reach needs no measure",
            "sources": ["estimate_pose"],
            "sink": "grasp.distance_m",
            "only": True,
            "fresh": True,
            "endorsed": {"type": "number", "minimum": 0.2, "maximum": 0.3},
        }
    )
    program = read_program(
        "c1 = estimate_pose(object_id=o)\n"
        "c2 = walk_to(object_id=o)\n"
        "c3 = grasp(object_id=o, distance_m=0.25)\n"
        "return c3.grasp_id\n"
    )
    checked = verify(program, ROBOT, PICK, flows=[endorsed])
    assert all(link.verdict == "proven" for link in checked.links)


def test_a_rule_names_its_scope_shared_or_principal() -> None:
    base = {"name": "r", "sources": ["estimate_pose"], "sink": "grasp.distance_m", "only": True}
    assert read_flow(base).scope == "shared"
    assert read_flow(base | {"scope": "principal"}).scope == "principal"
    with pytest.raises(ValueError, match="'scope' is 'shared' or 'principal', not 'everyone'"):
        read_flow(base | {"scope": "everyone"})
