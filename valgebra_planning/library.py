"""A library on disk: a server's tool list, the tasks over it and the rules each is held to.

A library is a directory. ``tools.json`` holds a server's ``tools/list`` result, ``{"tools":
[...]}``, ``"trusted": true`` where the server's annotations are trusted, and
``"closedResults": true`` where each whole result is read closed, an assumption about the
server (`valgebra_planning.contracts.read_contract`). ``tasks.json``
holds ``{"tasks": [...]}``, each task its ``name``, the JSON Schema of each input in ``inputs``
and of the ``goal``, the rules it names, ``errors`` (any tool may fail), ``policies`` and
``flows`` by name, and ``types``, the file of observed calls its types are mined from, and
``depth``, the calls and tests a path of its program may take where it sets its own bound; a tool
listed without an ``outputSchema`` gets one shaped from its results in those observed calls, an
assumption (`valgebra_planning.mining.infer_outputs`). ``policies.json`` and ``flows.json`` hold
the rules a task names, as `valgebra_planning.policy.read_policy` and
`valgebra_planning.flows.read_flow` read them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from valgebra_planning.contracts import (
    Contract,
    Task,
    fixes,
    flag,
    keys_read,
    read_contract,
    read_task,
)
from valgebra_planning.flows import Flow, read_flow
from valgebra_planning.mining import Types, infer_outputs, mine
from valgebra_planning.policy import Policy, read_policy

__all__ = ["Library", "Problem", "asserts_format", "read_library", "reads_closed"]


@dataclass(frozen=True, slots=True)
class Problem:
    """A task and the rules a program for it is held to."""

    task: Task
    errors: bool = False
    policies: tuple[Policy, ...] = ()
    flows: tuple[Flow, ...] = ()
    types: Types | None = None
    #: The calls and tests a path may take where the task sets its own bound.
    depth: int | None = None


@dataclass(frozen=True, slots=True)
class Library:
    """The tools of a library, by name, and its problems, in the order the file lists them."""

    tools: Mapping[str, Contract]
    problems: tuple[Problem, ...]
    #: Every policy and flow rule the library defines, by name, whether a task names it or not.
    policies: Mapping[str, Policy] = field(default_factory=dict)
    flows: Mapping[str, Flow] = field(default_factory=dict)

    def problem(self, name: str) -> Problem:
        """Return the problem whose task is ``name``; raise `KeyError` naming it otherwise."""
        for problem in self.problems:
            if problem.task.name == name:
                return problem
        raise KeyError(name)


#: The keys a task reads.
_TASK_KEYS = frozenset(
    {
        "name",
        "inputs",
        "goal",
        "goals",
        "errors",
        "policies",
        "flows",
        "types",
        "depth",
        "stops",
        "constants",
        "fresh",
    }
)


def _document(path: Path, keys: frozenset[str]) -> dict[str, Any]:
    """Return the JSON object a library file holds, its keys held to ``keys``; empty if absent."""
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    keys_read(document, keys, path.name)
    return document


def _listed(path: Path, key: str) -> list[Any]:
    return _document(path, frozenset({key})).get(key, [])


#: The keys of ``tools.json``: the listing, and how the library reads its server's tools.
_TOOLS_KEYS = frozenset({"tools", "trusted", "assertFormat", "closedResults"})


def asserts_format(directory: Path) -> bool:
    """Return whether a library reads ``format`` as an assertion: ``"assertFormat": true``.

    A caller reading a live server's tools for the library reads them with it, as
    `valgebra_planning.servers.read_server` takes it, so every set the library holds is read
    alike.
    """
    return flag(_document(directory / "tools.json", _TOOLS_KEYS), "assertFormat", "tools.json")


def reads_closed(directory: Path) -> bool:
    """Return whether a library reads each whole result closed: ``"closedResults": true``.

    A caller reading a live server's tools for the library reads them with it, as
    `valgebra_planning.servers.read_server` takes it.
    """
    return flag(_document(directory / "tools.json", _TOOLS_KEYS), "closedResults", "tools.json")


def _goals(spec: Mapping[str, Any]) -> list[Any]:
    """Return a task's goals in the order it prefers them: its `goal`, or its `goals`."""
    where = f"task {spec.get('name')!r}"
    if ("goal" in spec) == ("goals" in spec):
        msg = f"{where} names a `goal` or its `goals` in order, one of the two"
        raise ValueError(msg)
    if "goal" in spec:
        return [spec["goal"]]
    goals = spec["goals"]
    if not isinstance(goals, list) or not goals:
        msg = f"{where}: `goals` is a list of JSON Schemas, the first preferred"
        raise TypeError(msg)
    return goals


