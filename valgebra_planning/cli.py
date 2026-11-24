"""The command line: plan a library's tasks, or critique a program written for one.

    valgebra-planning plan LIBRARY [--task NAME] [--depth N] [--server SERVER] [--json]
    valgebra-planning critique LIBRARY --task NAME PROGRAM [--server SERVER] [--json]
    valgebra-planning run LIBRARY --task NAME --inputs JSON --server SERVER [--depth N]
        [--timeout S] [--json]
    valgebra-planning check LIBRARY [--server SERVER] [--json]
    valgebra-planning privileges OLD NEW [--json]
    valgebra-planning stubs LIBRARY [--server SERVER]
    valgebra-planning proxy --server SERVER [--library LIBRARY] [--timeout S]
        [--http HOST:PORT --resource URL --issuer URL --verifier MODULE:NAME [--scope S]...
        [--origin ORIGIN]...]

``LIBRARY`` is a directory `valgebra_planning.library.read_library` reads. ``plan`` prints each
task's program and its verification; it exits 1 when a task gets no program. ``critique`` reads a
program written in Python, verifies it under the task's rules and prints what is not proven; it
exits 1 when the program is not accepted. ``run`` plans a task and runs the program against a
live server, failing closed; it exits 1 when the run does not return a value inside the goal.
``check`` lists every specification that holds vacuously and exits 1 when it finds one.
``privileges`` compares two privilege policies in Progent's format and exits 1 unless the new one
narrows the old for every tool it names. ``proxy`` serves an MCP server over stdio in front of
``SERVER``, holding every call to the contracts and to the library's policies and flow rules;
with ``--http`` it serves Streamable HTTP to every client holding a token issued for
``--resource`` by ``--issuer``, which the verifier ``MODULE:NAME`` reads.
``stubs`` prints the tools as typed Python stubs, their schemas in their docstrings, for a model
to write a program against.
``SERVER`` is a URL or a command starting a stdio server; its tools replace ``tools.json``, and
it needs the ``mcp`` extra. A command is split as a POSIX shell splits it, on Windows as well, so
a path holding a backslash or a space is quoted. With ``--json`` each prints one JSON document.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from valgebra_planning.codemode import stubs
from valgebra_planning.contracts import Contract
from valgebra_planning.critic import ProgramError, critique
from valgebra_planning.library import (
    Library,
    Problem,
    after_violation,
    asserts_format,
    error_texts,
    forwards_elicitation,
    non_text_blocks,
    preempts,
    read_library,
    reads_closed,
)
from valgebra_planning.meaning import findings
from valgebra_planning.planner import DEPTH, Plan, plan
from valgebra_planning.privilege import describe, read_privileges, updates
from valgebra_planning.program import render
from valgebra_planning.run import blame
from valgebra_planning.vacuity import vacuities
from valgebra_planning.verify import Verification, verify


def _links(checked: Verification) -> list[dict[str, Any]]:
    return [
        {
            "where": link.where,
            "claim": link.claim,
            "verdict": link.verdict,
            "counterexample": list(link.counterexample),
            "assumed": list(link.assumed),
        }
        for link in checked.links
    ]


def _planned(
    library: Library, problem: Problem, depth: int | None = None
) -> tuple[dict[str, Any], Plan]:
    """Return a problem's row, its program verified, and the plan the row reads.

    The depth is ``depth`` where given, else the task's own, else the planner's default.
    """
    found = plan(
        library.tools,
        problem.task,
        depth=depth or problem.depth or DEPTH,
        errors=problem.errors,
        policies=problem.policies,
        flows=problem.flows,
        types=problem.types,
        postconditions=problem.postconditions,
    )
    row: dict[str, Any] = {"task": problem.task.name, "program": None}
    if found.program is None:
        return row | {"reason": found.reason}, found
    checked = verify(
        found.program,
        library.tools,
        problem.task,
        errors=problem.errors,
        policies=problem.policies,
        flows=problem.flows,
        postconditions=problem.postconditions,
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
    } | ({"preferred": list(found.preferred)} if problem.task.preferred else {}), found


def _text(row: dict[str, Any]) -> str:
    lines = [f"## {row['task']}", ""]
    if row["program"] is None:
        return "\n".join([*lines, row.get("reason") or "no program"])
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


def _plan(library: Library, names: Sequence[str], depth: int | None, *, as_json: bool) -> int:
    problems = [library.problem(name) for name in names] if names else list(library.problems)
    rows = [_planned(library, problem, depth)[0] for problem in problems]
    print(json.dumps(rows, indent=2) if as_json else "\n\n".join(map(_text, rows)))
    return 0 if all(row["program"] is not None for row in rows) else 1


def _critique(library: Library, name: str, source: Path, *, as_json: bool) -> int:
    problem = library.problem(name)
    try:
        checked = critique(
            source.read_text(encoding="utf-8"),
            library.tools,
            problem.task,
            errors=problem.errors,
            policies=problem.policies,
            flows=problem.flows,
            types=problem.types,
            postconditions=problem.postconditions,
        )
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


def _served(server: str, directory: Path) -> Mapping[str, Contract]:
    """Return the contracts of a live server's tools, read as the library reads its own."""
    from valgebra_planning.servers import connect, read_server  # noqa: PLC0415 - the mcp extra

    async def listed() -> dict[str, Contract]:
        async with connect(server) as client:
            return await read_server(
                client,
                assert_format=asserts_format(directory),
                closed_results=reads_closed(directory),
            )

    return asyncio.run(listed())


