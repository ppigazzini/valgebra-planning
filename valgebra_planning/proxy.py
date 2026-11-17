"""An MCP server in front of another, holding every call to the contracts, policies and flows.

The proxy lists the upstream server's tools as they are, and answers each ``tools/call`` only
after checking it, as the fail-closed runner checks a call: the argument record against the
tool's input set, each policy's step, and, the call forwarded, the result against its output
set. A check that breaks answers the call with a tool error naming it, as Progent's proxy does,
so the agent can try another way; nothing breaking it reaches the upstream server or the agent.

A flow rule is held by provenance read from values: the proxy sees values, not a program's
references, so an argument equal, as one JSON value, to a value an untrusted tool returned is
taken as coming from it, and refused unless it lies in the rule's endorsed set. A rule with
``only`` refuses an argument equal to no value its sources returned: an identifier not retrieved
earlier in the session, the grounding gate Sah et al. 2026 ask for. Both are assumptions: a
value equal by chance to an untrusted one is refused with it, and one equal by chance to a
retrieved one passes. A proxy sees no task, so a value from a task input is from elsewhere.
A session has no end a proxy sees, so a policy's pending states are not read there.
Requires the ``mcp`` extra.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from mcp import Client, types
from mcp.server.lowlevel import Server

from valgebra_planning._valgebra import json_key
from valgebra_planning.contracts import Contract
from valgebra_planning.flows import Flow
from valgebra_planning.policy import Monitor, Policy

__all__ = ["Gate", "proxy", "serve"]


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
        if not contract.accepts.is_valid(arguments):
            return f"{tool} refuses its arguments {arguments!r:.120}"
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
        # A refused call is never made, so no policy steps on it: the session goes on as before.
        states = [monitor.state for monitor in self.monitors]
        for monitor in self.monitors:
            if monitor.observe(tool, arguments, contract.hints):
                for kept, state in zip(self.monitors, states, strict=True):
                    kept.state = state
                return monitor.violations[-1]
        return None

    def after(self, tool: str, value: Any) -> str | None:
        """Return why a result may not be passed on, or None; remember untrusted tools' values."""
        contract = self.contracts[tool]
        if contract.returns is not None and not contract.returns.is_valid(value):
            return f"{tool} returned {value!r:.120}, outside its contract"
        if any(tool in flow.sources for flow in self.flows):
            for leaf in _leaves(value):
                self.seen.setdefault(json_key(leaf), set()).add(tool)
        return None


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
        result = await upstream.call_tool(params.name, arguments)
        if result.is_error:
            return result
        value = result.structured_content
        if value is None:
            value = "".join(getattr(block, "text", "") for block in result.content)
        refused = gate.after(params.name, value)
        return _refusal(refused) if refused is not None else result

    return Server("valgebra-planning proxy", on_list_tools=listed, on_call_tool=called)


async def serve(
    upstream: Client, *, policies: Sequence[Policy] = (), flows: Sequence[Flow] = ()
) -> None:
    """Serve the proxy over stdio in front of the server ``upstream`` is connected to."""
    from mcp.server.stdio import stdio_server  # noqa: PLC0415 - only a served proxy needs it

    from valgebra_planning.servers import read_server  # noqa: PLC0415

    listing: list[types.Tool] = []
    cursor: str | None = None
    while True:
        page = await upstream.list_tools(cursor=cursor)
        listing += page.tools
        cursor = page.next_cursor
        if cursor is None:
            break
    contracts = await read_server(upstream)
    server = proxy(upstream, listing, contracts, policies=policies, flows=flows)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
