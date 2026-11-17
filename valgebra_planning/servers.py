"""Tools read from a live MCP server, and a program run against it.

`read_server` pages through a server's ``tools/list`` and reads each tool into its contract, as
`valgebra_planning.contracts.read_contract` reads a listing on disk. `run_on_server` runs a
program with `valgebra_planning.run.run`, each call a ``tools/call`` through the client: the
result's ``structuredContent`` is the value a call returns, an ``isError`` result is a
`valgebra_planning.run.ToolError`, and a tool returning no structured content returns the text
of its content blocks. The client runs the protocol's own rounds of a multi round-trip request.

Requires the ``mcp`` extra. The runner is synchronous and the client asynchronous, so the run
goes on a worker thread and each call is sent to the client's event loop.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from mcp import Client, StdioServerParameters

from valgebra_planning.contracts import Contract, Task, read_contract
from valgebra_planning.policy import Policy
from valgebra_planning.program import Block
from valgebra_planning.run import Run, ToolError, run

__all__ = ["connect", "read_server", "run_on_server"]


def connect(server: str) -> Client:
    """Return a client for a server: a URL is Streamable HTTP, anything else a stdio command."""
    if server.startswith(("http://", "https://")):
        return Client(server)
    command, *arguments = shlex.split(server)
    return Client(StdioServerParameters(command=command, args=arguments))


async def read_server(client: Client) -> dict[str, Contract]:
    """Return the contract of every tool the server lists, by name, every page read."""
    contracts: dict[str, Contract] = {}
    cursor: str | None = None
    while True:
        listing = await client.list_tools(cursor=cursor)
        for tool in listing.tools:
            spec = tool.model_dump(by_alias=True, exclude_none=True, mode="json")
            contracts[tool.name] = read_contract(spec)
        cursor = listing.next_cursor
        if cursor is None:
            return contracts


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
    """Run ``program`` against the server ``client`` is connected to, failing closed."""
    loop = asyncio.get_running_loop()

    def caller(name: str) -> Callable[..., Any]:
        def call(**arguments: Any) -> Any:
            sent = asyncio.run_coroutine_threadsafe(client.call_tool(name, arguments), loop)
            return _value(sent.result())

        return call

    tools = {name: caller(name) for name in contracts}
    return await asyncio.to_thread(
        run, program, contracts, task, inputs, tools, policies=policies, enforce=enforce
    )
