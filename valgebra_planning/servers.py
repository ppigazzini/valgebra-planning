"""Tools read from a live MCP server, and a program run against it.

`read_server` pages through a server's ``tools/list`` and reads each tool into its contract, as
`valgebra_planning.contracts.read_contract` reads a listing on disk. `run_on_server` runs a
program with `valgebra_planning.run.run`, each call a ``tools/call`` through the client: the
result's ``structuredContent`` is the value a call returns, an ``isError`` result is a
`valgebra_planning.run.ToolError`, and a tool returning no structured content returns the text
of its content blocks. A result the mcp client refuses against the output schema the server
declares ends the run on the `result` check, naming the tool and never the value. A call the
server answers with a protocol error is a tool error too, and
so is one asking for input (MCP 2026-07-28's ``input_required``: a sampling, an elicitation, the
roots, or an interaction it waits on): the client gives none, so no value a program did not hold
reaches a call.

Requires the ``mcp`` extra. The runner is synchronous and the client asynchronous, so the run
goes on a worker thread and each call is sent to the client's event loop. Canceling the task that
awaits `run_on_server` cancels the call in flight and sends no call after it.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import shlex
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from mcp import Client, InputRequiredRoundsExceededError, MCPError, StdioServerParameters

from valgebra_planning.contracts import Contract, Task, read_contract
from valgebra_planning.policy import Policy
from valgebra_planning.program import Block
from valgebra_planning.run import RefusedResultError, Run, ToolError, run

__all__ = ["connect", "read_server", "refused_by_client", "run_on_server"]

#: How the mcp client refuses a result against the output schema the server declares, before it
#: hands the result back (``ClientSession.validate_tool_result``): its text holds the value, so
#: the refusal is read here and its text is never passed on.
_CLIENT_REFUSALS = ("Invalid structured content returned by tool ", "Invalid schema for tool ")


def connect(server: str, *, read_timeout_seconds: float | None = None) -> Client:
    """Return a client for a server: a URL is Streamable HTTP, anything else a stdio command.

    The command is split as a POSIX shell splits it (``shlex.split``), on Windows as well: a
    backslash escapes the character after it, so a Windows path is passed quoted, as
    ``shlex.quote`` quotes it, the way pre-commit quotes the interpreter in a hook's ``entry``.
    With ``read_timeout_seconds``, a request the server has not answered by then fails as a
    protocol error, so a call fails as a tool error; with none, a request waits for its answer.
    """
    if server.startswith(("http://", "https://")):
        return Client(server, read_timeout_seconds=read_timeout_seconds)
    command, *arguments = shlex.split(server)
    parameters = StdioServerParameters(command=command, args=arguments)
    return Client(parameters, read_timeout_seconds=read_timeout_seconds)


async def read_server(
    client: Client, *, assert_format: bool = False, closed_results: bool = False
) -> dict[str, Contract]:
    """Return the contract of every tool the server lists, by name, every page read.

    With ``assert_format``, ``format`` is read as an assertion, and with ``closed_results``
    each whole result is read closed, as `read_contract` reads them.
    """
    contracts: dict[str, Contract] = {}
    cursor: str | None = None
    while True:
        listing = await client.list_tools(cursor=cursor)
        for tool in listing.tools:
            spec = tool.model_dump(by_alias=True, exclude_none=True, mode="json")
            contracts[tool.name] = read_contract(
                spec, assert_format=assert_format, closed_results=closed_results
            )
        cursor = listing.next_cursor
        if cursor is None:
            return contracts


def refused_by_client(error: RuntimeError, tool: str) -> bool:
    """Return whether ``error`` is the mcp client refusing ``tool``'s result against its schema."""
    text = str(error)
    missing = f"Tool {tool} has an output schema but did not return structured content"
    return text.startswith(_CLIENT_REFUSALS) or text == missing


def _value(result: Any) -> Any:
    """Return what a ``tools/call`` result answers: its structured content, else its text."""
    text = "".join(getattr(block, "text", "") for block in result.content)
    if result.is_error:
        raise ToolError(text)
    return text if result.structured_content is None else result.structured_content


async def run_on_server(
    program: Block,
    contracts: Mapping[str, Contract],
    task: Task,
    inputs: Mapping[str, Any],
    client: Client,
    *,
    policies: Sequence[Policy] = (),
    enforce: bool = True,
) -> Run:
    """Run ``program`` against the server ``client`` is connected to, failing closed.

    Canceling the task that awaits it cancels the call in flight and sends no call after it.
    """
    loop = asyncio.get_running_loop()
    # The lock orders a call's send against the cancel: a call is sent before the cancel, and
    # is the call in flight it cancels, or after it, and is never sent.
    guard = threading.Lock()
    canceled = threading.Event()
    flying: list[concurrent.futures.Future[Any]] = []

    def caller(name: str) -> Callable[..., Any]:
        def call(**arguments: Any) -> Any:
            with guard:
                if canceled.is_set():
                    raise asyncio.CancelledError
                sent = asyncio.run_coroutine_threadsafe(client.call_tool(name, arguments), loop)
                flying[:] = [sent]
            try:
                result = sent.result()
            except concurrent.futures.CancelledError:
                # The call in flight was canceled with the run: the run ends here.
                raise asyncio.CancelledError from None
            except (MCPError, InputRequiredRoundsExceededError) as error:
                # Fail the call, as an error block or a refusal reads it: a raise would end the
                # run with no record of which call broke and why.
                message = f"{name} did not answer: {error}"
                raise ToolError(message) from error
            except RuntimeError as error:
                if not refused_by_client(error, name):
                    raise
                # The client's text holds the value: it is dropped, chain and all.
                raise RefusedResultError(name) from None
            return _value(result)

        return call

    tools = {name: caller(name) for name in contracts}
    try:
        return await asyncio.to_thread(
            run, program, contracts, task, inputs, tools, policies=policies, enforce=enforce
        )
    except asyncio.CancelledError:
        with guard:
            canceled.set()
            for sent in flying:
                sent.cancel()
        raise
