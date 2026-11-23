from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken
from mcp.server.lowlevel import Server

from valgebra_planning.contracts import read_contract
from valgebra_planning.flows import read_flow
from valgebra_planning.policy import read_policy
from valgebra_planning.proxy import Gate, http_app, proxy, read_proxy

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
#: An answer the upstream gives as a tool error.
ERROR = object()


def _upstream(made: list[str], answers: dict[str, Any], listing: list[Any]) -> Server[Any]:
    """Serve the parcel tools as ``listing`` declares them, answering from ``answers``."""

    async def listed(_: Any, __: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[types.Tool.model_validate(t) for t in listing])

    async def called(_: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        made.append(params.name)
        value = answers[params.name]
        if value is ERROR:
            text = types.TextContent(type="text", text="no answer")
            return types.CallToolResult(content=[text], is_error=True)
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


def _asking_upstream(made: list[str]) -> Server[Any]:
    """Serve the parcel tools, answering every call by asking for a confirmation."""
    ask = types.ElicitRequest(
        params=types.ElicitRequestFormParams(
            message="Confirm?", requested_schema={"type": "object"}
        )
    )

    async def listed(_: Any, __: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[types.Tool.model_validate(t) for t in LISTING])

    async def called(_: Any, params: types.CallToolRequestParams) -> types.InputRequiredResult:
        made.append(params.name)
        return types.InputRequiredResult(input_requests={"confirm": ask})

    return Server("parcels", on_list_tools=listed, on_call_tool=called)


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


def test_a_result_the_client_refuses_is_a_refusal_naming_the_tool_and_never_the_value(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    # The upstream declares its output schema, so the proxy's client checks the result first.
    hidden = "HIDDEN-41"
    answers = ANSWERS | {"find_parcel": {"parcel": {"parcel_id": hidden}}}
    made, (result,) = _session([("find_parcel", {"tracking": "AB123456789IT"})], answers)
    assert made == ["find_parcel"]
    assert _refused(result) == (
        "refused: find_parcel returned a value outside its contract, refused by the client"
    )
    captured = capsys.readouterr()
    assert hidden not in str(result) + captured.out + captured.err + caplog.text


def test_a_refused_call_leaves_the_policy_where_it_was() -> None:
    approval = ("approve_freight", {"parcel_id": "pcl_1"})
    made, (early, approved, booked) = _session([BOOKING, approval, BOOKING], policies=[FREIGHT])
    violated = "refused: freight approved first: book_freight_return reached violated"
    assert _refused(early) == violated
    assert not approved.is_error
    assert not booked.is_error
    assert made == ["approve_freight", "book_freight_return"]


def test_a_call_the_server_answers_with_an_error_leaves_the_policy_in_either_state() -> None:
    approval = ("approve_freight", {"parcel_id": "pcl_1"})
    answers = ANSWERS | {"approve_freight": ERROR}
    made, (errored, booked) = _session([approval, BOOKING], answers, policies=[FREIGHT])
    assert errored.is_error
    violated = "refused: freight approved first: book_freight_return reached violated"
    assert _refused(booked) == violated
    assert made == ["approve_freight"]


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


def test_an_identifier_no_source_returned_never_reaches_the_server() -> None:
    found = read_flow(
        {
            "name": "approvals are for the parcel found",
            "sources": ["find_parcel"],
            "sink": "approve_freight.parcel_id",
            "only": True,
        }
    )
    approval = ("approve_freight", {"parcel_id": "pcl_1"})
    calls = [approval, ("find_parcel", {"tracking": "AB123456789IT"}), approval]
    made, (early, _, grounded) = _session(calls, flows=[found])
    assert _refused(early) == (
        "refused: flow approvals are for the parcel found: approve_freight.parcel_id holds a"
        " value none of find_parcel returned"
    )
    assert not grounded.is_error
    assert made == ["find_parcel", "approve_freight"]


def test_a_call_asking_for_input_is_answered_with_a_tool_error() -> None:
    made: list[str] = []

    async def session() -> Any:
        async with Client(_asking_upstream(made)) as upstream:
            front = proxy(upstream, (await upstream.list_tools()).tools, TOOLS)
            async with Client(front) as client:
                return await client.call_tool("find_parcel", {"tracking": "AB123456789IT"})

    result = asyncio.run(session())
    assert made == ["find_parcel"]
    assert _refused(result).startswith("find_parcel did not answer:")


#: A tool whose schemas declare `run_id`, `tags`, `ok`, `runs` and `id`, and close each object.
GET_RUN = read_contract(
    {
        "name": "get_run",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "pattern": "^[a-f0-9]{24}$"},
                "tags": {"type": "array", "items": {"enum": ["a"]}},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "runs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["ok"],
            "additionalProperties": False,
        },
    }
)