def _run(
    directory: Path,
    name: str,
    inputs: dict[str, Any],
    server: str,
    *,
    depth: int | None,
    timeout: float | None,
    as_json: bool,
) -> int:
    """Plan a task over a live server's tools and run the program against it, in one session."""
    from valgebra_planning.servers import connect, read_server, run_on_server  # noqa: PLC0415

    async def ran() -> dict[str, Any]:
        async with connect(server, read_timeout_seconds=timeout) as client:
            served = await read_server(
                client,
                assert_format=asserts_format(directory),
                closed_results=reads_closed(directory),
            )
            library = read_library(directory, served)
            problem = library.problem(name)
            row, found = _planned(library, problem, depth)
            if found.program is None:
                return row
            result = await run_on_server(
                found.program,
                library.tools,
                problem.task,
                inputs,
                client,
                policies=problem.policies,
                postconditions=problem.postconditions,
                flows=problem.flows,
                error_texts=error_texts(directory),
            )
            return row | {
                "outcome": result.outcome,
                "ok": result.ok,
                "value": result.value,
                "stopped": result.stopped,
                "refused": result.refused,
                "error": result.error,
                "broken": list(result.broken),
                "blame": blame(result, library.tools, proven=row["proven"]),
            }

    row = asyncio.run(ran())
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
        cause = [f"cause: {row['blame']}"] if row["blame"] else []
        said = [f"its error: {row['error']}"] if row["error"] else []
        print("\n".join([_text(row), "", ended, *row["broken"], *said, *cause]))
    return 0 if row.get("ok", False) else 1


def _check(library: Library, *, as_json: bool) -> int:
    """Print the vacuous specifications, then the contracts wider than their keywords.

    The exit status reads the first alone: a wide contract is a note, the author's to keep.
    """
    found = vacuities(library)
    wide = findings(library)
    if as_json:
        print(
            json.dumps(
                [
                    {"where": f.where, "finding": f.finding, "certainty": f.certainty, "kind": kind}
                    for kind, listed in (("vacuous", found), ("wide", wide))
                    for f in listed
                ],
                indent=2,
            )
        )
    else:
        lines = [
            f"{f.where}: {f.finding}" + ("" if f.certainty == "proven" else " (undecided)")
            for f in found
        ]
        print("\n".join(lines) if lines else "no specification holds vacuously")
        for f in wide:
            print(
                f"note: {f.where}: {f.finding}"
                + ("" if f.certainty == "proven" else " (undecided)")
            )
    return 1 if found else 0


