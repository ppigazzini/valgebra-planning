"""A preempting call, as a robot's stop, is sent past the order a policy's calls hold.

A call a policy or an ordered rule reads holds the proxy's gate from its check to its result, so
a stop would wait behind the walk it must end. A tool of ``preempts`` is sent at once, and the
gate holds every order the two may take (Bertoli et al. 2006, §3): the stop acting before the
walk's effect, between its effect and its result, and after its result.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp import Client, types
from mcp.server.lowlevel import Server

from valgebra_planning.contracts import read_contract
from valgebra_planning.flows import read_flow
from valgebra_planning.policy import read_policy
from valgebra_planning.proxy import Gate, proxy


def _tool(name: str, inputs: dict[str, Any], outputs: dict[str, Any], *, acts: bool) -> Any:
    def closed(properties: dict[str, Any]) -> dict[str, Any]:
        required = list(properties)
        return {"type": "object", "properties": properties, "required": required}

    return {
        "name": name,
        "inputSchema": {**closed(inputs), "additionalProperties": False},
        "outputSchema": {**closed(outputs), "additionalProperties": False},
        "annotations": {"readOnlyHint": not acts},
    }


LISTING = [
    _tool("walk_to", {}, {"arrived": {"type": "boolean"}}, acts=True),
    _tool("stop", {}, {"stopped": {"type": "boolean"}}, acts=True),
    _tool("grasp", {"distance": {"type": "number"}}, {"status": {"const": "grasped"}}, acts=True),
    _tool("pose", {}, {"distance": {"type": "number"}}, acts=False),
]
CONTRACTS = {tool["name"]: read_contract(tool) for tool in LISTING}
ANSWERS = {
    "walk_to": {"arrived": True},
    "stop": {"stopped": True},
    "grasp": {"status": "grasped"},
    "pose": {"distance": 0.4},
}
#: An answer the server gives with ``isError`` set.
FAILED = object()

MEASURED = read_flow(
    {"name": "measured", "sources": ["pose"], "sink": "grasp.distance", "only": True, "fresh": True}
)
MOVING = read_policy(
    {
        "name": "no grasp while moving",
        "initial": "idle",
        "bad": ["violated"],
        "transitions": [
            {"from": "idle", "tool": "walk_to", "to": "moving"},
            {"from": "moving", "tool": "stop", "to": "still"},
            {"from": "moving", "tool": "grasp", "to": "violated"},
        ],
    }
)
DOCKING = read_policy(
    {
        "name": "no grasp once docked",
        "initial": "idle",
        "bad": ["violated"],
        "transitions": [
            {"from": "idle", "tool": "walk_to", "to": "moving"},
            {
                "from": "moving",
                "tool": "walk_to",
                "result": {"properties": {"arrived": {"const": True}}},
                "to": "arrived",
            },
            {"from": "moving", "tool": "stop", "to": "halted"},
            {"from": "arrived", "tool": "stop", "to": "docked"},
            {"from": "docked", "tool": "grasp", "to": "violated"},
        ],
    }
)
WALKED_FIRST = read_policy(
    {
        "name": "a grasp follows a walk",
        "initial": "idle",
        "bad": ["violated"],
        "transitions": [
            {"from": "idle", "tool": "walk_to", "to": "moving"},
            {"from": "moving", "tool": "stop", "to": "still"},
            {"from": "idle", "tool": "grasp", "to": "violated"},
        ],
    }
)
GRASP = ("grasp", {"distance": 0.4})


def _robot(answers: dict[str, Any]) -> Server[Any]:
    """Serve the robot's tools, each answering from ``answers`` after the delay it names."""

    async def listed(_: Any, __: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[types.Tool.model_validate(t) for t in LISTING])

    async def called(_: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        value, seconds = answers[params.name]
        await asyncio.sleep(seconds)
        if value is FAILED:
            text = types.TextContent(type="text", text="no answer")
            return types.CallToolResult(content=[text], is_error=True)
        text = types.TextContent(type="text", text=json.dumps(value))
        return types.CallToolResult(content=[text], structured_content=value)

    return Server("robot", on_list_tools=listed, on_call_tool=called)


def _overtaken(
    first: str, *later: tuple[str, dict[str, Any]], slow: Any = None, **rules: Any
) -> tuple[list[str], list[Any]]:
    """Send ``first``, slow, a stop 0.05 s into it, then each of ``later``.

    Return the order the first two were answered in, then the stop's answer and each later one.
    """
    answered: list[str] = []
    answers = {name: (value, 0.0) for name, value in ANSWERS.items()}
    answers[first] = (ANSWERS[first] if slow is None else slow, 0.3)

    async def session() -> list[Any]:
        async with Client(_robot(answers)) as upstream:
            front = proxy(upstream, (await upstream.list_tools()).tools, CONTRACTS, **rules)
            async with Client(front) as client:

                async def call(name: str) -> Any:
                    result = await client.call_tool(name, {})
                    answered.append(name)
                    return result

                flying = asyncio.create_task(call(first))
                await asyncio.sleep(0.05)
                stopped = await call("stop")
                await flying
                return [stopped, *[await client.call_tool(n, given) for n, given in later]]

    return answered, asyncio.run(session())


def _refused(result: Any) -> str:
    assert result.is_error
    return "".join(block.text for block in result.content)


@pytest.mark.parametrize(
    "rules",
    [
        {"flows": [MEASURED], "preempts": ["stop"]},
        {"flows": [MEASURED], "after_violation": ["stop"]},
        {"policies": [MOVING], "preempts": ["stop"]},
    ],
    ids=["fresh-rule", "after-violation", "policy-reading-it"],
)
def test_a_stop_is_answered_before_the_walk_it_overtakes(rules: dict[str, Any]) -> None:
    assert _overtaken("walk_to", **rules)[0] == ["stop", "walk_to"]
    held = {
        name: value for name, value in rules.items() if name not in ("preempts", "after_violation")
    }
    assert _overtaken("walk_to", **held)[0] == ["walk_to", "stop"]


def test_a_stop_acting_before_the_walk_it_overtakes_is_held() -> None:
    # The stop may act before the walk does, and the walk then moves the robot after it.
    _, (_, grasped) = _overtaken("walk_to", GRASP, policies=[MOVING], preempts=["stop"])
    assert _refused(grasped) == (
        "refused: no grasp while moving: grasp reached violated; from moving, stop leads to still"
    )
    _, (_, waited) = _overtaken("walk_to", GRASP, policies=[MOVING])
    assert not waited.is_error


def test_a_stop_a_policy_refuses_before_the_walk_it_overtakes_is_refused() -> None:
    # A stop with no walk to end is a violation here, and the overtaking stop may act first.
    ending = read_policy(
        {
            "name": "a stop ends a walk",
            "initial": "idle",
            "bad": ["violated"],
            "transitions": [
                {"from": "idle", "tool": "walk_to", "to": "moving"},
                {"from": "moving", "tool": "stop", "to": "idle"},
                {"from": "idle", "tool": "stop", "to": "violated"},
            ],
        }
    )
    answered, (stopped,) = _overtaken("walk_to", policies=[ending], preempts=["stop"])
    assert answered == ["stop", "walk_to"]
    assert _refused(stopped).startswith("refused: a stop ends a walk: stop reached violated")


def test_a_walk_answering_before_the_stop_it_was_overtaken_by_is_held() -> None:
    # The walk may arrive before the stop acts: only that order docks the robot.
    _, (_, grasped) = _overtaken("walk_to", GRASP, policies=[DOCKING], preempts=["stop"])
    assert _refused(grasped).startswith("refused: no grasp once docked: grasp reached violated")


def test_a_result_only_the_other_order_takes_to_a_bad_state_is_refused() -> None:
    # Stopping once arrived is the violation: the walk may have arrived before the stop acted,
    # so its result is refused, as one stepping into a bad state is.
    arriving = read_policy(
        {
            "name": "no stop once arrived",
            "initial": "idle",
            "bad": ["violated"],
            "transitions": [
                {"from": "idle", "tool": "walk_to", "to": "moving"},
                {
                    "from": "moving",
                    "tool": "walk_to",
                    "result": {"properties": {"arrived": {"const": True}}},
                    "to": "arrived",
                },
                {"from": "moving", "tool": "stop", "to": "halted"},
                {"from": "arrived", "tool": "stop", "to": "violated"},
            ],
        }
    )
    walked: list[Any] = []

    async def session() -> None:
        answers = {n: (v, 0.3 if n == "walk_to" else 0.0) for n, v in ANSWERS.items()}
        async with Client(_robot(answers)) as upstream:
            listing = (await upstream.list_tools()).tools
            front = proxy(upstream, listing, CONTRACTS, policies=[arriving], preempts=["stop"])
            async with Client(front) as client:
                flying = asyncio.create_task(client.call_tool("walk_to", {}))
                await asyncio.sleep(0.05)
                await client.call_tool("stop", {})
                walked.append(await flying)

    asyncio.run(session())
    assert (
        _refused(walked[0])
        == "refused: no stop once arrived: walk_to may have returned into violated"
    )


def test_a_walk_failing_after_a_stop_overtook_it_may_not_have_acted() -> None:
    # The walk answered with an error may never have moved the robot: the grasp it gates waits.
    _, (_, grasped) = _overtaken(
        "walk_to", GRASP, slow=FAILED, policies=[WALKED_FIRST], preempts=["stop"]
    )
    assert _refused(grasped).startswith("refused: a grasp follows a walk: grasp reached violated")


def test_a_reading_a_stop_may_have_outdated_reaches_no_fresh_sink() -> None:
    # The stop acts while the pose is read, so the distance may be one from before it moved.
    _, (_, grasped) = _overtaken("pose", GRASP, flows=[MEASURED], preempts=["stop"])
    assert _refused(grasped) == (
        "refused: flow measured: grasp.distance holds a value none of pose returned "
        "since the last call that may act"
    )
    _, (_, read, again) = _overtaken(
        "walk_to", ("pose", {}), GRASP, flows=[MEASURED], preempts=["stop"]
    )
    assert not read.is_error
    assert not again.is_error


def test_a_preempting_tool_the_proxy_lacks_is_refused() -> None:
    with pytest.raises(ValueError, match="halt"):
        Gate(CONTRACTS, [], [], preempts=frozenset({"halt"}))