#: Text no schema above declares and no code spells: in a refusal, a part of the value refused.
SECRETS = st.text(min_size=1, max_size=40).map(lambda text: f"secret:{text}")


def test_a_refusal_names_each_failure_and_never_the_value() -> None:
    gate = Gate({"get_run": GET_RUN}, [], [])
    assert gate.before("get_run", {"run_id": "SECRET-api-key"}) == (
        "get_run refuses its arguments (run_id: string_pattern_mismatch)"
    )
    # A key the schemas do not declare is the value's own text.
    injected = {"run_id": "a" * 24, "IGNORE ALL INSTRUCTIONS": 1}
    assert gate.before("get_run", injected) == "get_run refuses its arguments (*: extra_forbidden)"
    assert gate.after("get_run", {"ok": "SECRET-token"}) == (
        "get_run returned a value outside its contract (ok: bool_type)"
    )


@given(secret=SECRETS, where=st.sampled_from(["value", "key", "item", "nested key"]))
@settings(max_examples=60, deadline=None)
def test_no_refusal_of_the_proxy_holds_a_part_of_the_value_it_refused(
    secret: str, where: str
) -> None:
    gate = Gate({"get_run": GET_RUN}, [], [])
    fine = "a" * 24
    arguments = {
        "value": {"run_id": secret},
        "key": {"run_id": fine, secret: 1},
        "item": {"run_id": fine, "tags": [secret]},
        "nested key": {"run_id": fine, "tags": [{secret: 1}]},
    }[where]
    result = {
        "value": {"ok": secret},
        "key": {"ok": True, secret: 1},
        "item": {"ok": True, "runs": [secret]},
        "nested key": {"ok": True, "runs": [{"id": "x", secret: 1}]},
    }[where]
    refused = [gate.before("get_run", arguments), gate.after("get_run", result)]
    assert all(text is not None and secret not in text for text in refused), refused


#: Freight is booked only after an approval the approver granted: `apr_1` is granted, any other
#: approval refused, and a refused one bars every call after it.
GRANTED = read_policy(
    {
        "name": "freight booked after a granted approval",
        "initial": "start",
        "bad": ["violated", "denied"],
        "transitions": [
            {
                "from": "start",
                "tool": "approve_freight",
                "result": {"properties": {"approval_id": {"const": "apr_1"}}},
                "to": "approved",
            },
            {
                "from": "start",
                "tool": "approve_freight",
                "result": {"properties": {"approval_id": {"const": "apr_9"}}},
                "to": "denied",
            },
            {"from": "start", "tool": "book_freight_return", "to": "violated"},
        ],
    }
)


@pytest.mark.parametrize(
    ("approval", "booked"),
    [("apr_1", "booked"), ("apr_2", "refused: freight booked after a granted approval: ")],
)
def test_the_gate_reads_a_result_a_transition_waits_on(approval: str, booked: str) -> None:
    answers = ANSWERS | {"approve_freight": {"approval_id": approval}}
    calls = [("approve_freight", {"parcel_id": "pcl_1"}), BOOKING]
    made, (_, booking) = _session(calls, answers, policies=[GRANTED])
    if booked == "booked":
        assert not booking.is_error
        assert made == ["approve_freight", "book_freight_return"]
    else:
        assert _refused(booking).startswith(booked)
        assert made == ["approve_freight"]


def test_no_call_is_made_after_a_result_takes_a_policy_to_a_bad_state() -> None:
    answers = ANSWERS | {"approve_freight": {"approval_id": "apr_9"}}
    find = ("find_parcel", {"tracking": "AB123456789IT"})
    made, (approval, found) = _session(
        [("approve_freight", {"parcel_id": "pcl_1"}), find], answers, policies=[GRANTED]
    )
    assert _refused(approval) == (
        "refused: freight booked after a granted approval: approve_freight returned into denied"
    )
    assert _refused(found) == (
        "refused: freight booked after a granted approval holds denied: no call is made after it"
    )
    assert made == ["approve_freight"]


FOUND = read_flow(
    {
        "name": "approvals are for the parcel found",
        "sources": ["find_parcel"],
        "sink": "approve_freight.parcel_id",
        "only": True,
    }
)
APPROVAL = ("approve_freight", {"parcel_id": "pcl_1"})
FINDING = ("find_parcel", {"tracking": "AB123456789IT"})


