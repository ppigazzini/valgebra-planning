"""Plan and verify every task of an example library; print the programs, write or check counts.

    uv run python tools/case.py examples/parcels
    uv run python tools/case.py examples/parcels --json docs/studies/parcels.json
    uv run python tools/case.py examples/parcels --check docs/studies/parcels.json

An example is a directory holding ``tools.json``, a server's tool list, and ``tasks.json``, each
task's inputs and goal as JSON Schema. It may hold ``programs.json``, programs written for its
tasks as an agent writes one: each is a ``name``, the ``task`` it is held to, its ``source`` a
line to a string, and a ``run``, the task's ``inputs`` and each tool's ``answers`` in the order
its calls take them, the last repeating and a string answering with that error text. Each is
read and critiqued under its task's rules, and run where it names one. A published count or
verdict is a row of the study file, and ``--check`` fails when this code no longer produces it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from valgebra_planning.critic import ProgramError, critique
from valgebra_planning.library import Library, read_library
from valgebra_planning.planner import DEPTH, plan
from valgebra_planning.program import Block, Branch, Stop, render, steps
from valgebra_planning.run import ToolError, run
from valgebra_planning.verify import verify

FORMAT = 1


def _count(block: Block) -> tuple[int, int, int]:
    """Return the calls, tests and stops in a program, over every branch."""
    end = block.end
    calls, tests, stops = len(block.calls), 0, 0
    for call in block.calls:
        if call.on_error is not None:
            more = _count(call.on_error)
            calls, tests, stops = calls + more[0], tests + more[1], stops + more[2]
    if isinstance(end, Branch):
        for side in (end.then, end.otherwise):
            more = _count(side)
            calls, tests, stops = calls + more[0], tests + more[1], stops + more[2]
        tests += 1
    elif isinstance(end, Stop):
        stops = 1
    return calls, tests, stops


def _answering(answers: dict[str, list[Any]], made: list[str]) -> dict[str, Callable[..., Any]]:
    """Return tools answering from ``answers`` in turn, each call's name appended to ``made``."""
    taken = dict.fromkeys(answers, 0)

    def tool(name: str) -> Callable[..., Any]:
        def call(**_: Any) -> Any:
            made.append(name)
            listed = answers[name]
            answer = listed[min(taken[name], len(listed) - 1)]
            taken[name] += 1
            if isinstance(answer, str):
                raise ToolError(answer)
            return answer

        return call

    return {name: tool(name) for name in answers}


def _program(spec: dict[str, Any], library: Library) -> tuple[dict[str, Any], list[str]]:
    """Return the row of a written program, critiqued under its task's rules, and its text."""
    problem = next(p for p in library.problems if p.task.name == spec["task"])
    source = "\n".join(spec["source"]) + "\n"
    row: dict[str, Any] = {"program": spec["name"], "task": spec["task"]}
    text = [f"## {spec['name']}", "", "```", source.rstrip("\n"), "```", ""]
    try:
        checked = critique(
            source,
            library.tools,
            problem.task,
            errors=problem.errors,
            policies=problem.policies,
            flows=problem.flows,
            types=problem.types,
            postconditions=problem.postconditions,
        )
    except ProgramError as error:
        row["refused"] = str(error)
        return row, [*text, f"- refused: {error}"]
    links = checked.verification.links
    row |= {
        "accepted": checked.accepted,
        "verified": checked.verified,
        "weak": checked.weak,
        "links": len(links),
        "proven": checked.verification.count("proven"),
        "refuted": [f"{link.where}: {link.claim}" for link in links if link.verdict == "refuted"],
        "undecided": [
            f"{link.where}: {link.claim}" for link in links if link.verdict == "undecided"
        ],
        "notes": list(checked.notes),
    }
    keys = ("accepted", "verified", "weak", "refuted", "undecided")
    text += [f"- {key}: {row[key]}" for key in keys]
    if "run" in spec:
        made: list[str] = []
        ran = run(
            checked.program,
            library.tools,
            problem.task,
            spec["run"]["inputs"],
            _answering(spec["run"]["answers"], made),
            policies=problem.policies,
            postconditions=problem.postconditions,
        )
        row["run"] = {"outcome": ran.outcome, "calls": made, "refused": ran.refused}
        text.append(f"- run: {row['run']}")
    return row, text


def study(example: Path) -> tuple[dict[str, Any], list[str]]:
    """Return the study of an example and the text printed for it."""
    library = read_library(example)
    tools = library.tools
    rows, text = [], []
    for problem in library.problems:
        task, errors, types = problem.task, problem.errors, problem.types
        under, ruled = list(problem.policies), list(problem.flows)
        depth = problem.depth or DEPTH
        found = plan(
            tools, task, depth=depth, errors=errors, policies=under, flows=ruled, types=types
        )
        cells = found.abstraction.cells
        row: dict[str, Any] = {
            "task": task.name,
            "errors": errors,
            "policies": len(under),
            "flows": len(ruled),
            "mined": types is not None,
            "predicates": len(found.abstraction.predicates),
            "cells": len(cells),
            "undecided cells": sum(cell.status == "undecided" for cell in cells),
            "program": "none",
        }
        text.append(f"## {task.name}")
        if found.program is not None:
            checked = verify(found.program, tools, task, errors=errors, policies=under, flows=ruled)
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
                "assumed": len(found.assumed),
            }
            text += ["", "```", render(found.program), "```", ""]
            text += [
                f"- uncovered at {u.where}: {u.ref}, {list(u.examples)}" for u in found.uncovered
            ]
            text += [f"- assumed from the observed calls: {binding}" for binding in found.assumed]
        rows.append(row)
    result: dict[str, Any] = {"format": FORMAT, "example": example.name, "rows": rows}
    written = example / "programs.json"
    if written.exists():
        programs = json.loads(written.read_text(encoding="utf-8"))["programs"]
        result["programs"] = []
        for spec in programs:
            row, more = _program(spec, library)
            result["programs"].append(row)
            text += more
    return result, text


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
            for key in ("rows", "programs"):
                for old, new in zip(published.get(key, []), result.get(key, []), strict=False):
                    if old != new:
                        print(f"  published {old}\n  produced  {new}")
            return 1
        print(f"\nno difference against {args.check}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
