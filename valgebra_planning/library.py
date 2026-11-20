"""A library on disk: a server's tool list, the tasks over it and the rules each is held to.

A library is a directory. ``tools.json`` holds a server's ``tools/list`` result, ``{"tools":
[...]}``, and ``"trusted": true`` where the server's annotations are trusted. ``tasks.json``
holds ``{"tasks": [...]}``, each task its ``name``, the JSON Schema of each input in ``inputs``
and of the ``goal``, and the rules it names: ``errors`` (any tool may fail), ``policies`` and
``flows`` by name, and ``types``, the file of observed calls its types are mined from; a tool
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

from valgebra_planning.contracts import Contract, Task, read_contract, read_task, unclashed
from valgebra_planning.flows import Flow, read_flow
from valgebra_planning.mining import Types, infer_outputs, mine
from valgebra_planning.policy import Policy, read_policy

__all__ = ["Library", "Problem", "read_library"]


@dataclass(frozen=True, slots=True)
class Problem:
    """A task and the rules a program for it is held to."""

    task: Task
    errors: bool = False
    policies: tuple[Policy, ...] = ()
    flows: tuple[Flow, ...] = ()
    types: Types | None = None


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


def _listed(path: Path, key: str) -> list[Any]:
    return json.loads(path.read_text(encoding="utf-8"))[key] if path.exists() else []


def read_library(directory: Path, tools: Mapping[str, Contract] | None = None) -> Library:
    """Read a library directory; a rule a task names and the directory lacks is a `KeyError`.

    ``tools``, read from a live server, replaces the directory's ``tools.json``. A task naming
    an input as a tool is a `ValueError`.
    """
    specs = _listed(directory / "tasks.json", "tasks")
    logs = sorted({spec["types"] for spec in specs if "types" in spec})
    observed = [call for log in logs for call in _listed(directory / log, "calls")]
    if tools is None:
        listed = directory / "tools.json"
        document = json.loads(listed.read_text(encoding="utf-8")) if listed.exists() else {}
        listing = document.get("tools", [])
        shaped = infer_outputs(observed, listing)
        trusted = document.get("trusted") is True
        tools = {
            spec["name"]: replace(
                read_contract({**spec, "outputSchema": shaped[spec["name"]]})
                if spec["name"] in shaped
                else read_contract(spec),
                inferred=spec["name"] in shaped,
                trusted=trusted,
            )
            for spec in listing
        }
    policies = {
        p.name: p for p in map(read_policy, _listed(directory / "policies.json", "policies"))
    }
    flows = {f.name: f for f in map(read_flow, _listed(directory / "flows.json", "flows"))}
    problems = []
    for spec in specs:
        log = spec.get("types")
        task = read_task(spec["name"], spec["inputs"], spec["goal"])
        unclashed(task, tools)
        problems.append(
            Problem(
                task=task,
                errors=bool(spec.get("errors", False)),
                policies=tuple(policies[name] for name in spec.get("policies", [])),
                flows=tuple(flows[name] for name in spec.get("flows", [])),
                types=None if log is None else mine(_listed(directory / log, "calls"), tools),
            )
        )
    return Library(tools, tuple(problems), policies, flows)