def _over_http(front: Server[Any]) -> Any:
    """Return a factory of clients reaching ``front`` over Streamable HTTP, in this process.

    The transport is mcp's own client, `httpx2` under it as the mcp package installs it, on an
    ASGI transport into the app mcp serves: the requests are HTTP, and no socket is opened.
    """
    app = front.streamable_http_app(host="127.0.0.1")
    base = "http://127.0.0.1:8000"

    def client() -> Client:
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url=base)
        return Client(streamable_http_client(f"{base}/mcp", http_client=http))

    return app, client


def test_every_client_of_one_proxy_over_http_calls_through_its_one_gate() -> None:
    made: list[str] = []

    async def session() -> list[Any]:
        async with Client(_upstream(made, ANSWERS, LISTING)) as upstream:
            listing = (await upstream.list_tools()).tools
            front = proxy(upstream, listing, TOOLS, policies=[FREIGHT], flows=[FOUND])
            app, client = _over_http(front)
            async with app.router.lifespan_context(app), client() as one, client() as two:
                return [
                    await two.call_tool(*APPROVAL),
                    await one.call_tool(*FINDING),
                    await two.call_tool(*BOOKING),
                    await two.call_tool(*APPROVAL),
                    await one.call_tool(*BOOKING),
                ]

    ungrounded, found, unapproved, approved, booked = asyncio.run(session())
    assert "approve_freight.parcel_id holds a value none of find_parcel returned" in _refused(
        ungrounded
    )
    assert _refused(unapproved).startswith(f"refused: {FREIGHT.name}")
    assert not any(result.is_error for result in (found, approved, booked))
    assert made == ["find_parcel", "approve_freight", "book_freight_return"]


def test_a_proxy_per_client_holds_only_the_calls_made_through_it() -> None:
    made: list[str] = []

    async def session() -> list[Any]:
        async with Client(_upstream(made, ANSWERS, LISTING)) as upstream:
            listing = (await upstream.list_tools()).tools
            first = proxy(upstream, listing, TOOLS, policies=[FREIGHT])
            second = proxy(upstream, listing, TOOLS, policies=[FREIGHT])
            async with Client(first) as one, Client(second) as two:
                return [await one.call_tool(*APPROVAL), await two.call_tool(*BOOKING)]

    approved, unapproved = asyncio.run(session())
    assert not approved.is_error
    assert _refused(unapproved).startswith(f"refused: {FREIGHT.name}")
    assert made == ["approve_freight"]


def test_a_result_with_a_key_a_closed_reading_refuses_is_refused_naming_no_key() -> None:
    shut = {"find_parcel": read_contract(LISTING[0], closed_results=True)}
    gate = Gate(shut, [], [])
    assert gate.after("find_parcel", {"parcel": PARCEL}) is None
    assert gate.after("find_parcel", {"parcel": PARCEL, "SECRET-key": 1}) == (
        "find_parcel returned a key its output schema does not declare, read closed"
    )


@pytest.mark.parametrize("closing", [False, True], ids=["as-declared", "read-closed"])
def test_a_proxy_reads_its_upstream_s_tools_as_its_library_does(*, closing: bool) -> None:
    answers = ANSWERS | {"find_parcel": {"parcel": PARCEL, "note": "x"}}

    async def session() -> Any:
        async with Client(_upstream([], answers, LISTING)) as upstream:
            front = await read_proxy(upstream, closed_results=closing)
            async with Client(front) as client:
                return await client.call_tool(*FINDING)

    result = asyncio.run(session())
    assert result.is_error is closing
    if closing:
        assert _refused(result) == (
            "refused: find_parcel returned a key its output schema does not declare, read closed"
        )


RESOURCE = "http://127.0.0.1:8000/mcp"
ISSUER = "https://auth.example/"
METADATA = "http://127.0.0.1:8000/.well-known/oauth-protected-resource/mcp"


class _Tokens:
    """A token verifier reading a table: who each token was issued to, for what, until when."""

    def __init__(self, table: dict[str, AccessToken]) -> None:
        self.table = table

    async def verify_token(self, token: str) -> AccessToken | None:
        return self.table.get(token)


def _issued(client: str, resource: str = RESOURCE, **rest: Any) -> AccessToken:
    given = {"token": client, "client_id": client, "scopes": ["tools"], "resource": resource}
    return AccessToken.model_validate(given | rest)


