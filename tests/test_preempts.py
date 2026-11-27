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
from valgebra_planning.policy import Monitor, read_policy
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


def _robot(answers: dict[str, Any], made: list[str] | None = None) -> Server[Any]:
    """Serve the robot's tools, each answering from ``answers`` after the delay it names.

    Each call the robot receives is appended to ``made``, where one is given.
    """

    async def listed(_: Any, __: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[types.Tool.model_validate(t) for t in LISTING])

    async def called(_: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        if made is not None:
            made.append(params.name)
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


#: Reads the walk, the grasp and the pose in order and refuses none of them in any state: each
#: holds the gate's order, so a call sent while the walk is in flight waits for its result.
IN_ORDER = read_policy(
    {
        "name": "calls are read in order",
        "initial": "idle",
        "bad": ["violated"],
        "transitions": [
            {"from": "idle", "tool": tool, "to": "idle"} for tool in ("walk_to", "grasp", "pose")
        ],
    }
)


def _queued(waiting: tuple[str, dict[str, Any]], *, stop: bool = True) -> tuple[list[str], Any]:
    """Send a slow walk, ``waiting`` 0.03 s into it, then a stop 0.03 s later, where ``stop``.

    Return the calls the robot received in order, and the answer ``waiting`` got.
    """
    made: list[str] = []
    answers = {name: (value, 0.0) for name, value in ANSWERS.items()}
    answers["walk_to"] = (ANSWERS["walk_to"], 0.3)

    async def session() -> Any:
        async with Client(_robot(answers, made)) as upstream:
            listing = (await upstream.list_tools()).tools
            front = proxy(upstream, listing, CONTRACTS, policies=[IN_ORDER], preempts=["stop"])
            async with Client(front) as client:
                walk = asyncio.create_task(client.call_tool("walk_to", {}))
                await asyncio.sleep(0.03)
                queued = asyncio.create_task(client.call_tool(*waiting))
                await asyncio.sleep(0.03)
                if stop:
                    await client.call_tool("stop", {})
                await walk
                return await queued

    answered = asyncio.run(session())
    return made, answered


def test_a_call_sent_before_a_stop_is_refused_once_the_stop_is_made() -> None:
    # A robot's action server cancels every goal accepted before a cancel's stamp: the grasp
    # waiting behind the walk was sent before the stop, so the stop is the later word on it.
    made, grasped = _queued(GRASP)
    assert made == ["walk_to", "stop"]
    assert _refused(grasped) == (
        "refused: grasp was sent before stop, which was made while it waited: "
        "send it again if it is still wanted"
    )


def test_a_read_only_call_sent_before_a_stop_is_made() -> None:
    made, posed = _queued(("pose", {}))
    assert made == ["walk_to", "stop", "pose"]
    assert not posed.is_error


def test_a_call_waiting_for_no_stop_is_made() -> None:
    made, grasped = _queued(GRASP, stop=False)
    assert made == ["walk_to", "grasp"]
    assert not grasped.is_error


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


class _Clock:
    """A clock a test sets."""

    now = 0.0

    def __call__(self) -> float:
        return self.now


def _gate(clock: _Clock, *transitions: dict[str, Any]) -> tuple[Gate, Monitor]:
    """Return a gate whose stop overtakes, under one policy read by ``clock``."""
    policy = read_policy(
        {"name": "p", "initial": "idle", "bad": ["violated"], "transitions": list(transitions)}
    )
    monitor = Monitor(policy, clock=clock)
    return Gate(CONTRACTS, [monitor], [], preempts=frozenset({"stop"})), monitor


WALK_MOVES = {"from": "idle", "tool": "walk_to", "to": "moving"}
#: A policy reads the stop, and it moves no state a walk reaches.
STOPPED = {"from": "parked", "tool": "stop", "to": "parked"}


def test_a_walk_a_stop_overtook_enters_its_state_whenever_it_acts_until_it_answers() -> None:
    # From the stop on, the walk may act at any time until its answer: moving cannot lapse before.
    clock = _Clock()
    gate, monitor = _gate(
        clock,
        WALK_MOVES,
        {"from": "moving", "after_s": 10, "to": "rested"},
        {"from": "moving", "tool": "stop", "to": "still"},
        {"from": "moving", "tool": "grasp", "to": "violated"},
    )
    assert gate.before("walk_to", {}) is None
    clock.now = 1
    assert gate.before("stop", {}) is None
    clock.now = 50
    assert monitor.held() == {"still", "moving", "rested"}
    clock.now = 60
    gate.after("stop", {"stopped": True}, {})
    gate.after("walk_to", {"arrived": True}, {})
    for now, held in ((69, {"still", "moving", "rested"}), (70, {"still", "rested"})):
        clock.now = now
        assert monitor.held() == held
    assert gate.before(*GRASP) is None


def test_an_outcome_that_came_before_the_stop_is_read_at_a_time_before_it() -> None:
    # The walk's answer, had it come first, came between the walk and the stop: arrived was
    # entered in those four seconds, so it may have lapsed by the stop and may not.
    clock = _Clock()
    gate, monitor = _gate(
        clock,
        WALK_MOVES,
        {
            "from": "moving",
            "tool": "walk_to",
            "result": {"properties": {"arrived": {"const": True}}},
            "to": "arrived",
        },
        {"from": "arrived", "after_s": 3, "to": "left"},
        {"from": "moving", "tool": "stop", "to": "halted"},
        {"from": "arrived", "tool": "stop", "to": "docked"},
        {"from": "left", "tool": "stop", "to": "passed"},
        {"from": "docked", "after_s": 20, "to": "undocked"},
    )
    gate.before("walk_to", {})
    clock.now = 4
    gate.before("stop", {})
    clock.now = 23
    gate.after("stop", {"stopped": True}, {})
    gate.after("walk_to", {"arrived": True}, {})
    assert monitor.held() == {"halted", "arrived", "docked", "passed"}
    # Docked was entered by the stop, at four seconds, and not when the answers were read.
    clock.now = 24
    assert "docked" not in monitor.held()
    assert "undocked" in monitor.held()


def test_an_overtaken_walk_no_result_answered_may_have_answered_at_any_time() -> None:
    clock = _Clock()
    gate, monitor = _gate(
        clock,
        WALK_MOVES,
        {
            "from": "moving",
            "tool": "walk_to",
            "result": {"properties": {"arrived": {"const": True}}},
            "to": "arrived",
        },
        {"from": "arrived", "after_s": 10, "to": "left"},
        {"from": "left", "tool": "grasp", "to": "violated"},
        STOPPED,
    )
    gate.before("walk_to", {})
    clock.now = 5
    gate.before("stop", {})
    clock.now = 9
    gate.unread("walk_to", {})
    assert monitor.held() == {"idle", "moving", "arrived"}
    clock.now = 10
    assert monitor.held() == {"idle", "moving", "arrived", "left"}
    clock.now = 19
    assert monitor.held() == {"idle", "moving", "left"}


def test_a_stop_is_not_refused_by_a_deadline_the_walk_may_yet_meet() -> None:
    # The walk overtaken may act late, and no deadline has passed yet: the stop is admitted.
    clock = _Clock()
    gate, monitor = _gate(
        clock,
        WALK_MOVES,
        {"from": "idle", "after_s": 30, "to": "violated"},
        STOPPED,
    )
    assert gate.before("walk_to", {}) is None
    clock.now = 1
    assert gate.before("stop", {}) is None
    assert "violated" not in monitor.held()
