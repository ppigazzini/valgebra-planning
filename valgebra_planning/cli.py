"""The command line: plan a library's tasks, or critique a program written for one.

    valgebra-planning plan LIBRARY [--task NAME] [--server SERVER] [--json]
    valgebra-planning critique LIBRARY --task NAME PROGRAM [--server SERVER] [--json]
    valgebra-planning run LIBRARY --task NAME --inputs JSON --server SERVER [--json]

``LIBRARY`` is a directory `valgebra_planning.library.read_library` reads. ``plan`` prints each
task's program and its verification; it exits 1 when a task gets no program. ``critique`` reads a
program written in Python, verifies it under the task's rules and prints what is not proven; it
exits 1 when the program is not accepted. ``run`` plans a task and runs the program against a
live server, failing closed; it exits 1 when the run does not return a value inside the goal.
``SERVER`` is a URL or a command starting a stdio server; its tools replace ``tools.json``, and
it needs the ``mcp`` extra. With ``--json`` each prints one JSON document.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from valgebra_planning.contracts import Contract
from valgebra_planning.critic import ProgramError, critique
from valgebra_planning.library import Library, Problem, read_library
from valgebra_planning.planner import Plan, plan
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


def _planned(library: Library, problem: Problem) -> tuple[dict[str, Any], Plan]:
    """Return a problem's row, its program verified, and the plan the row reads."""
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
        return row, found
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
    }, found


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
    rows = [_planned(library, problem)[0] for problem in problems]
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


def _served(server: str) -> Mapping[str, Contract]:
    """Return the contracts of a live server's tools."""
    from valgebra_planning.servers import connect, read_server  # noqa: PLC0415 - the mcp extra

    async def listed() -> dict[str, Contract]:
        async with connect(server) as client:
            return await read_server(client)

    return asyncio.run(listed())


def _run(directory: Path, name: str, inputs: dict[str, Any], server: str, *, as_json: bool) -> int:
    """Plan a task over a live server's tools and run the program against it, in one session."""
    from valgebra_planning.servers import connect, read_server, run_on_server  # noqa: PLC0415

    async def ran() -> dict[str, Any]:
        async with connect(server) as client:
            library = read_library(directory, await read_server(client))
            problem = library.problem(name)
            row, found = _planned(library, problem)
            if found.program is None:
                return row
            result = await run_on_server(
                found.program,
                library.tools,
                problem.task,
                inputs,
                client,
                policies=problem.policies,
            )
            return row | {
                "value": result.value,
                "stopped": result.stopped,
                "refused": result.refused,
                "broken": list(result.broken),
            }

    row = asyncio.run(ran())
    returned = row["program"] is not None and row["stopped"] is None and row["refused"] is None
    if as_json:
        print(json.dumps(row, indent=2))
    elif row["program"] is None:
        print(_text(row))
    else:
        ended = (
            f"refused: {row['refused']}"
            if row["refused"]
            else (
                f"stopped on {row['stopped']}" if row["stopped"] else f"returned {row['value']!r}"
            )
        )
        print("\n".join([_text(row), "", ended, *row["broken"]]))
    return 0 if returned and not row["broken"] else 1


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
    running = commands.add_parser("run", help="plan a task and run it against a live server")
    running.add_argument("library", type=Path)
    running.add_argument("--task", required=True)
    running.add_argument("--inputs", required=True, type=json.loads, help="a JSON object")
    running.add_argument("--json", action="store_true")
    for command in (planning, critic, running):
        command.add_argument("--server", required=command is running, help="a URL or a command")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run(args.library, args.task, args.inputs, args.server, as_json=args.json)
        tools = None if args.server is None else _served(args.server)
        library = read_library(args.library, tools)
        if args.command == "plan":
            return _plan(library, args.task, as_json=args.json)
        return _critique(library, args.task, args.program, as_json=args.json)
    except KeyError as missing:
        print(f"{args.library}: no task named {missing}", file=sys.stderr)
        return 2