def _privileges(old: Path, new: Path, *, as_json: bool) -> int:
    """Compare two privilege policies in Progent's format, tool by tool."""
    found = updates(
        read_privileges(json.loads(old.read_text(encoding="utf-8"))),
        read_privileges(json.loads(new.read_text(encoding="utf-8"))),
    )
    if as_json:
        rows = [{"tool": u.tool, "change": u.change, "witness": list(u.witness)} for u in found]
        print(json.dumps(rows, indent=2))
    else:
        print("\n".join(map(describe, found)))
    return 0 if all(u.change == "narrowing" for u in found) else 1


def _verifier(named: str) -> Any:
    """Return the token verifier ``module:name`` names, its module found from the working directory.

    A verifier is an object with ``verify_token``, mcp's ``TokenVerifier``; reading a token
    issued by the deployment's authorization server is its own.
    """
    module, colon, attribute = named.partition(":")
    if not colon or not module or not attribute:
        msg = f"--verifier names `module:name`, not {named!r}"
        raise ValueError(msg)
    here = str(Path.cwd())
    if here not in sys.path:
        sys.path.insert(0, here)
    found: Any = importlib.import_module(module)
    for part in attribute.split("."):
        found = getattr(found, part)
    if not callable(getattr(found, "verify_token", None)):
        msg = f"--verifier {named} has no `verify_token`: it is no token verifier"
        raise TypeError(msg)
    return found


def _address(given: str) -> tuple[str, int]:
    """Return the host and the port ``HOST:PORT`` names, a bracketed IPv6 host unbracketed."""
    host, colon, port = given.rpartition(":")
    if not colon or not host or not port.isdigit():
        msg = f"--http names `HOST:PORT`, not {given!r}"
        raise ValueError(msg)
    return host.removeprefix("[").removesuffix("]"), int(port)


def _proxy(args: argparse.Namespace) -> int:
    """Serve a proxy in front of a server, holding calls to a library's rules.

    It serves the agent starting it over stdio, or every client holding a token over HTTP.
    """
    from valgebra_planning.proxy import read_proxy, serve, serve_http  # noqa: PLC0415 - the mcp extra
    from valgebra_planning.servers import connect  # noqa: PLC0415

    server, directory, timeout = args.server, args.library, args.timeout
    http = None if args.http is None else (_address(args.http), _verifier(args.verifier))

    library = None if directory is None else read_library(directory)
    policies = () if library is None else tuple(library.policies.values())
    flows = () if library is None else tuple(library.flows.values())
    # The server's tools are read as the library reads its own listing.
    asserted = directory is not None and asserts_format(directory)
    closing = directory is not None and reads_closed(directory)
    left_open = frozenset() if directory is None else after_violation(directory)
    first = frozenset() if directory is None else preempts(directory)
    kept = frozenset() if directory is None else non_text_blocks(directory)
    errors = {} if directory is None else error_texts(directory)
    asking = frozenset() if directory is None else forwards_elicitation(directory)

    async def served() -> None:
        async with connect(server, read_timeout_seconds=timeout) as upstream:
            if http is None:
                await serve(
                    upstream,
                    policies=policies,
                    flows=flows,
                    assert_format=asserted,
                    closed_results=closing,
                    spell_out=args.spell_out,
                    after_violation=left_open,
                    preempts=first,
                    non_text_blocks=kept,
                    error_texts=errors,
                    forwards_elicitation=asking,
                )
                return
            front = await read_proxy(
                upstream,
                policies=policies,
                flows=flows,
                assert_format=asserted,
                closed_results=closing,
                spell_out=args.spell_out,
                after_violation=left_open,
                preempts=first,
                non_text_blocks=kept,
                error_texts=errors,
                forwards_elicitation=asking,
            )
            (host, port), verifier = http
            await serve_http(
                front,
                host=host,
                port=port,
                resource=args.resource,
                issuer=args.issuer,
                verifier=verifier,
                scopes=args.scope,
                origins=args.origin,
            )

    asyncio.run(served())
    return 0


