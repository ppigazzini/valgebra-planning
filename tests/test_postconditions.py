from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from valgebra_planning.cli import main
from valgebra_planning.contracts import Contract, read_contract, read_postcondition, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.library import Library, read_library
from valgebra_planning.policy import read_policy
from valgebra_planning.run import ToolError, blame, run
from valgebra_planning.vacuity import vacuities
from valgebra_planning.verify import verify

OBJECT = {"type": "string", "pattern": "^obj_[0-9]+$"}
GRASPED = {"type": "string", "pattern": "^grs_[0-9]+$"}
REACH = {"type": "number", "minimum": 0.2, "maximum": 0.6}
SEEING = {"readOnlyHint": True}


def _spec(name: str, arguments: dict[str, Any], result: dict[str, Any], hints: Any) -> Any:
    spec = {
        "name": name,
        "inputSchema": {"type": "object", "properties": arguments, "required": [*arguments]},
        "outputSchema": {
            "type": "object",
            "properties": result,
            "required": [*result],
            "additionalProperties": False,
        },
    }
    return spec | ({"annotations": hints} if hints else {})


SPECS = [
    _spec("detect_object", {"name": {"type": "string"}}, {"object_id": OBJECT}, SEEING),
    _spec(
        "estimate_pose",
        {"object_id": OBJECT},
        {"distance": {"type": "number", "minimum": 0, "maximum": 20}},
        SEEING,
    ),
    _spec("walk_to", {"object_id": OBJECT}, {"arrived": {"type": "boolean"}}, None),
    _spec("say", {"text": {"type": "string"}}, {"said": {"type": "boolean"}}, None),
    _spec("point_at", {"object_id": OBJECT}, {"pointed": {"type": "boolean"}}, None),
    _spec("grasp", {"object_id": OBJECT, "distance": REACH}, {"grasp_id": GRASPED}, None),
]
# A root keyword the walk does not read argument by argument: the record is asked whole.
SPECS[-1]["inputSchema"]["minProperties"] = 2
TOOLS: dict[str, Contract] = {spec["name"]: read_contract(spec) for spec in SPECS}
ARRIVED = {
    "after": "walk_to",
    "when": {"properties": {"arrived": {"const": True}}, "required": ["arrived"]},
    "read": "estimate_pose",
    "with": {"object_id": "object_id"},
    "shows": {"properties": {"distance": REACH}, "required": ["distance"]},
}
POST = read_postcondition(ARRIVED, TOOLS)
NAMED = "the postcondition of walk_to on estimate_pose"
INPUTS = {
    "name": {"type": "string"},
    "names": {"type": "array", "items": {"type": "string"}},
    "objects": {
        "type": "array",
        "items": {"type": "object", "properties": {"object_id": OBJECT}, "required": ["object_id"]},
    },
}
TASK = read_task("pick", INPUTS, GRASPED)
CLAIM = "c3.distance <= grasp.distance"

DIRECT = (
    "c1 = detect_object(name=name)\n"
    "c2 = walk_to(object_id=c1.object_id)\n"
    "if c2.arrived == True:\n"
    "    c3 = estimate_pose(object_id=c1.object_id)\n"
    "    c4 = grasp(object_id=c1.object_id, distance=c3.distance)\n"
    "    return c4.grasp_id\n"
    "stop(c2.arrived)\n"
)


ERRORED = (
    "c1 = detect_object(name=name)\n"
    "try:\n"
    "    c2 = walk_to(object_id=c1.object_id)\n"
    "except ToolError:\n"
    "    c3 = estimate_pose(object_id=c1.object_id)\n"
    "    c4 = grasp(object_id=c1.object_id, distance=c3.distance)\n"
    "    return c4.grasp_id\n"
    "stop(c2.arrived)\n"
)
AGAIN = (
    "c1 = detect_object(name=name)\n"
    "c2 = walk_to(object_id=c1.object_id)\n"
    "if c2.arrived == True:\n"
    "    try:\n"
    "        c5 = walk_to(object_id=c1.object_id)\n"
    "    except ToolError:\n"
    "        c3 = estimate_pose(object_id=c1.object_id)\n"
    "        c4 = grasp(object_id=c1.object_id, distance=c3.distance)\n"
    "        return c4.grasp_id\n"
    "    stop(c5.arrived)\n"
    "stop(c2.arrived)\n"
)
BETWEEN = DIRECT.replace(
    "    c3 = estimate_pose", "    c9 = detect_object(name=name)\n    c3 = estimate_pose"
)
LOOPED = (
    "c1 = detect_object(name=name)\n"
    "c2 = walk_to(object_id=c1.object_id)\n"
    "if c2.arrived == True:\n"
    "    for n in names:\n"
    "        c3 = estimate_pose(object_id=c1.object_id)\n"
    "        c4 = grasp(object_id=c1.object_id, distance=c3.distance)\n"
    "    stop(c2.arrived)\n"
    "stop(c2.arrived)\n"
)


