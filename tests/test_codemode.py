from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from valgebra_planning.codemode import PREAMBLE, repair, stubs
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import ProgramError, critique, read_program
from valgebra_planning.planner import plan
from valgebra_planning.program import render

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
LISTING = json.loads((EXAMPLE / "tools.json").read_text(encoding="utf-8"))["tools"]
TOOLS = {contract.name: contract for contract in map(read_contract, LISTING)}
SETTLE = next(
    read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    if spec["name"] == "settle a parcel"
)
NAIVE = (
    "c1 = find_parcel(tracking=tracking)\n"
    "c2 = book_return(parcel_id=c1.parcel.parcel_id, state=c1.parcel.state, "
    "weight_g=c1.parcel.weight_g)\n"
    "return c2.label_id\n"
)


def test_the_stubs_are_python_and_carry_each_tool_s_own_schemas() -> None:
    text = stubs(TOOLS)
    module = ast.parse(text)
    functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    assert set(TOOLS) <= set(functions)
    for tool in LISTING:
        doc = ast.get_docstring(functions[tool["name"]]) or ""
        written = doc.split("Arguments, as JSON Schema: ", 1)[1].split("\n", 1)[0]
        assert json.loads(written) == tool["inputSchema"]


def test_a_model_s_program_is_repaired_by_the_critique_it_is_handed() -> None:
    planned = plan(TOOLS, SETTLE).program
    assert planned is not None
    told: list[str | None] = []

    def propose(feedback: str | None) -> str:
        told.append(feedback)
        return NAIVE if feedback is None else render(planned)

    repaired = repair(propose, TOOLS, SETTLE)
    assert repaired.accepted
    assert len(repaired.rounds) == 2
    assert told[1] is not None
    assert "refuted: c1.parcel.state <= book_return.state" in told[1]


def test_a_program_the_reader_refuses_is_handed_back_as_the_reason() -> None:
    told: list[str | None] = []

    def propose(feedback: str | None) -> str:
        told.append(feedback)
        return "import os\n" if feedback is None else NAIVE

    repaired = repair(propose, TOOLS, SETTLE, rounds=2)
    assert not repaired.accepted
    assert told[1] is not None
    assert told[1].startswith("the program is not read: line 1")


def test_text_python_cannot_parse_is_handed_back_as_the_reason() -> None:
    # A proposal that is no Python spends a round as any program the reader refuses does,
    # its reason naming the proposal's own line.
    told: list[str | None] = []

    def propose(feedback: str | None) -> str:
        told.append(feedback)
        return "c1 = find_parcel(\nreturn c1\n" if feedback is None else NAIVE

    repaired = repair(propose, TOOLS, SETTLE, rounds=2)
    assert len(repaired.rounds) == 2
    assert told[1] is not None
    assert told[1].startswith("the program is not read: line 1: '(' was never closed")


def test_the_rounds_run_out_on_a_program_never_mended() -> None:
    repaired = repair(lambda _: NAIVE, TOOLS, SETTLE, rounds=3)
    assert not repaired.accepted
    assert len(repaired.rounds) == 3


OBJECT_ID = {"type": "string", "pattern": "^obj_[0-9]+$"}
REACH = {"type": "number", "minimum": 0.2, "maximum": 0.6}


