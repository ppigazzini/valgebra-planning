"""An MCP server in front of another, holding every call to the contracts, policies and flows.

The proxy lists the upstream server's tools as they are, or, with ``spell_out``, each input
schema spelled out for a model's chat template where valgebra proves the two equal
(`spelled_out`), and answers each ``tools/call`` only after checking it, as the fail-closed
runner checks a call: the argument record against the tool's input set, each policy's step,
and, the call forwarded, the result against its output set. A check that breaks answers the
call with a tool error naming it, as Progent's proxy mode answers a denied call with its
fallback message (Shi et al. 2025, §4.1, §7), so the agent can try another way; nothing
breaking it reaches the upstream server or the agent.

A flow rule is held by provenance read from values: the proxy sees values, not a program's
references, so an argument equal, as one JSON value, to a value an untrusted tool returned is
taken as coming from it, and refused unless it lies in the rule's endorsed set. A rule with
``only`` refuses an argument equal to no value its sources returned: an identifier not retrieved
earlier in the proxy's process, the strict grounding gate (Sah et al. 2026, §4.8). Both are
assumptions: a value equal by chance to an untrusted one is refused with it, and one equal by
chance to a retrieved one passes, and a value the agent transforms, upper-cased, encoded or cut,
equals none it was returned and passes: a flow rule here holds against no attacker that adapts,
where the contract and policy checks hold against any. A proxy sees no task, so a value from a
task input is from elsewhere. The proxy serves the agent that starts it over stdio, or, through
`http_app`, every client holding a token issued for it over Streamable HTTP, all of them through
its one gate. MCP 2026-07-28 has no session a client ends, so a policy's pending states are not
read there. A call the upstream answers with a protocol error, or with a request for input, is
answered with a tool error: the proxy gives no input and forwards no request for one, since a
value it never checked would reach the call. Requires the ``mcp`` extra.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import uvicorn
from mcp import Client, InputRequiredRoundsExceededError, MCPError, types
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings

from valgebra_planning._valgebra import Unsupported, json_key, read_tool
from valgebra_planning.contracts import Contract, contract_names, failures
from valgebra_planning.flows import Flow
from valgebra_planning.policy import Monitor, Policy
from valgebra_planning.servers import refused_by_client

if TYPE_CHECKING:
    from starlette.applications import Starlette

__all__ = ["Gate", "http_app", "proxy", "read_proxy", "serve", "serve_http", "spelled_out"]

#: The keywords whose value is one schema, in JSON Schema 2020-12 and draft-07.
_ONE = frozenset(
    {
        "additionalItems",
        "additionalProperties",
        "contains",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
#: The keywords whose value maps names to schemas.
_NAMED = frozenset(
    {"$defs", "definitions", "dependencies", "dependentSchemas", "patternProperties", "properties"}
)
#: The keywords whose value lists schemas; draft-07's ``items`` may be one of them.
_LISTED = frozenset({"allOf", "anyOf", "items", "oneOf", "prefixItems"})


def _leaves(value: Any) -> Iterable[Any]:
    """Return every value a result holds, the whole and each part, for provenance."""
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item)


@dataclass(slots=True)
class Gate:
    """The checks a proxy holds each call to, and the values each untrusted tool returned."""

    contracts: Mapping[str, Contract]
    monitors: list[Monitor]
    flows: Sequence[Flow]
    seen: dict[object, set[str]] = field(default_factory=dict)

    def before(self, tool: str, arguments: dict[str, Any]) -> str | None:
        """Return why a call may not be made, or None."""
        contract = self.contracts.get(tool)
        if contract is None:
            return f"{tool} is no tool the proxy holds"
        for monitor in self.monitors:
            # A result took the policy to a bad state after its call was made: no call follows.
            if held := sorted(monitor.held() & monitor.policy.bad):
                return f"{monitor.policy.name} holds {held[0]}: no call is made after it"
        if not contract.accepts.is_valid(arguments):
            detail = failures(contract.accepts, arguments, contract_names(contract))
            return f"{tool} refuses its arguments ({detail})"
        flowing = self.flowing(tool, arguments)
        if flowing is not None:
            return flowing
        # A refused call is never made, so no policy steps on it: the session goes on as before.
        states = [monitor.snapshot() for monitor in self.monitors]
        for monitor in self.monitors:
            if monitor.observe(tool, arguments, contract.hints):
                for kept, snapshot in zip(self.monitors, states, strict=True):
                    kept.restore(snapshot)
                return monitor.violations[-1]
        return None

    def flowing(self, tool: str, arguments: dict[str, Any]) -> str | None:
        """Return the flow rule a call breaks, or None."""
        for flow in self.flows:
            if flow.tool != tool or flow.argument not in arguments:
                continue
            value = arguments[flow.argument]
            sources = self.seen.get(json_key(value), set()) & flow.sources
            # A value equal to one a source returned is taken as that source's, and any other
            # as from elsewhere: the rule holds the first, or with ``only`` the second.
            if bool(sources) == flow.only or (
                flow.endorsed is not None and flow.endorsed.is_valid(value)
            ):
                continue
            if flow.only:
                named = ", ".join(sorted(flow.sources))
                return f"flow {flow.name}: {flow.sink} holds a value none of {named} returned"
            named = ", ".join(sorted(sources))
            return f"flow {flow.name}: {flow.sink} holds a value {named} returned"
        return None

    def errored(self) -> None:
        """Hold each policy in the states before the last call too: it answered with an error."""
        for monitor in self.monitors:
            monitor.errored()

    def after(
        self, tool: str, value: Any, arguments: Mapping[str, Any] | None = None
    ) -> str | None:
        """Return why a result may not be passed on, or None; remember untrusted tools' values.

        Each policy steps on the result with the call's ``arguments``.
        """
        contract = self.contracts[tool]
        if contract.returns is not None and not contract.returns.is_valid(value):
            if contract.declared is not None and contract.declared.is_valid(value):
                return f"{tool} returned a key its output schema does not declare, read closed"
            detail = failures(contract.returns, value, contract_names(contract))
            return f"{tool} returned a value outside its contract ({detail})"
        for monitor in self.monitors:
            if monitor.returned(tool, arguments or {}, value, contract.hints):
                return monitor.violations[-1]
        if any(tool in flow.sources for flow in self.flows):
            for leaf in _leaves(value):
                self.seen.setdefault(json_key(leaf), set()).add(tool)
        return None


def _kind(value: Any) -> str:
    """Return the JSON Schema type whose instances include ``value``."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def _noted(schema: Mapping[str, Any], note: str) -> str:
    """Return the schema's description with ``note`` after it, or ``note`` where it has none."""
    described = schema.get("description")
    return f"{described}; {note}" if isinstance(described, str) and described else note