def _links(source: str, claim: str = CLAIM, *posts: Any) -> list[Any]:
    checked = verify(read_program(source), TOOLS, TASK, postconditions=posts)
    return [link for link in checked.links if link.claim == claim]


def test_a_read_after_the_call_returned_in_when_is_read_in_what_it_shows() -> None:
    (unread,) = _links(DIRECT)
    assert unread.verdict == "refuted"
    (shown,) = _links(DIRECT, CLAIM, POST)
    assert shown.verdict == "proven"
    assert NAMED in shown.assumed
    (record,) = _links(DIRECT, "the arguments of grasp <= its input schema", POST)
    assert record.verdict == "proven"
    assert NAMED in record.assumed


def test_a_read_only_call_between_the_call_and_the_read_leaves_it_covered() -> None:
    (link,) = _links(BETWEEN, CLAIM, POST)
    assert link.verdict == "proven"


def test_a_policy_splitting_the_read_s_result_keeps_the_postcondition_named() -> None:
    near = read_policy(
        {
            "name": "close or far",
            "initial": "seen",
            "bad": [],
            "transitions": [
                {
                    "from": "seen",
                    "tool": "estimate_pose",
                    "result": {"properties": {"distance": {"maximum": 0.3}}},
                    "to": "close",
                },
            ],
        }
    )
    checked = verify(read_program(DIRECT), TOOLS, TASK, policies=[near], postconditions=[POST])
    links = [link for link in checked.links if link.claim == CLAIM]
    assert len(links) == 2
    assert all(link.verdict == "proven" and NAMED in link.assumed for link in links)


def test_a_branch_the_read_s_shown_set_misses_is_no_path() -> None:
    tested = DIRECT.replace(
        "    c4 = grasp(object_id=c1.object_id, distance=c3.distance)\n    return c4.grasp_id\n",
        "    if c3.distance in grasp.distance:\n"
        "        c4 = grasp(object_id=c1.object_id, distance=c3.distance)\n"
        "        return c4.grasp_id\n"
        "    stop(c3.distance)\n",
    )
    gaps = {gap.ref for gap in verify(read_program(tested), TOOLS, TASK).gaps}
    assert gaps == {"c3.distance", "c2.arrived"}
    shown = verify(read_program(tested), TOOLS, TASK, postconditions=[POST])
    assert {gap.ref for gap in shown.gaps} == {"c2.arrived"}


@pytest.mark.parametrize(
    "source",
    [
        DIRECT.replace("if c2.arrived == True:\n    ", "")
        .replace("\n    ", "\n")
        .replace("stop(c2.arrived)\n", ""),
        DIRECT.replace("estimate_pose(object_id=c1.object_id)", 'estimate_pose(object_id="obj_9")'),
        DIRECT.replace("    c3 = estimate_pose", "    c9 = say(text=name)\n    c3 = estimate_pose"),
        DIRECT.replace(
            "    c3 = estimate_pose", "    c1 = detect_object(name=name)\n    c3 = estimate_pose"
        ),
        ERRORED,
        AGAIN,
        LOOPED,
    ],
    ids=[
        "when-unproven",
        "another-value",
        "another-call-that-may-act",
        "its-name-bound-again",
        "after-an-error",
        "after-an-error-of-a-second-walk",
        "in-a-loop-whose-body-acts",
    ],
)
def test_a_read_no_postcondition_covers_is_read_as_its_contract_says(source: str) -> None:
    links = _links(source, CLAIM, POST)
    assert links
    assert all(link.verdict == "refuted" for link in links)
    assert all(NAMED not in link.assumed for link in links)


