from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from valgebra_planning.contracts import Contract, read_contract, read_task
from valgebra_planning.critic import critique, read_program
from valgebra_planning.mining import mine
from valgebra_planning.planner import plan
from valgebra_planning.program import render
from valgebra_planning.verify import verify

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
TOOLS = {
    contract.name: contract
    for contract in map(read_contract, json.loads((EXAMPLE / "tools.json").read_text())["tools"])
}
OBSERVED = json.loads((EXAMPLE / "observed.json").read_text())["calls"]
TASK = next(
    read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text())["tasks"]
    if spec["name"] == "notify a parcel's contact"
)


def test_slots_holding_one_observed_value_are_one_type() -> None:
    types = mine(OBSERVED, TOOLS)
    assert types.agree(
        ("result", "find_parcel.parcel.contact"), ("argument", "verify_contact.email")
    )
    assert types.agree(("result", "verify_contact.email"), ("argument", "notify.email"))
    assert not types.agree(("result", "find_parcel.parcel.contact"), ("argument", "notify.email"))
    assert types.agree(
        ("result", "find_parcel.parcel.parcel_id"), ("argument", "book_return.parcel_id")
    )
    # A slot never observed carries no type, so nothing narrows what fills it.
    assert types.agree(
        ("result", "find_parcel.parcel.parcel_id"), ("argument", "redirect.parcel_id")
    )
    assert not types.typed(
        ("result", "find_parcel.parcel.parcel_id"), ("argument", "redirect.parcel_id")
    )


def test_a_boolean_or_a_null_links_nothing() -> None:
    calls = [
        {"tool": "notify", "arguments": {"email": None}, "result": {"notice_id": True}},
        {"tool": "verify_contact", "arguments": {"email": True}, "result": {"email": None}},
    ]
    assert mine(calls, TOOLS).of == {}


def test_mined_types_route_the_notice_through_the_sanitizer_as_observed() -> None:
    unmined = plan(TOOLS, TASK)
    assert unmined.program is not None
    assert "verify_contact" not in render(unmined.program)
    assert unmined.assumed == ()
    found = plan(TOOLS, TASK, types=mine(OBSERVED, TOOLS))
    assert found.program is not None
    assert render(found.program) == (
        "c1 = find_parcel(tracking=tracking)\n"
        "c2 = verify_contact(email=c1.parcel.contact)\n"
        "c3 = notify(email=c2.email)\n"
        "return c3.notice_id"
    )
    assert found.assumed == (
        "c1.parcel.contact -> verify_contact.email",
        "c2.email -> notify.email",
    )


def test_the_verifier_reads_no_mined_type() -> None:
    direct = read_program(
        "c1 = find_parcel(tracking=tracking)\n"
        "c2 = notify(email=c1.parcel.contact)\n"
        "return c2.notice_id\n"
    )
    checked = verify(direct, TOOLS, TASK)
    assert checked.count("proven") == len(checked.links)


OBJECT_ID = {"type": "string", "pattern": "^obj_[0-9]+$"}
GRASP_ID = {"type": "string", "pattern": "^grs_[0-9]+$"}


def _tool(name: str, arguments: dict[str, Any], result: dict[str, Any]) -> Contract:
    return read_contract(
        {
            "name": name,
            "inputSchema": {"type": "object", "properties": arguments, "required": [*arguments]},
            "outputSchema": {"type": "object", "properties": result, "required": [*result]},
        }
    )


