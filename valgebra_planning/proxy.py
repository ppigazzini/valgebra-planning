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
its one gate, a policy or a flow rule of ``scope`` ``"principal"`` holding each principal its
tokens name apart, and a call a policy reads holds the gate from its check to its result, so each
policy reads such calls in the order they act. MCP 2026-07-28 has no session a client ends, so a
policy's pending states are not read there. A call the upstream answers with a protocol error,
or with a request for input, is answered with a tool error: the proxy gives no input, since a
value it never checked would reach the call, and forwards a request for a person's answer alone,
for a tool its library names, its state sealed and the answers held to what was asked. A call
made whose outcome the gate does not read, its result refused, the call canceled or answered by
no result, may have acted and returned anything, so each policy holds every state the call may
have reached. Requires the ``mcp`` extra.
"""

from __future__ import annotations

import asyncio
import json
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import uvicorn
from mcp import Client, InputRequiredRoundsExceededError, MCPError, types
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import TokenVerifier, principal_components
from mcp.server.auth.settings import AuthSettings
from mcp.server.lowlevel import Server
from mcp.server.request_state import RequestStateBoundary, RequestStateSecurity
from mcp.server.transport_security import TransportSecuritySettings

from valgebra_planning._valgebra import Unsupported, Validator, compile_schema, json_key, read_tool
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


#: How long a forwarded request for input stays answerable, in seconds: a person's answer.
SEALED_SECONDS = 600


def _leaves(value: Any) -> Iterable[Any]:
    """Return every value a result holds, the whole and each part, for provenance."""
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item)


def _principal() -> tuple[str, str | None, str | None] | None:
    """Return the principal the call's token names, its client, issuer and subject, or None.

    The mcp SDK reads a token's principal so for the sessions it owns and the request states it
    binds; a call over stdio carries no token, and its one client is one principal.
    """
    token = get_access_token()
    return None if token is None else principal_components(token)


@dataclass(slots=True)
class Gate:
    """The checks a proxy holds each call to, and the values each untrusted tool returned."""

    contracts: Mapping[str, Contract]
    monitors: list[Monitor]
    flows: Sequence[Flow]
    seen: dict[object, set[str]] = field(default_factory=dict)
    #: The tools a call may be made to after a result took a policy to a bad state, as a robot's
    #: stop: each is a tool no policy reads, so admitting it changes no policy's verdict.
    after_violation: frozenset[str] = frozenset()
    #: The tools whose result is answered with its blocks other than text beside the checked
    #: value, as a camera's image: no check reads them.
    non_text_blocks: frozenset[str] = frozenset()
    #: The set each tool's error text must lie in to be passed on as the server wrote it.
    error_texts: Mapping[str, Validator[Any]] = field(default_factory=dict)
    #: The tools whose requests for a person's answer the proxy forwards to its client.
    forwards_elicitation: frozenset[str] = frozenset()
    #: For a rule with ``fresh`` or ``key``, the values its sources returned, by the rule's name
    #: and the principal it holds them for (None for a shared rule), then by the JSON text of the
    #: key's value the source call took, None for a rule with no key.
    returned: dict[tuple[str, object], dict[object, set[object]]] = field(default_factory=dict)
    #: A policy of ``scope`` ``"principal"``'s monitor for each principal, by its index among
    #: ``monitors``, whose own monitor is never stepped.
    instances: dict[object, dict[int, Monitor]] = field(default_factory=dict)
    #: What the sources of a rule of ``scope`` ``"principal"`` returned, by principal, as
    #: ``seen`` holds it for the shared rules.
    seen_by: dict[object, dict[object, set[str]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse a tool named for its answers or its calls that the proxy lacks, or a policy reads.

        Each of ``after_violation``, ``non_text_blocks``, ``error_texts`` and
        ``forwards_elicitation`` names tools the proxy holds, and a tool of ``after_violation`` is
        one no policy reads.
        """
        named = self.non_text_blocks | self.error_texts.keys() | self.forwards_elicitation
        for tool in sorted(named - self.contracts.keys()):
            msg = f"{tool} is named for its answers, and the proxy holds no such tool"
            raise ValueError(msg)
        for tool in sorted(self.after_violation):
            if tool not in self.contracts:
                msg = f"{tool} is called after a violation, and the proxy holds no such tool"
                raise ValueError(msg)
            hints = self.contracts[tool].hints
            for monitor in self.monitors:
                if any(t.reads(tool, hints) for t in monitor.policy.transitions):
                    msg = (
                        f"{tool} is called after a violation, and {monitor.policy.name} reads it: "
                        "a tool a policy reads would move it after the session stopped"
                    )
                    raise ValueError(msg)

    def policed(self, tool: str) -> bool:
        """Return whether a policy reads a call to ``tool``, by its name or by its hints."""
        contract = self.contracts.get(tool)
        if contract is None:
            return False
        return any(
            transition.reads(tool, contract.hints)
            for monitor in self.monitors
            for transition in monitor.policy.transitions
        )

    def answered(self, tool: str, result: types.CallToolResult, value: Any) -> types.CallToolResult:
        """Return a result whose ``value`` passed every check as the agent reads it.

        Its text is the checked value, its JSON where the tool answered with structured content,
        and no other text the upstream wrote: a client hands a model the text, and the gate
        checks the value. A block that is not text is kept only for a tool of
        ``non_text_blocks``. A tool declaring no output schema answers text no check reads, and
        is answered as the upstream wrote it.
        """
        if self.contracts[tool].returns is None:
            return result
        text = value if result.structured_content is None else json.dumps(value)
        kept = [block for block in result.content if block.type != "text"]
        blocks = kept if tool in self.non_text_blocks else []
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text), *blocks],
            structured_content=result.structured_content,
        )

    def erred(self, tool: str, result: types.CallToolResult) -> types.CallToolResult:
        """Return an error the server answered, as the agent reads it.

        Its text is passed on where it lies in the set ``error_texts`` holds for the tool, as an
        enum of the failures a tool reports, and is otherwise a fixed text naming the tool: no
        contract bounds what a server writes in an error, and a model reads it to recover.
        """
        text = "".join(block.text for block in result.content if block.type == "text")
        allowed = self.error_texts.get(tool)
        if allowed is None or not allowed.is_valid(text):
            text = f"{tool} answered with an error"
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], is_error=True
        )

    @staticmethod
    def answers(tool: str, given: Mapping[str, Any] | None, asked: Mapping[str, Any]) -> str | None:
        """Return why the answers to a forwarded request are refused, or None.

        Each answers a request that was asked, with a person's answer; one a person accepted lies
        in the schema its form asked under, as the elicitation page asks a client to check, and
        one to a URL holds no content. Each failure names the request's key, never the answer.
        """
        for name, response in (given or {}).items():
            if name not in asked:
                return f"{tool} answers {name}, which was not asked"
            if not isinstance(response, types.ElicitResult):
                return f"{tool} answers {name} with no person's answer"
            if response.action != "accept":
                continue
            schema = asked[name]
            if schema is None and response.content is not None:
                return f"{tool} answers {name}, a URL, with content"
            if schema is not None and not compile_schema(schema).is_valid(response.content):
                return f"{tool} answers {name} outside the schema it was asked under"
        return None

    def forwarded(
        self, tool: str, result: types.InputRequiredResult
    ) -> types.CallToolResult | types.InputRequiredResult:
        """Return the upstream's request for input as the proxy forwards it, or a tool error.

        The round ends with the request, and the call may have acted, so each policy reading it
        holds the states before it too, as after an error. A request for a person's answer, a
        form or a URL, is forwarded with a state holding the upstream's own and the schema each
        form asked an answer under (None for a URL), which the server's request-state boundary
        seals (`proxy`); a sampling or a request for roots is not forwarded, a model's text or
        the client's files reaching the call held to no contract.
        """
        if self.policed(tool):
            self.errored()
        asked: dict[str, Any] = {}
        for name, request in (result.input_requests or {}).items():
            if not isinstance(request, types.ElicitRequest):
                text = f"{tool} asked for input other than a person's answer"
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=text)], is_error=True
                )
            form = request.params
            asked[name] = (
                dict(form.requested_schema)
                if isinstance(form, types.ElicitRequestFormParams)
                else None
            )
        state = json.dumps({"state": result.request_state, "asked": asked}, sort_keys=True)
        return types.InputRequiredResult(input_requests=result.input_requests, request_state=state)

    def returned_for(self, flow: Flow, arguments: Mapping[str, Any], value: Any) -> str | None:
        """Return why a rule with ``fresh`` or ``key`` refuses ``value`` at its sink, or None.

        The value is one a source returned since the last call that may act, where the rule is
        ``fresh``, and in the latest answer of a source call that took the sink's call's value at
        the rule's ``key``, where it has one; a value inside the endorsed set passes too.
        """
        if flow.endorsed is not None and flow.endorsed.is_valid(value):
            return None
        kept = self.returned.get(self.holder(flow), {})
        held = (
            kept.get(json_key(arguments.get(flow.key)), set())
            if flow.key
            else set().union(*kept.values())
        )
        if json_key(value) in held:
            return None
        named = ", ".join(sorted(flow.sources))
        when = [
            *(["since the last call that may act"] if flow.fresh else []),
            *([f"for this {flow.key}"] if flow.key else []),
        ]
        said = " and ".join(when)
        return f"flow {flow.name}: {flow.sink} holds a value none of {named} returned {said}"

    def made(self, tool: str) -> None:
        """Withdraw every value a ``fresh`` rule's sources returned: ``tool`` may act.

        A call whose annotations do not say it is read-only may change what a reading reported
        (Mansoor et al. 2026, §4.1), so a value read before it reaches a fresh rule's sink no more.
        Every principal's calls reach the one server, so each principal's values are withdrawn.
        """
        contract = self.contracts.get(tool)
        if contract is not None and "readOnlyHint" in contract.hints:
            return
        fresh = {flow.name for flow in self.flows if flow.fresh}
        for held in [held for held in self.returned if held[0] in fresh]:
            del self.returned[held]

    @staticmethod
    def holder(flow: Flow) -> tuple[str, object]:
        """Return whom a rule's returned values are held for: its name and the call's principal."""
        return flow.name, _principal() if flow.scope == "principal" else None

    def ordered(self, tool: str) -> bool:
        """Return whether a call to ``tool`` is held in order: a policy or an ordered rule reads it.

        A rule with ``fresh`` or ``key`` reads which call came last, so its sources, its sink and,
        for a fresh rule, a call that may act take their place in the order beside a policy's.
        """
        if self.policed(tool):
            return True
        contract = self.contracts.get(tool)
        acts = contract is not None and "readOnlyHint" not in contract.hints
        return any(
            ((flow.fresh or flow.key) and (tool in flow.sources or tool == flow.tool))
            or (flow.fresh and acts)
            for flow in self.flows
        )

    def watching(self) -> list[Monitor]:
        """Return the monitor of each policy a call is read by: its principal's, for a scoped one.

        A policy of ``scope`` ``"principal"`` is a parametric property whose parameter is the
        principal the call's token names: each principal's calls are one instance's trace (Roşu
        and Chen 2012, Def. 12), so a yes one principal relays admits no call of another's. A
        shared policy reads every client's calls as one trace.
        """
        principal = _principal()
        mine = self.instances.setdefault(principal, {})
        return [
            mine.setdefault(at, Monitor(monitor.policy))
            if monitor.policy.scope == "principal"
            else monitor
            for at, monitor in enumerate(self.monitors)
        ]

    def everyone(self) -> list[Monitor]:
        """Return every monitor stepped: each shared policy's, and every principal's instance."""
        shared = [m for m in self.monitors if m.policy.scope != "principal"]
        return shared + [m for mine in self.instances.values() for m in mine.values()]

    def stopped(self, tool: str) -> str | None:
        """Return the bad state a result took a policy to, which refuses a call to ``tool``.

        A result reaches a bad state after its call was made, so no call follows it but one to
        a tool of ``after_violation``, which no policy reads. Every principal's calls reach the
        one server, so a bad state any principal's instance holds stops each of them.
        """
        if tool in self.after_violation:
            return None
        for monitor in self.everyone():
            if held := sorted(monitor.held() & monitor.policy.bad):
                return f"{monitor.policy.name} holds {held[0]}: no call is made after it"
        return None

    def before(self, tool: str, arguments: dict[str, Any]) -> str | None:
        """Return why a call may not be made, or None."""
        contract = self.contracts.get(tool)
        if contract is None:
            return f"{tool} is no tool the proxy holds"
        stopped = self.stopped(tool)
        if stopped is not None:
            return stopped
        if not contract.accepts.is_valid(arguments):
            detail = failures(contract.accepts, arguments, contract_names(contract))
            return f"{tool} refuses its arguments ({detail})"
        flowing = self.flowing(tool, arguments)
        if flowing is not None:
            return flowing
        if not self.policed(tool):
            # No policy reads the call, so it steps none and leaves the states each held before
            # the last call a policy reads, which that call's error reads.
            return None
        # A refused call is never made, so no policy steps on it: the session goes on as before.
        watched = self.watching()
        states = [monitor.snapshot() for monitor in watched]
        for monitor in watched:
            if monitor.observe(tool, arguments, contract.hints):
                for kept, snapshot in zip(watched, states, strict=True):
                    kept.restore(snapshot)
                # Name the calls that move the policy on from where the refusal leaves it, so an
                # agent reads what admits the call, not the state alone.
                ways = monitor.ways_out(tool, contract.hints)
                return "; ".join([monitor.violations[-1], *ways])
        return None

    def flowing(self, tool: str, arguments: dict[str, Any]) -> str | None:
        """Return the flow rule a call breaks, or None."""
        for flow in self.flows:
            if flow.tool != tool or flow.argument not in arguments:
                continue
            value = arguments[flow.argument]
            if flow.fresh or flow.key:
                refused = self.returned_for(flow, arguments, value)
                if refused is not None:
                    return refused
                continue
            seen = self.seen if flow.scope != "principal" else self.seen_by.get(_principal(), {})
            sources = seen.get(json_key(value), set()) & flow.sources
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
        for monitor in self.watching():
            monitor.errored()

    def unread(self, tool: str, arguments: Mapping[str, Any]) -> None:
        """Hold each policy in every state the last call may have reached: no result of it is read.

        The call was made and its outcome is not known: its result was refused, it was canceled,
        or no result answered it. Every later call is refused where any such state reaches a bad
        one, and none is made once one is bad.
        """
        if not self.policed(tool):
            return
        hints = self.contracts[tool].hints
        for monitor in self.watching():
            monitor.unread(tool, arguments, hints)

    def after(
        self, tool: str, value: Any, arguments: Mapping[str, Any] | None = None
    ) -> str | None:
        """Return why a result may not be passed on, or None; remember untrusted tools' values.

        Each policy steps on the result with the call's ``arguments``. A result refused is not
        read: each policy then holds every state a result of the call may reach (`unread`).
        """
        contract = self.contracts[tool]
        if contract.returns is not None and not contract.returns.is_valid(value):
            self.unread(tool, arguments or {})
            if contract.declared is not None and contract.declared.is_valid(value):
                return f"{tool} returned a key its output schema does not declare, read closed"
            detail = failures(contract.returns, value, contract_names(contract))
            return f"{tool} returned a value outside its contract ({detail})"
        for monitor in self.watching():
            if monitor.returned(tool, arguments or {}, value, contract.hints):
                return monitor.violations[-1]
        principal = _principal()
        for scope in sorted({flow.scope for flow in self.flows if tool in flow.sources}):
            seen = self.seen if scope != "principal" else self.seen_by.setdefault(principal, {})
            for leaf in _leaves(value):
                seen.setdefault(json_key(leaf), set()).add(tool)
        leaves = {json_key(leaf) for leaf in _leaves(value)}
        for flow in self.flows:
            if tool in flow.sources and (flow.fresh or flow.key):
                kept = self.returned.setdefault(self.holder(flow), {})
                # A keyed rule holds the latest value a source returned for each key.
                at = json_key((arguments or {}).get(flow.key)) if flow.key else None
                kept[at] = leaves if flow.key else kept.get(at, set()) | leaves
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


