from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import Client, types
from mcp.server.lowlevel import Server

from valgebra_planning.contracts import read_contract
from valgebra_planning.flows import read_flow
from valgebra_planning.policy import read_policy
from valgebra_planning.proxy import proxy

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
LISTING = json.loads((EXAMPLE / "tools.json").read_text())["tools"]
TOOLS = {contract.name: contract for contract in map(read_contract, LISTING)}
FREIGHT = read_policy(json.loads((EXAMPLE / "policies.json").read_text())["policies"][0])
VERIFIED = read_flow(json.loads((EXAMPLE / "flows.json").read_text())["flows"][0])
PARCEL = {
    "parcel_id": "pcl_1",
    "state": "delivered",
    "weight_g": 25000,
    "country": "IT",
    "contact": "ann@shop.example",
}
ANSWERS: dict[str, Any] = {
    "find_parcel": {"parcel": PARCEL},
    "approve_freight": {"approval_id": "apr_1"},
    "book_freight_return": {"label_id": "lbl_1"},
    "verify_contact": {"email": "desk@parcels.example"},
    "notify": {"notice_id": "ntc_1"},
}
BOOKING = ("book_freight_return", {"parcel_id": "pcl_1", "state": "delivered", "weight_g": 25000})


def _upstream(made: list[str], answers: dict[str, Any], listing: list[Any]) -> Server[Any]:
    """Serve the parcel tools as ``listing`` declares them, answering from ``answers``."""

    async def listed(_: Any, __: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[types.Tool.model_validate(t) for t in listing])

    async def called(_: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        made.append(params.name)
        value = answers[params.name]
        text = types.TextContent(type="text", text=json.dumps(value))
        return types.CallToolResult(content=[text], structured_content=value)

    return Server("parcels", on_list_tools=listed, on_call_tool=called)


def _session(
    calls: list[tuple[str, dict[str, Any]]],
    answers: dict[str, Any] = ANSWERS,
    listing: list[Any] = LISTING,
    **rules: Any,
) -> tuple[list[str], list[Any]]:
    """Run ``calls`` through a proxy in front of the parcel server: what reached it, the answers."""
    made: list[str] = []

    async def session() -> list[Any]:
        async with Client(_upstream(made, answers, listing)) as upstream:
            front = proxy(upstream, (await upstream.list_tools()).tools, TOOLS, **rules)
            async with Client(front) as client:
                return [await client.call_tool(name, arguments) for name, arguments in calls]

    return made, asyncio.run(session())


def _refused(result: Any) -> str:
    assert result.is_error
    return "".join(block.text for block in result.content)


def test_a_call_inside_every_check_passes_through() -> None:
    made, (result,) = _session([("find_parcel", {"tracking": "AB123456789IT"})])
    assert made == ["find_parcel"]
    assert result.structured_content == {"parcel": PARCEL}


def test_an_argument_record_outside_the_input_set_never_reaches_the_server() -> None:
    made, (result,) = _session([("find_parcel", {"tracking": "nope"})])
    assert made == []
    assert _refused(result).startswith("refused: find_parcel refuses its arguments")


def test_a_result_outside_its_contract_is_never_passed_on() -> None:
    # The upstream declares no output schema, so it passes the result on and the proxy judges it.
    bare = [{k: v for k, v in tool.items() if k != "outputSchema"} for tool in LISTING]
    answers = ANSWERS | {"find_parcel": {"parcel": {"parcel_id": "x"}}}
    made, (result,) = _session([("find_parcel", {"tracking": "AB123456789IT"})], answers, bare)
    assert made == ["find_parcel"]
    assert "outside its contract" in _refused(result)


def test_a_refused_call_leaves_the_policy_where_it_was() -> None:
    approval = ("approve_freight", {"parcel_id": "pcl_1"})
    made, (early, approved, booked) = _session([BOOKING, approval, BOOKING], policies=[FREIGHT])
    violated = "refused: freight approved first: book_freight_return reached violated"
    assert _refused(early) == violated
    assert not approved.is_error
    assert not booked.is_error
    assert made == ["approve_freight", "book_freight_return"]


def test_a_value_an_untrusted_tool_returned_does_not_reach_the_sink() -> None:
    calls = [
        ("find_parcel", {"tracking": "AB123456789IT"}),
        ("notify", {"email": "ann@shop.example"}),
        ("verify_contact", {"email": "ann@shop.example"}),
        ("notify", {"email": "desk@parcels.example"}),
    ]
    made, results = _session(calls, flows=[VERIFIED])
    flow = "flow notices go to verified addresses"
    assert (
        _refused(results[1]) == f"refused: {flow}: notify.email holds a value find_parcel returned"
    )
    assert not results[3].is_error
    assert made == ["find_parcel", "verify_contact", "notify"]