ROBOT = {
    contract.name: contract
    for contract in (
        _tool(
            "detect_object",
            {"name": {"type": "string"}},
            {"object_id": OBJECT_ID, "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
        ),
        _tool(
            "estimate_pose",
            {"object_id": OBJECT_ID},
            {"distance_m": {"type": "number", "minimum": 0, "maximum": 20}},
        ),
        _tool(
            "grasp",
            {
                "object_id": OBJECT_ID,
                "distance_m": {"type": "number", "minimum": 0.2, "maximum": 0.6},
            },
            {"grasp_id": GRASP_ID},
        ),
    )
}
PICK = read_task("pick", {"name": {"type": "string"}}, GRASP_ID)
#: Calls an operator made: the distance a grasp took is the one the pose returned.
WATCHED: list[dict[str, Any]] = [
    {
        "tool": "detect_object",
        "arguments": {"name": "cup"},
        "result": {"object_id": "obj_1", "confidence": 0.91},
    },
    {"tool": "estimate_pose", "arguments": {"object_id": "obj_1"}, "result": {"distance_m": 0.41}},
    {
        "tool": "grasp",
        "arguments": {"object_id": "obj_1", "distance_m": 0.41},
        "result": {"grasp_id": "grs_1"},
    },
]
#: Calls a planned program made, passing a detection's confidence as the grasp's distance.
PLANNED: list[dict[str, Any]] = [
    {
        "tool": "detect_object",
        "arguments": {"name": "cup"},
        "result": {"object_id": "obj_2", "confidence": 0.43},
        "planned": True,
    },
    {
        "tool": "grasp",
        "arguments": {"object_id": "obj_2", "distance_m": 0.43},
        "result": {"grasp_id": "grs_2"},
        "planned": True,
    },
]


def test_a_planned_call_s_arguments_link_nothing() -> None:
    confidence = ("result", "detect_object.confidence")
    distance = ("argument", "grasp.distance_m")
    unmarked = [{key: value for key, value in call.items() if key != "planned"} for call in PLANNED]
    assert mine(WATCHED + unmarked, ROBOT).agree(confidence, distance)
    types = mine(WATCHED + PLANNED, ROBOT)
    assert not types.agree(confidence, distance)
    assert types.typed(("result", "detect_object.object_id"), ("argument", "grasp.object_id"))
    found = plan(ROBOT, PICK, types=types)
    assert found.program is not None
    assert "distance_m=c2.distance_m" in render(found.program)
    assert "c1.confidence -> grasp.distance_m" not in found.assumed


def test_a_task_input_no_log_shows_fills_no_argument_a_tool_s_result_was_seen_in() -> None:
    types = mine(WATCHED, ROBOT)
    name = ("input", "name")
    assert types.agree(name, ("argument", "detect_object.name"))
    assert not types.agree(name, ("argument", "grasp.object_id"))
    assert types.agree(("result", "detect_object.confidence"), ("argument", "redirect.target"))
    held = read_task("grasp a known object", {"object_id": OBJECT_ID}, GRASP_ID)
    untyped = plan(ROBOT, held).program
    assert untyped is not None
    assert "grasp(object_id=object_id" in render(untyped)
    typed = plan(ROBOT, held, types=types).program
    assert typed is not None
    assert "object_id=object_id" not in render(typed)
    named = read_task("grasp a named object", {"name": {"type": "string"}}, GRASP_ID)
    source = (
        "if name in grasp.object_id:\n"
        "    c1 = estimate_pose(object_id=name)\n"
        "    if c1.distance_m in grasp.distance_m:\n"
        "        c2 = grasp(object_id=name, distance_m=c1.distance_m)\n"
        "        return c2.grasp_id\n"
        "    stop(c1.distance_m)\n"
        "stop(name)\n"
    )
    notes = critique(source, ROBOT, named, types=types).notes
    assert (
        "assumed: the observed calls show estimate_pose.object_id taking a tool's result, and"
        " name is a task input they never show"
    ) in notes


def test_a_task_input_a_log_shows_is_typed_as_any_slot() -> None:
    held = read_task("grasp a known object", {"object_id": OBJECT_ID}, GRASP_ID)
    types = mine([{"inputs": {"object_id": "obj_1"}}, *WATCHED], ROBOT)
    assert types.agree(("input", "object_id"), ("argument", "grasp.object_id"))
    assert not types.agree(("input", "object_id"), ("argument", "grasp.distance_m"))
    found = plan(ROBOT, held, types=types)
    assert found.program is not None
    assert "object_id -> estimate_pose.object_id" in found.assumed