def _answered_input(
    gate: Gate, params: types.CallToolRequestParams
) -> types.CallToolResult | tuple[str | None] | None:
    """Return the upstream's state a call's answers resume, or why they are refused.

    A call carrying answers resumes a request the proxy forwarded for its tool, and its answers
    are held to the schemas the request asked under. Its state is the one `Gate.forwarded`
    minted: the request-state boundary verified the client's echo before the call reached here
    and refused one it did not seal, for another call or another principal, or past its expiry.
    A call carrying none resumes nothing, and None is returned.
    """
    if params.request_state is None and params.input_responses is None:
        return None
    if params.name not in gate.forwards_elicitation or params.request_state is None:
        return _refusal(f"{params.name} answers no request the proxy forwarded")
    minted = json.loads(params.request_state)
    refused = gate.answers(params.name, params.input_responses, minted["asked"])
    return _refusal(refused) if refused is not None else (minted["state"],)


async def _made(
    gate: Gate,
    upstream: Client,
    params: types.CallToolRequestParams,
    resumed: tuple[str | None] | None,
) -> types.CallToolResult | types.InputRequiredResult:
    """Make a checked call through ``upstream``, the rounds driven here for a forwarding tool."""
    arguments = dict(params.arguments or {})
    if params.name not in gate.forwards_elicitation:
        return await upstream.call_tool(params.name, arguments)
    # Drive the rounds here, so a request for a person's answer reaches the proxy's client.
    result = await upstream.session.call_tool(
        params.name,
        arguments,
        input_responses=params.input_responses,
        request_state=None if resumed is None else resumed[0],
        allow_input_required=True,
    )
    if not isinstance(result, (types.CallToolResult, types.InputRequiredResult)):
        msg = f"{params.name} answered a result of no kind the proxy reads"
        raise MCPError(-32603, msg)
    return result