def _proxy_parser(commands: Any) -> argparse.ArgumentParser:
    """Add the ``proxy`` command and the arguments it alone takes."""
    proxied = commands.add_parser("proxy", help="serve an MCP proxy holding calls to the rules")
    proxied.add_argument("--library", type=Path, help="a library whose policies and flows hold")
    proxied.add_argument(
        "--spell-out",
        action="store_true",
        help="list each const and pattern spelled out for a model, where proven equal",
    )
    proxied.add_argument(
        "--http", metavar="HOST:PORT", help="serve Streamable HTTP, every client authenticated"
    )
    proxied.add_argument("--resource", help="the proxy's URL, the audience of its tokens")
    proxied.add_argument("--issuer", help="the URL of the authorization server issuing them")
    proxied.add_argument(
        "--verifier", metavar="MODULE:NAME", help="the token verifier, found from this directory"
    )
    proxied.add_argument(
        "--scope", action="append", default=[], help="a scope every token holds; repeatable"
    )
    proxied.add_argument(
        "--origin", action="append", default=[], help="an Origin a client may send; repeatable"
    )
    return proxied


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
    checking = commands.add_parser("check", help="find specifications that hold vacuously")
    checking.add_argument("library", type=Path)
    checking.add_argument("--json", action="store_true")
    privileged = commands.add_parser("privileges", help="compare two Progent privilege policies")
    privileged.add_argument("old", type=Path)
    privileged.add_argument("new", type=Path)
    privileged.add_argument("--json", action="store_true")
    stubbed = commands.add_parser("stubs", help="print typed Python stubs of a library's tools")
    stubbed.add_argument("library", type=Path)
    proxied = _proxy_parser(commands)
    for command in (planning, critic, running, checking, stubbed, proxied):
        required = command in (running, proxied)
        command.add_argument("--server", required=required, help="a URL or a command")
    for command in (planning, running):
        command.add_argument(
            "--depth", type=int, help=f"calls and tests a path may take; the task's, else {DEPTH}"
        )
    for command in (running, proxied):
        command.add_argument(
            "--timeout", type=float, help="seconds a call waits for its answer; no limit by default"
        )
    args = parser.parse_args(argv)
    if args.command == "privileges":
        return _privileges(args.old, args.new, as_json=args.json)
    if args.command == "proxy":
        authority = (args.resource, args.issuer, args.verifier)
        given = any(authority) or args.scope or args.origin
        if (args.http is None and given) or (args.http is not None and not all(authority)):
            proxied.error(
                "--http serves authenticated clients alone, and takes --resource, --issuer and"
                " --verifier, which, with --scope and --origin, serve --http alone"
            )
        return _proxy(args)
    try:
        return _on_library(args)
    except KeyError as missing:
        print(f"{args.library}: no task named {missing}", file=sys.stderr)
        return 2
    except (TypeError, ValueError) as refused:
        print(f"{args.library}: {refused}", file=sys.stderr)
        return 2


def _on_library(args: argparse.Namespace) -> int:
    """Run a command that reads a library, and return its exit status."""
    if args.command == "run":
        return _run(
            args.library,
            args.task,
            args.inputs,
            args.server,
            depth=args.depth,
            timeout=args.timeout,
            as_json=args.json,
        )
    tools = None if args.server is None else _served(args.server, args.library)
    library = read_library(args.library, tools)
    if args.command == "plan":
        return _plan(library, args.task, args.depth, as_json=args.json)
    if args.command == "check":
        return _check(library, as_json=args.json)
    if args.command == "stubs":
        print(stubs(library.tools))
        return 0
    return _critique(library, args.task, args.program, as_json=args.json)
