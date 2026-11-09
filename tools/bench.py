"""Plan over synthetic libraries of growing size; print the times, write or check the counts.

    uv run python tools/bench.py
    uv run python tools/bench.py --json docs/studies/bench.json
    uv run python tools/bench.py --check docs/studies/bench.json

A library holds a planted chain of four tools from the input to the goal, each passing on an
identifier of its own pattern, beside two kinds of distractor: tools taking an identifier no
value the agent holds can match, and tools taking the input and returning identifiers
nothing reads. Each size is built three times, the tools in a seeded order. The counts are
recorded and checked; the times depend on the machine and are printed only.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.planner import plan
from valgebra_planning.verify import verify

FORMAT = 1
SIZES = (8, 16, 32, 64, 128, 256)
SEEDS = (0, 1, 2)
CHAIN = 4


def _id(prefix: str) -> dict[str, str]:
    return {"type": "string", "pattern": f"^{prefix}_[0-9]+$"}


def _tool(name: str, takes: str, gives: str) -> dict[str, Any]:
    return {
        "name": name,
        "inputSchema": {
            "type": "object",
            "properties": {"id": _id(takes)},
            "required": ["id"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"id": _id(gives)},
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


def study() -> tuple[dict[str, Any], list[str]]:
    """Return the study of every size and seed, and the timings printed beside it."""
    rows, text = [], []
    task = read_task("reach the end", {"start": _id("k0")}, _id(f"k{CHAIN}"))
    for size in SIZES:
        for seed in SEEDS:
            tools = {c.name: c for c in map(read_contract, library(size, seed))}
            began = time.perf_counter()
            found = plan(tools, task)
            took = time.perf_counter() - began
            checked = verify(found.program, tools, task) if found.program else None
            rows.append(
                {
                    "tools": size,
                    "seed": seed,
                    "predicates": len(found.abstraction.predicates),
                    "cells": len(found.abstraction.cells),
                    "program": "strong" if found.strong else "none",
                    "proven": checked.count("proven") if checked else 0,
                    "links": len(checked.links) if checked else 0,
                    "expanded": found.expanded,
                }
            )
            text.append(f"{size:>4} tools, seed {seed}: {took:.2f} s")
    return {"format": FORMAT, "rows": rows}, text


def main(argv: list[str]) -> int:
    """Run the study, then print it, write it, or check it against a published one."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--json", type=Path)
    out.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    result, text = study()
    print("\n".join(text))
    for row in result["rows"]:
        print(row)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.check:
        if json.loads(args.check.read_text(encoding="utf-8")) != result:
            print(f"\nthe counts differ from {args.check}")
            return 1
        print(f"\nno difference against {args.check}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