def _read(
    gate: Gate,
    tool: str,
    arguments: dict[str, Any],
    result: types.CallToolResult | types.InputRequiredResult,
) -> types.CallToolResult | types.InputRequiredResult:
    """Return what the agent is answered with, the upstream's answer checked."""
    if isinstance(result, types.InputRequiredResult):
        return gate.forwarded(tool, result)
    if result.is_error:
        if gate.policed(tool):
            gate.errored()
        return gate.erred(tool, result)
    value = result.structured_content
    if value is None:
        value = "".join(getattr(block, "text", "") for block in result.content)
    refused = gate.after(tool, value, arguments)
    if refused is not None:
        return _refusal(refused)
    return gate.answered(tool, result, value)


async def _checked(
    gate: Gate, upstream: Client, params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
    """Check a call, make it through ``upstream`` and check its result: the proxy's answer."""
    arguments = dict(params.arguments or {})
    resumed = _answered_input(gate, params)
    if isinstance(resumed, types.CallToolResult):
        return resumed
    refused = gate.before(params.name, arguments)
    if refused is not None:
        return _refusal(refused)
    gate.made(params.name)
    try:
        result = await _made(gate, upstream, params, resumed)
    except asyncio.CancelledError:
        # The client canceled the call while the upstream held it: it may have acted.
        gate.unread(params.name, arguments)
        raise
    except (MCPError, InputRequiredRoundsExceededError) as error:
        gate.unread(params.name, arguments)
        # The message is the server's or the client's, text no contract bounds: name the code.
        said = (
            f"error {error.error.code}"
            if isinstance(error, MCPError)
            else "it asked for input, which the proxy gives none of"
        )
        text = types.TextContent(type="text", text=f"{params.name} did not answer: {said}")
        return types.CallToolResult(content=[text], is_error=True)
    except RuntimeError as error:
        if not refused_by_client(error, params.name):
            raise
        gate.unread(params.name, arguments)
        # The client's text holds the value, so the refusal names the tool alone.
        refused = f"{params.name} returned a value outside its contract, refused by the client"
        return _refusal(refused)
    return _read(gate, params.name, arguments, result)


def proxy(
    upstream: Client,
    listing: Sequence[types.Tool],
    contracts: Mapping[str, Contract],
    *,
    policies: Sequence[Policy] = (),
    flows: Sequence[Flow] = (),
    after_violation: Iterable[str] = (),
    non_text_blocks: Iterable[str] = (),
    error_texts: Mapping[str, object] | None = None,
    forwards_elicitation: Iterable[str] = (),
) -> Server[Any]:
    """Return an MCP server answering ``listing`` through ``upstream``, every call checked.

    A call a policy reads holds the gate from its check to its result: an action's effect and
    its response travel apart, and a response may arrive after a later call is sent (Mansoor et
    al. 2026, §4.1), so a policy reading two calls in flight would read a call against states
    the other's result has not yet stepped, and an error against the states before the other.
    Each policy reads such calls in the order they act. A call no policy reads waits on none.
    A call holds the gate while the upstream holds it, so ``upstream`` is a client with a read
    timeout, as `valgebra_planning.servers.connect` takes one.

    Once a result takes a policy to a bad state every later call is refused but one to a tool
    of ``after_violation``, as a robot's stop; `Gate` refuses such a tool a policy reads. A
    result's text is the checked value alone, and a block that is not text is kept only for a
    tool of ``non_text_blocks`` (`Gate.answered`). An error's text is passed on where it lies
    in the JSON Schema ``error_texts`` gives its tool, and is otherwise a fixed text naming the
    tool (`Gate.erred`). A call to a tool of ``forwards_elicitation`` that the upstream answers
    by asking a person, a form or a URL, is answered with that request, and the answers that
    resume it are held to what was asked (`Gate.forwarded`). A client must not change a
    request's state, and a server protects its integrity (MCP 2026-07-28, the multi round-trip
    pattern), so the server carries the mcp SDK's request-state boundary: a state leaves sealed
    under a key the process alone holds, bound to the call's tool and arguments, to the principal
    the token names, its client, issuer and subject, and to an expiry of ``SEALED_SECONDS``, and
    an echo it did not seal so is answered with a protocol error before any check reads it.
    """
    gate = Gate(
        contracts,
        [Monitor(policy) for policy in policies],
        flows,
        after_violation=frozenset(after_violation),
        non_text_blocks=frozenset(non_text_blocks),
        error_texts={tool: compile_schema(schema) for tool, schema in (error_texts or {}).items()},
        forwards_elicitation=frozenset(forwards_elicitation),
    )
    held = asyncio.Lock()

    async def listed(_: Any, __: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(listing))

    async def called(
        _: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult | types.InputRequiredResult:
        if not gate.ordered(params.name):
            return await _checked(gate, upstream, params)
        async with held:
            return await _checked(gate, upstream, params)

    server = Server("valgebra-planning proxy", on_list_tools=listed, on_call_tool=called)
    security = RequestStateSecurity.ephemeral(ttl=SEALED_SECONDS)
    server.middleware.append(RequestStateBoundary(security, default_audience=server.name))
    return server


async def read_proxy(
    upstream: Client,
    *,
    policies: Sequence[Policy] = (),
    flows: Sequence[Flow] = (),
    assert_format: bool = False,
    closed_results: bool = False,
    spell_out: bool = False,
    after_violation: Iterable[str] = (),
    non_text_blocks: Iterable[str] = (),
    error_texts: Mapping[str, object] | None = None,
    forwards_elicitation: Iterable[str] = (),
) -> Server[Any]:
    """Return the proxy in front of the server ``upstream`` is connected to, its listing read once.

    The tools it lists and the contracts its gate holds are read from one listing
    (`valgebra_planning.servers.read_listing`), which refuses a cursor named twice and a listing
    past its page bound.

    Its tools are read with ``assert_format`` and ``closed_results`` as
    `valgebra_planning.servers.read_server` reads them, so a proxy held to a library's rules
    reads the server's tools as the library does. With ``spell_out``, each tool is listed as
    `spelled_out` returns it, and one it returns None for is listed as published, with a
    `UserWarning` naming it; the gate holds every call to the published schemas. A call to a
    tool of ``after_violation`` is admitted after a violation, and a tool of
    ``non_text_blocks`` answers its blocks other than text, and an error's text is held to
    ``error_texts``, and a tool of ``forwards_elicitation`` forwards a request for a person's
    answer, as `proxy` reads them.
    """
    from valgebra_planning.servers import read_listing, read_server  # noqa: PLC0415

    published = await read_listing(upstream)
    contracts = await read_server(
        upstream,
        assert_format=assert_format,
        closed_results=closed_results,
        listing=published,
    )
    listing = (
        [_listed(tool, assert_format=assert_format) for tool in published]
        if spell_out
        else published
    )
    return proxy(
        upstream,
        listing,
        contracts,
        policies=policies,
        flows=flows,
        after_violation=after_violation,
        non_text_blocks=non_text_blocks,
        error_texts=error_texts,
        forwards_elicitation=forwards_elicitation,
    )


async def serve(
    upstream: Client,
    *,
    policies: Sequence[Policy] = (),
    flows: Sequence[Flow] = (),
    assert_format: bool = False,
    closed_results: bool = False,
    spell_out: bool = False,
    after_violation: Iterable[str] = (),
    non_text_blocks: Iterable[str] = (),
    error_texts: Mapping[str, object] | None = None,
    forwards_elicitation: Iterable[str] = (),
) -> None:
    """Serve the proxy over stdio in front of the server ``upstream`` is connected to.

    The proxy is `read_proxy`'s, its tools read with ``assert_format`` and ``closed_results``,
    listed spelled out with ``spell_out``, those of ``after_violation`` called after a
    violation, those of ``non_text_blocks`` answering their blocks other than text, and each
    error's text held to ``error_texts``, and a request for a person's answer forwarded for a
    tool of ``forwards_elicitation``.
    """
    from mcp.server.stdio import stdio_server  # noqa: PLC0415 - only a served proxy needs it

    server = await read_proxy(
        upstream,
        policies=policies,
        flows=flows,
        assert_format=assert_format,
        closed_results=closed_results,
        spell_out=spell_out,
        after_violation=after_violation,
        non_text_blocks=non_text_blocks,
        error_texts=error_texts,
        forwards_elicitation=forwards_elicitation,
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
    server on a connection of its own. Every client calls through the one gate ``server`` holds,
    and a rule of ``scope`` ``"principal"`` there reads each token's principal apart.
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
