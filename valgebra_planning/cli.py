"""The command line: plan a library's tasks, or critique a program written for one.

    valgebra-planning plan LIBRARY [--task NAME] [--json]
    valgebra-planning critique LIBRARY --task NAME PROGRAM [--json]

``LIBRARY`` is a directory `valgebra_planning.library.read_library` reads. ``plan`` prints each
task's program and its verification; it exits 1 when a task gets no program. ``critique`` reads a
program written in Python, verifies it under the task's rules and prints what is not proven; it
exits 1 when the program is not accepted. With ``--json`` each prints one JSON document.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from valgebra_planning.critic import ProgramError, critique
from valgebra_planning.library import Library, Problem, read_library
from valgebra_planning.planner import plan
from valgebra_planning.program import render
from valgebra_planning.verify import Verification, verify


def _links(checked: Verification) -> list[dict[str, Any]]:
    return [
        {
            "where": link.where,
            "claim": link.claim,
            "verdict": link.verdict,
            "counterexample": list(link.counterexample),
        }
        for link in checked.links
    ]


def _planned(library: Library, problem: Problem) -> dict[str, Any]:
    """Return a problem's program, its verification, its stops and the bindings assumed."""
    found = plan(
        library.tools,
        problem.task,
        errors=problem.errors,
        policies=problem.policies,
        flows=problem.flows,
        types=problem.types,
    )
    row: dict[str, Any] = {"task": problem.task.name, "program": None}
    if found.program is None:
        return row
    checked = verify(
        found.program,
        library.tools,
        problem.task,
        errors=problem.errors,
        policies=problem.policies,
        flows=problem.flows,
    )
    return row | {
        "program": render(found.program),
        "strong": found.strong,
        "proven": checked.proven,
        "links": _links(checked),
        "uncovered": [
            {"where": u.where, "ref": u.ref, "examples": list(u.examples)} for u in found.uncovered
        ],
        "assumed": list(found.assumed),
    }


def _text(row: dict[str, Any]) -> str:
    lines = [f"## {row['task']}", ""]
    if row["program"] is None:
        return "\n".join([*lines, "no program"])
    proven = sum(link["verdict"] == "proven" for link in row["links"])
    kind = "strong" if row["strong"] else "weak"
    lines += [row["program"], "", f"{kind}; {proven} of {len(row['links'])} links proven"]
    lines += [
        f"{link['verdict']}: {link['claim']}" + (f" (at {link['where']})" if link["where"] else "")
        for link in row["links"]
        if link["verdict"] != "proven"
    ]
    lines += [
        f"uncovered at {u['where'] or 'top'}: {u['ref']}, {u['examples']}" for u in row["uncovered"]
    ]
    lines += [f"assumed from the observed calls: {binding}" for binding in row["assumed"]]
    return "\n".join(lines)


def _plan(library: Library, names: Sequence[str], *, as_json: bool) -> int:
    problems = [library.problem(name) for name in names] if names else list(library.problems)
    rows = [_planned(library, problem) for problem in problems]
    print(json.dumps(rows, indent=2) if as_json else "\n\n".join(map(_text, rows)))
    return 0 if all(row["program"] is not None for row in rows) else 1


def _critique(library: Library, name: str, source: Path, *, as_json: bool) -> int:
    problem = library.problem(name)
    try:
        checked = critique(source.read_text(encoding="utf-8"), library.tools, problem.task)
    except ProgramError as error:
        print(f"{source}: {error}", file=sys.stderr)
        return 2
    if as_json:
        print(
            json.dumps(
                {
                    "task": name,
                    "accepted": checked.accepted,
                    "links": _links(checked.verification),
                    "notes": list(checked.notes),
                },
                indent=2,
            )
        )
    else:
        proven = checked.verification.count("proven")
        print(f"{name}: {proven} of {len(checked.verification.links)} links proven")
        print("\n".join(checked.notes) if checked.notes else "accepted")
    return 0 if checked.accepted else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command the arguments name and return its exit status."""
    parser = argparse.ArgumentParser(prog="valgebra-planning", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    planning = commands.add_parser("plan", help="plan and verify a library's tasks")
    planning.add_argument("library", type=Path)
    planning.add_argument("--task", action="append", default=[], help="a task by name; repeatable")
    planning.add_argument("--json", action="store_true")
    critic = commands.add_parser("critique", help="verify a program written for one task")
    critic.add_argument("library", type=Path)
    critic.add_argument("--task", required=True)
    critic.add_argument("program", type=Path)
    critic.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    library = read_library(args.library)
    try:
        if args.command == "plan":
            return _plan(library, args.task, as_json=args.json)
        return _critique(library, args.task, args.program, as_json=args.json)
    except KeyError as missing:
        print(f"{args.library}: no task named {missing}", file=sys.stderr)
        return 2