def _below(keyword: str, value: Any) -> Any:
    """Return the value of ``keyword`` with every schema it holds spelled out."""
    if keyword in _NAMED and isinstance(value, dict):
        return {name: _spelled(item) for name, item in value.items()}
    if keyword in _LISTED and isinstance(value, list):
        return [_spelled(item) for item in value]
    return _spelled(value) if keyword in _ONE else value


def _spelled(schema: Any) -> Any:
    """Return ``schema`` with each ``const`` spelled out and each ``pattern`` described.

    A ``const`` becomes its value's ``type`` and a one-value ``enum``, which the specification
    states equivalent (JSON Schema 2020-12 Validation, §6.1.3), and a ``description`` naming the
    value; a ``pattern`` stays and is named in the ``description``, an annotation. A ``const``
    beside an ``enum`` stays as written, since one schema holds one ``enum``.
    """
    if not isinstance(schema, dict):
        return schema
    spelled = {keyword: _below(keyword, value) for keyword, value in schema.items()}
    if "const" in spelled and "enum" not in spelled:
        value = spelled.pop("const")
        spelled.setdefault("type", _kind(value))
        spelled["enum"] = [value]
        spelled["description"] = _noted(spelled, f"always {json.dumps(value, sort_keys=True)}")
    if isinstance(pattern := spelled.get("pattern"), str):
        spelled["description"] = _noted(spelled, f"matches {pattern}")
    return spelled


