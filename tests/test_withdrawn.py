"""A fresh rule may name the calls that withdraw what its sources returned.

``"fresh": true`` withdraws a source's value at every call that may act. A list of tools and
hints is the author's claim that only those change what the sources read (Mansoor et al. 2026,
§4.1): every link it decides names it, and ``check`` names each tool that may act it leaves out.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, types
from mcp.server.lowlevel import Server

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.flows import check_flow, read_flow
from valgebra_planning.library import read_library
from valgebra_planning.meaning import findings
from valgebra_planning.planner import plan
from valgebra_planning.policy import read_policy
from valgebra_planning.program import render
from valgebra_planning.proxy import proxy
from valgebra_planning.run import run
from valgebra_planning.verify import verify

ROBOT = Path(__file__).parent.parent / "examples" / "robot-skills"
LIBRARY = read_library(ROBOT)
MEASURED = {
    "name": "measured",
    "sources": ["estimate_pose"],
    "sink": "grasp.distance_m",
    "only": True,
    "key": "object_id",
}
GIVEN = {"o": "obj_1", "p": "obj_2", "a": "mug", "b": "bottle", "d": 0.4}
SPOKEN = (
    "c1 = estimate_pose(object_id=o)\nc2 = {}\n"
    "c3 = grasp(object_id=o, distance_m=c1.distance_m)\nreturn c3.grasp_id\n"
)


def _rule(fresh: object) -> Any:
    return read_flow({**MEASURED, "fresh": fresh})


def test_a_fresh_rule_reads_true_or_a_list_of_tools_and_hints() -> None:
    assert (_rule(True).fresh, _rule(True).withdrawn_by) == (True, frozenset())
    listed = _rule(["walk_to", "destructiveHint"])
    assert (listed.fresh, listed.withdrawn_by) == (True, {"walk_to", "destructiveHint"})
    assert listed.withdraws("walk_to", frozenset())
    assert listed.withdraws("grasp", frozenset({"destructiveHint"}))
    assert not listed.withdraws("say", frozenset())
    for given in ([], [1], "walk_to"):
        with pytest.raises(TypeError, match="'fresh' is a boolean or a list"):
            _rule(given)
    with pytest.raises(ValueError, match="'fresh' names 'walk', no tool listed and no hint"):
        check_flow(_rule(["walk"]), LIBRARY.tools)


@pytest.mark.parametrize(
    ("between", "fresh", "verdict"),
    [
        ("say(text=a)", True, "refuted"),
        ("say(text=a)", ["walk_to"], "proven"),
        ("walk_to(object_id=o)", ["walk_to"], "refuted"),
    ],
    ids=["any-act", "an-act-the-list-leaves", "an-act-it-names"],
)
def test_a_listed_rule_is_withdrawn_by_the_calls_it_names(
    between: str, fresh: object, verdict: str
) -> None:
    """Speech is an act, and no reading of a distance changes for it: under the list, a
    distance read before it reaches the grasp, the link naming the list; a walk still
    withdraws it, and every call that may act does under ``true``."""
    rule, source = _rule(fresh), SPOKEN.format(between)
    task = LIBRARY.problem("pick").task
    links = verify(read_program(source), LIBRARY.tools, task, flows=[rule]).links
    (fresh_link,) = [link for link in links if "was returned after every call" in link.claim]
    assert fresh_link.verdict == verdict
    if verdict == "proven":
        assert fresh_link.assumed == ("flow measured: only walk_to change what estimate_pose read",)
    answers = {
        "estimate_pose": {"distance_m": 0.3},
        "say": {"status": "spoken"},
        "walk_to": {"object_id": "obj_1", "arrived": True},
        "grasp": {"grasp_id": "g_1"},
    }
    tools = {name: (lambda name=name, **_: answers[name]) for name in answers}
    ran = run(read_program(source), LIBRARY.tools, task, GIVEN, tools, flows=[rule])
    assert (ran.outcome == "returned") is (verdict == "proven")


def test_the_planner_reads_a_listed_rule_after_a_call_that_may_have_acted() -> None:
    # A grasp that failed may have acted; under a list leaving it out, the pinch after it may
    # take the distance read before it, where `true` measures again first.
    listed = json.loads((ROBOT / "tools.json").read_text(encoding="utf-8"))["tools"]
    pinch = {**next(tool for tool in listed if tool["name"] == "grasp"), "name": "pinch"}
    hands = {name: LIBRARY.tools[name] for name in ("estimate_pose", "grasp", "walk_to")}
    hands["pinch"] = read_contract(pinch)
    pick = read_task("pick", {"o": {"type": "string"}}, LIBRARY.problem("pick").task.goal_schema)

    def planned(fresh: object) -> str:
        rules = [
            read_flow({**MEASURED, "sink": f"{sink}.distance_m", "name": sink, "fresh": fresh})
            for sink in ("grasp", "pinch")
        ]
        found = plan(hands, pick, depth=6, errors=True, flows=rules)
        assert found.program is not None
        checked = verify(found.program, hands, pick, errors=True, flows=rules)
        assert [link for link in checked.links if link.verdict != "proven"] == []
        return render(found.program)

    assert planned(True).count("estimate_pose") == 2
    assert planned(["walk_to"]).count("estimate_pose") == 1


def test_the_planner_reads_again_after_a_call_the_list_names() -> None:
    """A distance read before the walk a policy asks for is read again before the grasp."""
    hands = {name: LIBRARY.tools[name] for name in ("estimate_pose", "grasp", "walk_to")}
    pick = read_task("pick", {"o": {"type": "string"}}, LIBRARY.problem("pick").task.goal_schema)
    walked = read_policy(
        {
            "name": "walked to first",
            "initial": "away",
            "bad": ["violated"],
            "transitions": [
                {"from": "away", "tool": "walk_to", "to": "there"},
                {"from": "away", "tool": "grasp", "to": "violated"},
            ],
        }
    )

    def planned(fresh: object) -> list[str]:
        rules = [_rule(fresh)]
        found = plan(hands, pick, depth=5, policies=[walked], flows=rules)
        assert found.program is not None
        checked = verify(found.program, hands, pick, policies=[walked], flows=rules)
        assert [link for link in checked.links if link.verdict != "proven"] == []
        return [
            line.split(" = ")[1].split("(")[0] for line in render(found.program).split("\n")[:-1]
        ]

    assert planned(["walk_to"]) == ["walk_to", "estimate_pose", "grasp"]
    assert planned(["grasp"]) == ["estimate_pose", "walk_to", "grasp"]


def _replayed(fresh: object, between: str | None = None) -> Any:
    """Read a pose, grasp, the robot answering "replayed", then grasp again: the second answer."""
    tried: list[str] = []
    listing = json.loads((ROBOT / "tools.json").read_text(encoding="utf-8"))["tools"]

    async def listed(_: Any, __: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[types.Tool.model_validate(t) for t in listing])

    async def called(_: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        tried.append(params.name)
        if params.name == "grasp" and tried.count("grasp") == 1:
            text = types.TextContent(type="text", text="replayed")
            return types.CallToolResult(content=[text], is_error=True)
        value = {
            "estimate_pose": {"distance_m": 0.3},
            "grasp": {"grasp_id": "g_1"},
            "walk_to": {"object_id": "obj_1", "arrived": True},
        }[params.name]
        return types.CallToolResult(content=[], structured_content=value)

    async def session() -> Any:
        async with Client(Server("robot", on_list_tools=listed, on_call_tool=called)) as up:
            rule = _rule(fresh)
            front = proxy(up, (await up.list_tools()).tools, LIBRARY.tools, flows=[rule])
            async with Client(front) as client:
                await client.call_tool("estimate_pose", {"object_id": "obj_1"})
                grasp = {"object_id": "obj_1", "distance_m": 0.3}
                await client.call_tool("grasp", grasp)
                if between:
                    await client.call_tool(between, {"object_id": "obj_1"})
                return await client.call_tool("grasp", grasp)

    return asyncio.run(session())


def test_a_replayed_grasp_keeps_the_reading_a_listed_rule_does_not_withdraw() -> None:
    """The grasp the robot answered "replayed" may have acted, so `true` withdraws the pose it
    read and refuses the grasp made again; a rule naming the walk keeps it, and a walk between
    still withdraws it."""
    refused = _replayed(fresh=True)
    assert refused.is_error
    assert "since the last call that may act" in refused.content[0].text
    assert not _replayed(fresh=["walk_to"]).is_error
    walked = _replayed(fresh=["walk_to"], between="walk_to")
    assert walked.is_error
    assert "since the last call to walk_to" in walked.content[0].text


def test_check_names_each_tool_that_may_act_a_list_leaves_out(tmp_path: Path) -> None:
    shutil.copytree(ROBOT, tmp_path, dirs_exist_ok=True)
    flows = json.loads((tmp_path / "flows.json").read_text(encoding="utf-8"))
    flows["flows"][0]["fresh"] = ["walk_to"]
    (tmp_path / "flows.json").write_text(json.dumps(flows), encoding="utf-8")
    notes = {f.finding for f in findings(read_library(tmp_path)) if f.where == "flow measured"}
    acting = {"grasp", "move_joint", "say", "walk_to"}
    assert notes == {
        f"{tool} may act, and its calls are taken to leave what estimate_pose read"
        for tool in acting - {"walk_to"}
    }