def test_a_loop_item_bound_under_a_name_the_call_read_is_another_value() -> None:
    task = read_task("near", INPUTS, REACH)
    source = (
        "c1 = detect_object(name=name)\n"
        "c2 = walk_to(object_id=c1.object_id)\n"
        "if c2.arrived == True:\n"
        "    for c1 in objects:\n"
        "        c3 = estimate_pose(object_id=c1.object_id)\n"
        "        return c3.distance\n"
        "    stop(c2.arrived)\n"
        "stop(c2.arrived)\n"
    )
    plain = source.replace("for c1 in objects", "for o in objects")
    checked = verify(read_program(plain), TOOLS, task, postconditions=[POST])
    returned = [link for link in checked.links if link.claim == "return c3.distance <= goal"]
    assert [link.verdict for link in returned] == ["proven"]
    shadowed = verify(read_program(source), TOOLS, task, postconditions=[POST])
    returned = [link for link in shadowed.links if link.claim == "return c3.distance <= goal"]
    assert [link.verdict for link in returned] == ["refuted"]


def _tools(*, arrived: bool, distance: float, failing: int = 0) -> dict[str, Any]:
    """Return the tools, ``walk_to`` answering with an error from its ``failing``-th call on."""
    walks: list[int] = []

    def walk_to(**_: Any) -> Any:
        walks.append(1)
        if failing and len(walks) >= failing:
            message = "no answer"
            raise ToolError(message)
        return {"arrived": arrived}

    return {
        "detect_object": lambda **_: {"object_id": "obj_1"},
        "walk_to": walk_to,
        "estimate_pose": lambda **_: {"distance": distance},
        "say": lambda **_: {"said": True},
        "grasp": lambda **_: {"grasp_id": "grs_1"},
    }


def test_a_run_refuses_a_read_outside_what_a_postcondition_says_it_shows() -> None:
    inputs = {"name": "cup", "names": [], "objects": []}
    shown = run(
        read_program(DIRECT),
        TOOLS,
        TASK,
        inputs,
        _tools(arrived=True, distance=0.4),
        postconditions=[POST],
    )
    assert shown.ok
    assert shown.value == "grs_1"
    far = run(
        read_program(DIRECT),
        TOOLS,
        TASK,
        inputs,
        _tools(arrived=True, distance=5.0),
        postconditions=[POST],
    )
    assert far.why is not None
    assert (far.why.check, far.why.tool) == ("postcondition", "estimate_pose")
    assert far.refused is not None
    assert far.refused.startswith(f"estimate_pose returned a value outside {NAMED} (distance: ")
    assert blame(far, TOOLS, proven=True) == (
        "estimate_pose read a value a postcondition of the library excludes: its declaration"
    )
    unposted = run(read_program(DIRECT), TOOLS, TASK, inputs, _tools(arrived=True, distance=5.0))
    assert unposted.why is not None
    assert unposted.why.check == "arguments"


@pytest.mark.parametrize(
    ("source", "failing"),
    [
        (
            DIRECT.replace(
                "estimate_pose(object_id=c1.object_id)", 'estimate_pose(object_id="obj_9")'
            ),
            0,
        ),
        (
            DIRECT.replace(
                "    c3 = estimate_pose", "    c9 = say(text=name)\n    c3 = estimate_pose"
            ),
            0,
        ),
        (
            ERRORED,
            1,
        ),
        (AGAIN, 2),
    ],
    ids=["another-value", "another-call-that-may-act", "after-an-error", "after-a-second-walk"],
)
def test_a_run_holds_a_read_no_postcondition_covers_to_its_contract_alone(
    source: str, failing: int
) -> None:
    inputs = {"name": "cup", "names": [], "objects": []}
    tools = _tools(arrived=True, distance=5.0, failing=failing)
    ended = run(read_program(source), TOOLS, TASK, inputs, tools, postconditions=[POST])
    assert ended.why is not None
    assert (ended.why.check, ended.why.tool) == ("arguments", "grasp")


def test_a_run_reads_a_read_after_a_read_only_call_as_covered() -> None:
    inputs = {"name": "cup", "names": [], "objects": []}
    tools = _tools(arrived=True, distance=5.0)
    ended = run(read_program(BETWEEN), TOOLS, TASK, inputs, tools, postconditions=[POST])
    assert ended.why is not None
    assert (ended.why.check, ended.why.tool) == ("postcondition", "estimate_pose")


