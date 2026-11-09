"""Plan and verify every task of an example library; print the programs, write or check counts.

    uv run python tools/case.py examples/parcels
    uv run python tools/case.py examples/parcels --json docs/studies/parcels.json
    uv run python tools/case.py examples/parcels --check docs/studies/parcels.json

An example is a directory holding ``tools.json``, a server's tool list, and ``tasks.json``, each
task's inputs and goal as JSON Schema. A published count is a row of the study file, and
``--check`` fails when this code no longer produces it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.planner import plan
from valgebra_planning.program import Block, Branch, Stop, render, steps
from valgebra_planning.verify import verify

FORMAT = 1


def _count(block: Block) -> tuple[int, int, int]:
    """Return the calls, tests and stops in a program, over every branch."""
    end = block.end
    calls, tests, stops = len(block.calls), 0, 0
    if isinstance(end, Branch):
        for side in (end.then, end.otherwise):
            more = _count(side)
            calls, tests, stops = calls + more[0], tests + more[1], stops + more[2]
        tests += 1
    elif isinstance(end, Stop):
        stops = 1
    return calls, tests, stops


def study(example: Path) -> tuple[dict[str, Any], list[str]]:
    """Return the study of an example and the text printed for it."""
    tools = {
        contract.name: contract
        for contract in map(
            read_contract, json.loads((example / "tools.json").read_text())["tools"]
        )
    }
    rows, text = [], []
    for spec in json.loads((example / "tasks.json").read_text())["tasks"]:
        task = read_task(spec["name"], spec["inputs"], spec["goal"])
        found = plan(tools, task)
        cells = found.abstraction.cells
        row: dict[str, Any] = {
            "task": task.name,
            "predicates": len(found.abstraction.predicates),
            "cells": len(cells),
            "undecided cells": sum(cell.status == "undecided" for cell in cells),
            "program": "none",
        }
        text.append(f"## {task.name}")
        if found.program is not None:
            checked = verify(found.program, tools, task)
            calls, tests, stops = _count(found.program)
            row |= {
                "program": "strong" if found.strong else "weak",
                "calls": calls,
                "tests": tests,
                "stops": stops,
                "steps": steps(found.program),
                "links": len(checked.links),
                "proven": checked.count("proven"),
                "refuted": checked.count("refuted"),
                "undecided": checked.count("undecided"),
            }
            text += ["", "```", render(found.program), "```", ""]
            text += [
                f"- uncovered at {u.where}: {u.ref}, {list(u.examples)}" for u in found.uncovered
            ]
        rows.append(row)
    return {"format": FORMAT, "example": example.name, "rows": rows}, text


def main(argv: list[str]) -> int:
    """Plan and verify an example, then print, write or check its study."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("example", type=Path)
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--json", type=Path)
    out.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    result, text = study(args.example)
    print("\n".join(text))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.check:
        published = json.loads(args.check.read_text(encoding="utf-8"))
        if published != result:
            print(f"\nthe counts differ from {args.check}")
            for old, new in zip(published.get("rows", []), result["rows"], strict=False):
                if old != new:
                    print(f"  published {old}\n  produced  {new}")
            return 1
        print(f"\nno difference against {args.check}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