def spelled_out(tool: types.Tool, *, assert_format: bool = False) -> types.Tool | None:
    """Return ``tool`` with its input schema spelled out for a model's template, or None.

    A chat template that renders a tool's parameters may drop a ``const`` or a ``pattern``, so
    the model never reads the value a call needs. The tool returned writes each ``const`` as
    its value's ``type``, a one-value ``enum`` and a ``description``, and names each
    ``pattern`` in a ``description``, an upstream description kept before it. It is returned
    only where valgebra proves the arguments it admits equal, both ways, to the published
    ones, read as `valgebra_planning.contracts.read_contract` reads them with
    ``assert_format``; None where it does not. A tool holding neither keyword is returned as
    it is. The output schema stays as published.
    """
    published = tool.model_dump(by_alias=True, exclude_none=True, mode="json")
    spelled = _spelled(published["inputSchema"])
    if spelled == published["inputSchema"]:
        return tool
    before = read_tool(published, assert_format=assert_format).arguments
    try:
        after = read_tool(published | {"inputSchema": spelled}, assert_format=assert_format)
    except Unsupported:
        return None
    if before.relation_to(after.arguments) != "subset":
        return None
    if after.arguments.relation_to(before) != "subset":
        return None
    return tool.model_copy(update={"input_schema": spelled})


def _listed(tool: types.Tool, *, assert_format: bool) -> types.Tool:
    """Return the tool spelled out, or as published, with a warning naming it, where unproven."""
    spelled = spelled_out(tool, assert_format=assert_format)
    if spelled is not None:
        return spelled
    message = (
        f"{tool.name} is listed as published: valgebra does not prove its input schema "
        "spelled out equal to it"
    )
    # In a coroutine no caller's frame is on the stack: name `read_proxy`, which lists.
    warnings.warn(message, stacklevel=2)
    return tool


def _refusal(reason: str) -> types.CallToolResult:
    text = types.TextContent(type="text", text=f"refused: {reason}")
    return types.CallToolResult(content=[text], is_error=True)