def read_library(directory: Path, tools: Mapping[str, Contract] | None = None) -> Library:
    """Read a library directory; a rule a task names and the directory lacks is a `KeyError`.

    ``tools``, read from a live server, replaces the listing of the directory's ``tools.json``,
    whose ``trusted`` still marks them trusted; read them with `asserts_format` and
    `reads_closed`, and a tool given read open where the library reads results closed is a
    `ValueError`. With
    ``"assertFormat": true`` there, ``format`` is read as an assertion in every contract, task,
    policy guard and endorsed set the library reads. A task naming
    an input as a tool is a `ValueError`, and so is a key a reader does not read, in a file, a
    task, a policy, a transition or a flow rule; a flag that is no boolean is a `TypeError`.
    """
    specs = _listed(directory / "tasks.json", "tasks")
    for spec in specs:
        keys_read(spec, _TASK_KEYS, f"task {spec.get('name')!r}")
        depth = spec.get("depth")
        if depth is not None and (type(depth) is not int or depth < 1):
            message = f"task {spec.get('name')!r}: 'depth' is a positive integer, not {depth!r}"
            raise ValueError(message)
    logs = sorted({spec["types"] for spec in specs if "types" in spec})
    observed = [call for log in logs for call in _listed(directory / log, "calls")]
    document = _document(directory / "tools.json", _TOOLS_KEYS)
    trusted = flag(document, "trusted", "tools.json")
    asserted = flag(document, "assertFormat", "tools.json")
    closing = flag(document, "closedResults", "tools.json")
    if tools is None:
        listing = document.get("tools", [])
        shaped = infer_outputs(observed, listing)
        tools = {
            spec["name"]: replace(
                read_contract(
                    {**spec, "outputSchema": shaped[spec["name"]]}
                    if spec["name"] in shaped
                    else spec,
                    assert_format=asserted,
                    closed_results=closing,
                ),
                inferred=spec["name"] in shaped,
            )
            for spec in listing
        }
    elif closing and (
        opened := sorted(
            n for n, c in tools.items() if c.returns is not None and c.declared is None
        )
    ):
        msg = (
            f"tools.json reads results closed, and these tools were read open: {', '.join(opened)}"
        )
        raise ValueError(msg)
    tools = {name: replace(contract, trusted=trusted) for name, contract in tools.items()}
    policies = {
        p.name: p
        for p in (
            read_policy(spec, assert_format=asserted)
            for spec in _listed(directory / "policies.json", "policies")
        )
    }
    flows = {
        f.name: f
        for f in (
            read_flow(spec, assert_format=asserted)
            for spec in _listed(directory / "flows.json", "flows")
        )
    }
    problems = []
    for spec in specs:
        log = spec.get("types")
        goals = _goals(spec)
        task = read_task(
            spec["name"],
            spec["inputs"],
            goals[0],
            stops=spec.get("stops"),
            fallbacks=goals[1:],
            constants=spec.get("constants"),
            fresh=spec.get("fresh", ()),
            assert_format=asserted,
        )
        fixes(task, tools)
        problems.append(
            Problem(
                task=task,
                errors=flag(spec, "errors", f"task {spec['name']!r}"),
                policies=tuple(policies[name] for name in spec.get("policies", [])),
                flows=tuple(flows[name] for name in spec.get("flows", [])),
                types=None if log is None else mine(_listed(directory / log, "calls"), tools),
                depth=spec.get("depth"),
            )
        )
    return Library(tools, tuple(problems), policies, flows)