def test_a_run_reads_no_postcondition_where_the_call_returned_outside_when() -> None:
    inputs = {"name": "cup", "names": [], "objects": []}
    source = DIRECT.replace("if c2.arrived == True:", "if c2.arrived == False:")
    ended = run(
        read_program(source),
        TOOLS,
        TASK,
        inputs,
        _tools(arrived=False, distance=5.0),
        postconditions=[POST],
    )
    assert ended.why is not None
    assert (ended.why.check, ended.why.tool) == ("arguments", "grasp")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"after": "fly"}, "'fly' is no tool listed"),
        ({"read": None}, "None is no tool listed"),
        ({"with": {}}, "'with' names each argument of estimate_pose"),
        ({"with": {"object": "object_id"}}, "'object' is no argument of estimate_pose"),
        ({"with": {"object_id": "target"}}, "'target' is no argument of walk_to"),
        ({"then": True}, "'then'"),
    ],
    ids=["after-no-tool", "read-no-tool", "with-empty", "with-mine", "with-theirs", "unread-key"],
)
def test_a_postcondition_naming_what_its_tools_lack_is_refused(
    change: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        read_postcondition(ARRIVED | change, TOOLS)


@pytest.mark.parametrize("key", ["when", "shows"])
def test_a_postcondition_whose_sets_are_no_schemas_is_refused(key: str) -> None:
    with pytest.raises(TypeError, match=f"{key!r} is a JSON Schema, not str"):
        read_postcondition(ARRIVED | {key: "acted"}, TOOLS)


def test_a_postcondition_with_no_when_covers_every_result() -> None:
    spec = {key: value for key, value in ARRIVED.items() if key != "when"}
    untested = (
        DIRECT.replace("if c2.arrived == True:\n    ", "")
        .replace("\n    ", "\n")
        .replace("stop(c2.arrived)\n", "")
    )
    always = read_postcondition(spec, TOOLS)
    (link,) = _links(untested, CLAIM, always)
    assert link.verdict == "proven"
    pointed = untested.replace(
        "c3 = estimate_pose", "c9 = point_at(object_id=c1.object_id)\nc3 = estimate_pose"
    )
    (link,) = _links(pointed, CLAIM, always)
    assert link.verdict == "refuted"


def test_a_field_below_an_array_s_items_takes_any_value_whatever_shows_names() -> None:
    items = {"properties": {"seen": {"properties": {"[]": False}}}, "required": ["seen"]}
    post = read_postcondition(ARRIVED | {"shows": items}, TOOLS)
    assert post.field(("seen", "[]")).is_valid("obj_1")
    assert not post.field(("seen",)).is_valid({"[]": 1})


def test_a_postcondition_covering_nothing_or_narrowing_nothing_is_found() -> None:
    never = read_postcondition(
        ARRIVED | {"when": {"properties": {"arrived": {"const": "yes"}}, "required": ["arrived"]}},
        TOOLS,
    )
    impossible = read_postcondition(
        ARRIVED
        | {"shows": {"properties": {"distance": {"type": "string"}}, "required": ["distance"]}},
        TOOLS,
    )
    idle = read_postcondition(ARRIVED | {"shows": {"type": "object"}}, TOOLS)
    library = Library(TOOLS, (), postconditions=(never, impossible, idle, POST))
    found = {(finding.finding, finding.certainty) for finding in vacuities(library)}
    where = {finding.where for finding in vacuities(library)}
    assert ("its when meets no result of walk_to", "proven") in found
    assert ("its shows meets no result of estimate_pose", "proven") in found
    assert ("its shows holds every result of estimate_pose", "proven") in found
    assert where == {"postcondition of walk_to on estimate_pose"}
    assert len(found) == 3


def test_a_library_reads_its_postconditions_and_each_task_is_held_to_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "tools.json").write_text(json.dumps({"tools": SPECS, "postconditions": [ARRIVED]}))
    task = {"name": "pick", "inputs": INPUTS, "goal": GRASPED}
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [task]}))
    library = read_library(tmp_path)
    assert [post.name for post in library.postconditions] == [NAMED]
    assert library.problem("pick").postconditions == library.postconditions
    (tmp_path / "pick.py").write_text(DIRECT)
    main(["critique", str(tmp_path), "--task", "pick", str(tmp_path / "pick.py")])
    out = capsys.readouterr().out
    assert f"assumed: {NAMED}" in out
    assert "refuted" not in out
    (tmp_path / "tools.json").write_text(json.dumps({"tools": SPECS, "postconditions": {}}))
    with pytest.raises(TypeError, match="'postconditions' is a list, not dict"):
        read_library(tmp_path)
