from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

import pytest
from mcp import Client, types
from mcp.server import MCPServer
from mcp.server.lowlevel import Server

from valgebra_planning._valgebra import Sampler
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.planner import plan
from valgebra_planning.policy import read_policy
from valgebra_planning.program import Block
from valgebra_planning.run import Refusal, blame
from valgebra_planning.servers import connect, read_server, run_on_server

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
LISTING = json.loads((EXAMPLE / "tools.json").read_text(encoding="utf-8"))["tools"]
TOOLS = {contract.name: contract for contract in map(read_contract, LISTING)}
SETTLE = next(
    read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    if spec["name"] == "settle a parcel"
)


def _server(answer: Callable[[str, dict[str, Any]], Any]) -> Server[Any]:
    """Serve the parcel tools in-process, each call answered by ``answer``."""

    async def listed(_: Any, __: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[types.Tool.model_validate(tool) for tool in LISTING])

    async def called(_: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        value = answer(params.name, params.arguments or {})
        if isinstance(value, Exception):
            text = types.TextContent(type="text", text=str(value))
            return types.CallToolResult(content=[text], is_error=True)
        text = types.TextContent(type="text", text=json.dumps(value))
        return types.CallToolResult(content=[text], structured_content=value)

    return Server("parcels", on_list_tools=listed, on_call_tool=called)


def _drawn(rng: random.Random) -> Callable[[str, dict[str, Any]], Any]:
    def answer(name: str, _: dict[str, Any]) -> Any:
        whole = TOOLS[name].result()
        return rng.choice(Sampler(whole.root, rng).draw(whole.schema, 8))

    return answer


async def _listed(server: Any) -> dict[str, Any]:
    async with Client(server) as client:
        return await read_server(client)


def test_a_live_listing_reads_as_the_listing_on_disk() -> None:
    live = asyncio.run(_listed(_server(_drawn(random.Random(0)))))
    assert set(live) == set(TOOLS)
    for name, contract in TOOLS.items():
        assert [s.name for s in live[name].arguments] == [s.name for s in contract.arguments]
        assert [s.name for s in live[name].results] == [s.name for s in contract.results]
        assert live[name].idempotent == contract.idempotent


def test_a_strong_program_runs_against_a_live_server_breaking_nothing() -> None:
    found = plan(TOOLS, SETTLE)
    assert found.program is not None
    program = found.program
    rng = random.Random(1)

    async def runs() -> list[Any]:
        async with Client(_server(_drawn(rng))) as client:
            contracts = await read_server(client)
            inputs = {"tracking": "AB123456789IT"}
            return [
                await run_on_server(program, contracts, SETTLE, inputs, client) for _ in range(20)
            ]

    for result in asyncio.run(runs()):
        assert result.broken == ()
        assert result.refused is None
        assert result.stopped is None


def test_an_error_result_is_a_tool_error_and_an_unhandled_one_stops_the_run() -> None:
    found = plan(TOOLS, SETTLE)
    assert found.program is not None
    program = found.program

    def answer(name: str, _: dict[str, Any]) -> Any:
        if name == "find_parcel":
            return {
                "parcel": {
                    "parcel_id": "pcl_1",
                    "state": "lost",
                    "weight_g": 10,
                    "country": "IT",
                    "contact": "a@b.c",
                }
            }
        return RuntimeError(f"{name} is down")

    async def once() -> Any:
        async with Client(_server(answer)) as client:
            return await run_on_server(
                program, TOOLS, SETTLE, {"tracking": "AB123456789IT"}, client
            )

    result = asyncio.run(once())
    assert result.stopped == "an error of open_claim"
    assert result.broken == ("open_claim answered with an error nothing handles",)


class _Label(TypedDict):
    label_id: str


def test_a_server_built_from_typed_functions_is_read_with_its_derived_output_schema() -> None:
    server = MCPServer("typed")

    @server.tool()
    def book(parcel_id: str) -> _Label:
        return {"label_id": f"lbl_{parcel_id}"}

    contracts = asyncio.run(_listed(server))
    assert [slot.name for slot in contracts["book"].arguments] == ["book.parcel_id"]
    assert "book.label_id" in [slot.name for slot in contracts["book"].results]


CONFIRM = {
    "name": "confirm",
    "inputSchema": {
        "type": "object",
        "properties": {"order": {"type": "integer"}},
        "required": ["order"],
    },
    "outputSchema": {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    },
}
ASK = types.ElicitRequest(
    params=types.ElicitRequestFormParams(
        message="Confirm the order?",
        requested_schema={"type": "object", "properties": {"yes": {"type": "boolean"}}},
    )
)
ASKING = [
    types.InputRequiredResult(input_requests={"confirm": ASK}),
    types.InputRequiredResult(request_state="waiting"),
]


def _asking(answer: types.InputRequiredResult) -> Server[Any]:
    """Serve one tool that answers every call by asking for input."""

    async def listed(_: Any, __: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[types.Tool.model_validate(CONFIRM)])

    async def called(_: Any, __: types.CallToolRequestParams) -> types.InputRequiredResult:
        return answer

    return Server("asks", on_list_tools=listed, on_call_tool=called)


@pytest.mark.parametrize("answer", ASKING, ids=["elicitation", "waiting"])
def test_a_call_asking_for_input_fails_as_a_tool_error(answer: types.InputRequiredResult) -> None:
    contracts = {"confirm": read_contract(CONFIRM)}
    task = read_task("confirm an order", {"order": {"type": "integer"}}, {"type": "boolean"})
    program = plan(contracts, task).program
    assert program is not None

    async def ran(program: Block) -> Any:
        # One round: a server that only waits is otherwise polled ten times, with backoff.
        async with Client(_asking(answer), input_required_max_rounds=1) as client:
            return await run_on_server(program, contracts, task, {"order": 1}, client)

    result = asyncio.run(ran(program))
    assert result.value is None
    assert result.why == Refusal("error", "confirm")


def test_a_call_no_result_answered_reaches_every_state_it_may_have() -> None:
    # A call asking for input past its rounds is answered by no result: it may have acted and
    # returned anything, so the run holds the state a result would reach, owed at the stop.
    contracts = {"confirm": read_contract(CONFIRM)}
    task = read_task("confirm an order", {"order": {"type": "integer"}}, {"type": "boolean"})
    owed = read_policy(
        {
            "name": "a confirmation is followed up",
            "initial": "idle",
            "bad": [],
            "pending": ["confirmed"],
            "transitions": [
                {"from": "idle", "tool": "confirm", "result": {"type": "object"}, "to": "confirmed"}
            ],
        }
    )
    program = read_program(
        "try:\n    c1 = confirm(order=order)\nexcept ToolError:\n    stop(order)\nreturn c1.ok\n"
    )

    async def ran() -> Any:
        async with Client(_asking(ASKING[0]), input_required_max_rounds=1) as client:
            return await run_on_server(
                program, contracts, task, {"order": 1}, client, policies=[owed]
            )

    result = asyncio.run(ran())
    assert result.why == Refusal("policy")
    assert "confirmed" in (result.refused or "")


#: A string no schema declares and no code spells: a part of a value the client refuses.
HIDDEN = "HIDDEN-41"


def test_a_result_the_client_refuses_ends_the_run_naming_the_tool_and_never_the_value(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    found = plan(TOOLS, SETTLE)
    assert found.program is not None
    program = found.program

    def answer(name: str, _: dict[str, Any]) -> Any:
        assert name == "find_parcel"
        return {"parcel": {"parcel_id": HIDDEN}}

    async def once() -> Any:
        async with Client(_server(answer)) as client:
            return await run_on_server(
                program, TOOLS, SETTLE, {"tracking": "AB123456789IT"}, client
            )

    result = asyncio.run(once())
    assert result.refused == (
        "find_parcel returned a value outside its contract, refused by its caller"
    )
    assert result.why == Refusal("result", "find_parcel")
    assert blame(result, TOOLS, proven=True) == (
        "find_parcel broke the output schema its server declares"
    )
    captured = capsys.readouterr()
    assert HIDDEN not in repr(result) + captured.out + captured.err + caplog.text


WALK = {
    "name": "walk_to",
    "inputSchema": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
    "outputSchema": {
        "type": "object",
        "properties": {"arrived": {"type": "boolean"}},
        "required": ["arrived"],
    },
}
WALKING = {"walk_to": read_contract(WALK)}
THREE_WALKS = read_program(
    "c1 = walk_to(x=1)\nc2 = walk_to(x=2)\nc3 = walk_to(x=3)\nreturn c3.arrived\n"
)


def test_a_canceled_run_sends_no_call_after_the_cancel() -> None:
    made: list[int] = []
    walking = asyncio.Event()

    async def listed(_: Any, __: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[types.Tool.model_validate(WALK)])

    async def called(_: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        made.append((params.arguments or {})["x"])
        walking.set()
        await asyncio.sleep(0.2)
        arrived = {"arrived": True}
        text = types.TextContent(type="text", text=json.dumps(arrived))
        return types.CallToolResult(content=[text], structured_content=arrived)

    async def once() -> None:
        walker = Server("walker", on_list_tools=listed, on_call_tool=called)
        async with Client(walker) as client:
            task = asyncio.create_task(
                run_on_server(THREE_WALKS, WALKING, read_task("walk", {}, True), {}, client)
            )
            await walking.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # Three walks take 0.6 s: a worker going on would make the second and the third.
            await asyncio.sleep(0.8)

    asyncio.run(once())
    assert made == [1]


def test_a_client_waits_for_an_answer_as_long_as_its_read_timeout() -> None:
    assert connect("https://robot.example/mcp").read_timeout_seconds is None
    assert (
        connect("https://robot.example/mcp", read_timeout_seconds=2.5).read_timeout_seconds == 2.5
    )
    assert connect("python -m server", read_timeout_seconds=1.5).read_timeout_seconds == 1.5