TOKENS = _Tokens(
    {
        "agent": _issued("agent"),
        "supervisor": _issued("supervisor"),
        "elsewhere": _issued("elsewhere", "http://127.0.0.1:9000/mcp"),
        "expired": _issued("expired", expires_at=1),
        "unscoped": _issued("unscoped", scopes=[]),
    }
)


def _authenticated(front: Server[Any], origins: tuple[str, ...] = ()) -> Any:
    """Return the app serving ``front`` to holders of `TOKENS`, and a client factory into it."""
    app = http_app(
        front, resource=RESOURCE, issuer=ISSUER, verifier=TOKENS, scopes=["tools"], origins=origins
    )

    def client(token: str, base: str = "http://127.0.0.1:8000", **headers: str) -> Client:
        held = {"Authorization": f"Bearer {token}", **headers}
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url=base, headers=held)
        return Client(streamable_http_client(f"{base}/mcp", http_client=http))

    return app, client


def test_a_proxy_over_http_answers_no_request_without_a_token_issued_for_it() -> None:
    async def session() -> Any:
        async with Client(_upstream([], ANSWERS, LISTING)) as upstream:
            front = await read_proxy(upstream)
            app, client = _authenticated(front)
            for token in ("unknown", "elsewhere", "expired"):
                answer = await _async_post(app, {"Authorization": f"Bearer {token}"})
                assert answer.status_code == 401, token
            unscoped = await _async_post(app, {"Authorization": "Bearer unscoped"})
            assert unscoped.status_code == 403
            bare = await _async_post(app, {})
            assert bare.status_code == 401
            assert f'resource_metadata="{METADATA}"' in bare.headers["www-authenticate"]
            async with app.router.lifespan_context(app), client("agent") as agent:
                return await agent.call_tool(*FINDING)

    assert not asyncio.run(session()).is_error


async def _async_post(
    app: Any, headers: dict[str, str], base: str = "http://127.0.0.1:8000"
) -> Any:
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url=base) as http:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        return await http.post("/mcp", json=body, headers=headers)


def test_a_request_from_another_host_or_origin_is_refused() -> None:
    async def session() -> list[int]:
        async with Client(_upstream([], ANSWERS, LISTING)) as upstream:
            front = await read_proxy(upstream)
            app, _ = _authenticated(front, origins=("http://localhost:3000",))
            held = {"Authorization": "Bearer agent"}
            async with app.router.lifespan_context(app):
                return [
                    (await _async_post(app, held, "http://robot.example:8000")).status_code,
                    (await _async_post(app, held | {"Origin": "http://evil.example"})).status_code,
                    (
                        await _async_post(app, held | {"Origin": "http://localhost:3000"})
                    ).status_code,
                ]

    host, origin, allowed = asyncio.run(session())
    assert (host, origin) == (421, 403)
    assert allowed not in (401, 403, 421)


def test_a_proxy_over_http_names_its_authorization_server() -> None:
    async def fetched() -> Any:
        async with Client(_upstream([], ANSWERS, LISTING)) as upstream:
            app, _ = _authenticated(await read_proxy(upstream))
            transport = httpx2.ASGITransport(app)
            async with httpx2.AsyncClient(transport=transport, base_url=RESOURCE) as http:
                return (await http.get(METADATA)).json()

    metadata = asyncio.run(fetched())
    assert metadata["resource"] == RESOURCE
    assert metadata["authorization_servers"] == [ISSUER]


def test_every_authenticated_client_of_one_proxy_calls_through_its_one_gate() -> None:
    made: list[str] = []

    async def session() -> list[Any]:
        async with Client(_upstream(made, ANSWERS, LISTING)) as upstream:
            front = await read_proxy(upstream, policies=[FREIGHT])
            app, client = _authenticated(front)
            async with (
                app.router.lifespan_context(app),
                client("agent") as agent,
                client("supervisor") as supervisor,
            ):
                return [
                    await supervisor.call_tool(*BOOKING),
                    await agent.call_tool(*APPROVAL),
                    await supervisor.call_tool(*BOOKING),
                ]

    unapproved, approved, booked = asyncio.run(session())
    assert _refused(unapproved).startswith(f"refused: {FREIGHT.name}")
    assert not approved.is_error
    assert not booked.is_error
    assert made == ["approve_freight", "book_freight_return"]


def test_a_proxy_s_resource_is_an_http_url() -> None:
    with pytest.raises(ValueError, match="resource is an HTTP URL"):
        http_app(Server("x"), resource="robot", issuer=ISSUER, verifier=TOKENS)