def proxy(
    upstream: Client,
    listing: Sequence[types.Tool],
    contracts: Mapping[str, Contract],
    *,
    policies: Sequence[Policy] = (),
    flows: Sequence[Flow] = (),
) -> Server[Any]:
    """Return an MCP server answering ``listing`` through ``upstream``, every call checked."""
    gate = Gate(contracts, [Monitor(policy) for policy in policies], flows)

    async def listed(_: Any, __: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(listing))

    async def called(_: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        arguments = dict(params.arguments or {})
        refused = gate.before(params.name, arguments)
        if refused is not None:
            return _refusal(refused)
        try:
            result = await upstream.call_tool(params.name, arguments)
        except (MCPError, InputRequiredRoundsExceededError) as error:
            gate.errored()
            text = types.TextContent(type="text", text=f"{params.name} did not answer: {error}")
            return types.CallToolResult(content=[text], is_error=True)
        except RuntimeError as error:
            if not refused_by_client(error, params.name):
                raise
            # The client's text holds the value, so the refusal names the tool alone.
            refused = f"{params.name} returned a value outside its contract, refused by the client"
            return _refusal(refused)
        if result.is_error:
            gate.errored()
            return result
        value = result.structured_content
        if value is None:
            value = "".join(getattr(block, "text", "") for block in result.content)
        refused = gate.after(params.name, value, arguments)
        return _refusal(refused) if refused is not None else result

    return Server("valgebra-planning proxy", on_list_tools=listed, on_call_tool=called)


async def read_proxy(
    upstream: Client,
    *,
    policies: Sequence[Policy] = (),
    flows: Sequence[Flow] = (),
    assert_format: bool = False,
    closed_results: bool = False,
    spell_out: bool = False,
) -> Server[Any]:
    """Return the proxy in front of the server ``upstream`` is connected to, every page listed.

    Its tools are read with ``assert_format`` and ``closed_results`` as
    `valgebra_planning.servers.read_server` reads them, so a proxy held to a library's rules
    reads the server's tools as the library does. With ``spell_out``, each tool is listed as
    `spelled_out` returns it, and one it returns None for is listed as published, with a
    `UserWarning` naming it; the gate holds every call to the published schemas.
    """
    from valgebra_planning.servers import read_server  # noqa: PLC0415

    listing: list[types.Tool] = []
    cursor: str | None = None
    while True:
        page = await upstream.list_tools(cursor=cursor)
        listing += page.tools
        cursor = page.next_cursor
        if cursor is None:
            break
    if spell_out:
        listing = [_listed(tool, assert_format=assert_format) for tool in listing]
    contracts = await read_server(
        upstream, assert_format=assert_format, closed_results=closed_results
    )
    return proxy(upstream, listing, contracts, policies=policies, flows=flows)


async def serve(
    upstream: Client,
    *,
    policies: Sequence[Policy] = (),
    flows: Sequence[Flow] = (),
    assert_format: bool = False,
    closed_results: bool = False,
    spell_out: bool = False,
) -> None:
    """Serve the proxy over stdio in front of the server ``upstream`` is connected to.

    The proxy is `read_proxy`'s, its tools read with ``assert_format`` and ``closed_results``,
    and listed spelled out with ``spell_out``.
    """
    from mcp.server.stdio import stdio_server  # noqa: PLC0415 - only a served proxy needs it

    server = await read_proxy(
        upstream,
        policies=policies,
        flows=flows,
        assert_format=assert_format,
        closed_results=closed_results,
        spell_out=spell_out,
    )
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def http_app(
    server: Server[Any],
    *,
    resource: str,
    issuer: str,
    verifier: TokenVerifier,
    scopes: Sequence[str] = (),
    origins: Sequence[str] = (),
) -> Starlette:
    """Return an ASGI app serving ``server`` over Streamable HTTP to authenticated clients alone.

    The app is an OAuth 2.1 resource server, as MCP 2026-07-28 has an HTTP server authorize:
    every request carries a bearer token, which ``verifier`` reads, issued for ``resource``, the
    proxy's canonical URL, as its audience, unexpired, and holding every scope of ``scopes``.
    Any other request is answered 401, or 403 where a scope is missing, naming the protected
    resource metadata that points the
    client at ``issuer``, the authorization server. A request whose Host is not the resource's is
    answered 421, and one carrying an Origin not among ``origins`` 403, the check MCP asks of
    every connection against DNS rebinding. A token reaches no upstream: the proxy reaches its
    server on a connection of its own. Every client calls through the one gate ``server`` holds.
    Raises `ValueError` for a resource that is no HTTP URL.
    """
    where = urlsplit(resource)
    if where.scheme not in {"http", "https"} or not where.hostname:
        msg = f"the proxy's resource is an HTTP URL, not {resource!r}"
        raise ValueError(msg)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[where.netloc],
        allowed_origins=list(origins),
    )
    auth = AuthSettings.model_validate(
        {
            "issuer_url": issuer,
            "resource_server_url": resource,
            "required_scopes": list(scopes) or None,
            "validate_token_resource": True,
        }
    )
    return server.streamable_http_app(
        streamable_http_path=where.path or "/",
        transport_security=security,
        host=where.hostname,
        auth=auth,
        token_verifier=verifier,
    )


async def serve_http(
    server: Server[Any],
    *,
    host: str,
    port: int,
    resource: str,
    issuer: str,
    verifier: TokenVerifier,
    scopes: Sequence[str] = (),
    origins: Sequence[str] = (),
) -> None:
    """Serve ``server``, a proxy `read_proxy` returns, on ``host`` and ``port`` as `http_app` does.

    It serves until its process is stopped.
    """
    app = http_app(
        server, resource=resource, issuer=issuer, verifier=verifier, scopes=scopes, origins=origins
    )
    await uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning")).serve()
