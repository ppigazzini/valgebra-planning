"""Serve the parcel tools over stdio, for the command line's tests.

    python tests/parcel_server.py [--note]

``find_parcel`` answers a delivered parcel of 500 g, and with ``--note`` a ``note`` beside it,
a key its output schema does not declare; every other tool answers the first value drawn from
its own output schema with seed 0.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from valgebra_planning._valgebra import Sampler
from valgebra_planning.contracts import read_contract

LISTING = json.loads((Path(__file__).parent.parent / "examples/parcels/tools.json").read_text())
TOOLS = {contract.name: contract for contract in map(read_contract, LISTING["tools"])}
PARCEL = {
    "parcel_id": "pcl_1",
    "state": "delivered",
    "weight_g": 500,
    "country": "IT",
    "contact": "a@b.c",
}


def answer(name: str) -> Any:
    """Return what the tool ``name`` answers."""
    if name == "find_parcel":
        return {"parcel": PARCEL} | ({"note": "left at the door"} if "--note" in sys.argv else {})
    whole = TOOLS[name].result()
    return Sampler(whole.root, random.Random(0)).draw(whole.schema, 1)[0]


async def listed(_: Any, __: Any) -> types.ListToolsResult:
    """List the parcel tools."""
    return types.ListToolsResult(tools=[types.Tool.model_validate(t) for t in LISTING["tools"]])


async def called(_: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
    """Answer one call."""
    value = answer(params.name)
    text = types.TextContent(type="text", text=json.dumps(value))
    return types.CallToolResult(content=[text], structured_content=value)


async def main() -> None:
    """Serve until the client closes stdin."""
    server = Server("parcels", on_list_tools=listed, on_call_tool=called)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
