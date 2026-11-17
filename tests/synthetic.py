"""Synthetic libraries of growing size, for the suite.

A library holds a planted chain of four tools from the input to the goal, each passing on an
identifier of its own pattern, beside two kinds of distractor: tools taking an identifier no
value the agent holds can match, and tools taking the input and returning identifiers nothing
reads.
"""

from __future__ import annotations

import random
from typing import Any

CHAIN = 4


def identifier(prefix: str) -> dict[str, str]:
    """Return the schema of an identifier: the prefix, an underscore, digits."""
    return {"type": "string", "pattern": f"^{prefix}_[0-9]+$"}


def _tool(name: str, takes: str, gives: str) -> dict[str, Any]:
    return {
        "name": name,
        "inputSchema": {
            "type": "object",
            "properties": {"id": identifier(takes)},
            "required": ["id"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"id": identifier(gives)},
            "required": ["id"],
        },
    }


def library(size: int, seed: int) -> list[dict[str, Any]]:
    """Return a library of ``size`` tools: the chain, then distractors of both kinds."""
    tools = [_tool(f"step{i}", f"k{i}", f"k{i + 1}") for i in range(CHAIN)]
    for i in range(size - CHAIN):
        if i % 2:
            tools.append(_tool(f"stray{i}", f"d{i}", f"e{i}"))
        else:
            tools.append(_tool(f"dead{i}", "k0", f"z{i}"))
    random.Random(seed).shuffle(tools)
    return tools