def _closed(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


METERS = _closed({"unit": {"const": "m"}, "value": {"type": "number", "minimum": 0, "maximum": 20}})
PICKING = {
    contract.name: contract
    for contract in map(
        read_contract,
        [
            {
                "name": "detect_object",
                "inputSchema": _closed({"name": {"type": "string"}}),
                "outputSchema": _closed(
                    {"status": {"enum": ["found", "not_found"]}, "object_id": OBJECT_ID}
                ),
            },
            {
                "name": "estimate_pose",
                "inputSchema": _closed({"object_id": OBJECT_ID}),
                "outputSchema": _closed({"distance": METERS}),
            },
            {
                "name": "grasp",
                "inputSchema": _closed(
                    {
                        "object_id": OBJECT_ID,
                        "distance": {
                            **METERS,
                            "properties": {"unit": {"const": "m"}, "value": REACH},
                        },
                    }
                ),
                "outputSchema": _closed(
                    {"grasp_id": {"type": "string", "pattern": "^grs_[0-9]+$"}}
                ),
            },
        ],
    )
}
OUT_OF_REACH = {"properties": {"distance": {"not": {"properties": {"value": REACH}}}}}
NOT_FOUND = {"properties": {"status": {"const": "not_found"}}}
GRASPED = {"type": "string", "pattern": "^grs_[0-9]+$"}
PLANNED = """\
c1 = detect_object(name=name)
c2 = estimate_pose(object_id=c1.object_id)
if c2.distance in grasp.distance:
    c3 = grasp(object_id=c1.object_id, distance=c2.distance)
    return c3.grasp_id
else:
    stop(c2.distance)
"""
FOUND_FIRST = """\
c1 = detect_object(name=name)
if c1.status in ("found",):
    c2 = estimate_pose(object_id=c1.object_id)
    if c2.distance in grasp.distance:
        c3 = grasp(object_id=c1.object_id, distance=c2.distance)
        return c3.grasp_id
    else:
        stop(c2.distance)
else:
    stop(c1.status)
"""
IN_REACH_STOPPED = """\
c1 = detect_object(name=name)
c2 = estimate_pose(object_id=c1.object_id)
if c2.distance in grasp.distance:
    stop(c2.distance)
else:
    c3 = estimate_pose(object_id=c1.object_id)
    if c3.distance in grasp.distance:
        c4 = grasp(object_id=c1.object_id, distance=c3.distance)
        return c4.grasp_id
    else:
        stop(c3.distance)
"""
NOTHING_GRASPED = "c1 = detect_object(name=name)\nstop(c1)\n"


def _picking(stops: dict[str, Any]) -> Any:
    return read_task("pick", {"name": {"type": "string"}}, GRASPED, stops=stops)


@pytest.mark.parametrize(
    ("source", "stops", "weak"),
    [
        (PLANNED, {}, False),
        (PLANNED, {"estimate_pose": OUT_OF_REACH}, True),
        (FOUND_FIRST, {"estimate_pose": OUT_OF_REACH}, False),
        (FOUND_FIRST, {"estimate_pose": OUT_OF_REACH, "detect_object": NOT_FOUND}, True),
        (IN_REACH_STOPPED, {"estimate_pose": OUT_OF_REACH}, False),
        (NOTHING_GRASPED, {"estimate_pose": OUT_OF_REACH, "detect_object": NOT_FOUND}, False),
    ],
    ids=["no-stops", "planned", "not-found-untolerated", "both", "in-reach", "nothing"],
)
def test_a_weak_program_stops_only_where_its_task_tolerates(
    source: str, stops: dict[str, Any], *, weak: bool
) -> None:
    checked = critique(source, PICKING, _picking(stops))
    assert not checked.accepted
    assert checked.verified
    assert checked.weak is weak


def test_repair_accepts_a_weak_program_where_asked_and_stays_strict_by_default() -> None:
    task = _picking({"estimate_pose": OUT_OF_REACH})

    def proposing() -> Any:
        proposals = iter([NOTHING_GRASPED, PLANNED, PLANNED])
        return lambda _: next(proposals)

    weakly = repair(proposing(), PICKING, task, accept="weak")
    assert len(weakly.rounds) == 2
    # The rounds end on a program the weak mode meets, which strict acceptance reads as not met.
    assert weakly.met
    assert not weakly.accepted
    strictly = repair(proposing(), PICKING, task)
    assert len(strictly.rounds) == 3
    assert not strictly.accepted
    assert not strictly.met
    note = critique(PLANNED, PICKING, task).notes
    assert note == ("uncovered: c2.distance (at else); the task tolerates it",)


def test_the_preamble_s_program_is_one_the_critic_reads_and_proves() -> None:
    written = PREAMBLE.split("A program for another task reads:\n\n", 1)[1].split('"""', 1)[0]
    source = "\n".join(line.removeprefix("    ") for line in written.strip("\n").splitlines())
    status = {"enum": ["shipped", "lost"]}
    order = {"type": "string", "pattern": "^ord_[0-9]+$"}
    shop = {
        contract.name: contract
        for contract in map(
            read_contract,
            [
                {
                    "name": "find_order",
                    "inputSchema": _closed({"order_id": order}),
                    "outputSchema": _closed({"order_id": order, "status": status}),
                },
                {
                    "name": "send_notice",
                    "inputSchema": _closed({"order_id": order, "status": {"const": "shipped"}}),
                    "outputSchema": _closed({"notice_id": {"type": "string"}}),
                },
            ],
        )
    }
    task = read_task("notify", {"order_id": order}, {"type": "string"}, stops={"find_order": {}})
    checked = critique(source, shop, task)
    assert checked.verified
    assert checked.weak


@pytest.mark.parametrize(
    ("source", "says"),
    [
        (
            "c1 = find_parcel(tracking=tracking)\nnotify(email=c1.parcel.contact)\nreturn 1\n",
            "line 2: a call's result is bound to a name: `c2 = notify(...)`",
        ),
        (
            "from typing import Any\nc1 = find_parcel(tracking=tracking)\nreturn 1\n",
            "line 1: the tools are given: write the program alone",
        ),
        (
            "c1 = find_parcel(tracking=tracking)\nstop(c1)\nreturn 1\n",
            "line 2: a `stop` ends its path",
        ),
        (
            "c1 = find_parcel(tracking=tracking)\nreturn c1.parcels[0]\n",
            "an array's items by a loop, `for item in c1.items:`",
        ),
    ],
    ids=["bare-call", "import", "stop-first", "index"],
)
def test_a_program_error_names_the_form_the_reader_reads(source: str, says: str) -> None:
    with pytest.raises(ProgramError) as refused:
        read_program(source)
    assert says in str(refused.value)
