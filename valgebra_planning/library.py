"""A library on disk: a server's tool list, the tasks over it and the rules each is held to.

A library is a directory. ``tools.json`` holds a server's ``tools/list`` result, ``{"tools":
[...]}``, ``"trusted": true`` where the server's annotations are trusted, and
``"closedResults": true`` where each whole result is read closed, an assumption about the
server (`valgebra_planning.contracts.read_contract`), ``"afterViolation"``, the tools a proxy
calls after a violation (`after_violation`), ``"nonTextBlocks"``, the tools whose blocks other
than text a proxy keeps (`non_text_blocks`), ``"errorTexts"``, the set each tool's
error text lies in to be passed on (`error_texts`), and ``"forwardsElicitation"``, the tools whose
request for a person's answer a proxy forwards (`forwards_elicitation`). ``tasks.json``
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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TypeVar

from valgebra_planning.contracts import (
    Contract,
    Postcondition,
    Task,
    fixes,
    flag,
    keys_read,
    read_contract,
    read_postcondition,
    read_task,
)
from valgebra_planning.flows import Flow, check_flow, read_flow
from valgebra_planning.mining import Types, infer_outputs, mine
from valgebra_planning.policy import Policy, check_policy, read_policy
from valgebra_planning.program import aliases

__all__ = [
    "Library",
    "Problem",
    "after_violation",
    "asserts_format",
    "error_texts",
    "forwards_elicitation",
    "non_text_blocks",
    "read_library",
    "reads_closed",
]


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
    #: The library's postconditions, which every task's programs are verified and run under.
    postconditions: tuple[Postcondition, ...] = ()


@dataclass(frozen=True, slots=True)
class Library:
    """The tools of a library, by name, and its problems, in the order the file lists them."""

    tools: Mapping[str, Contract]
    problems: tuple[Problem, ...]
    #: Every policy and flow rule the library defines, by name, whether a task names it or not.
    policies: Mapping[str, Policy] = field(default_factory=dict)
    flows: Mapping[str, Flow] = field(default_factory=dict)
    #: The postconditions ``tools.json`` declares, in the order it lists them.
    postconditions: tuple[Postcondition, ...] = ()

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
_TOOLS_KEYS = frozenset(
    {
        "tools",
        "trusted",
        "assertFormat",
        "closedResults",
        "postconditions",
        "afterViolation",
        "nonTextBlocks",
        "errorTexts",
        "forwardsElicitation",
    }
)


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


def _named(directory: Path, key: str) -> frozenset[str]:
    """Return the tool names ``tools.json`` lists at ``key``; refuse anything but such a list."""
    named = _document(directory / "tools.json", _TOOLS_KEYS).get(key, [])
    if not isinstance(named, list) or not all(isinstance(tool, str) for tool in named):
        message = f"tools.json: {key!r} is a list of tool names, not {named!r}"
        raise TypeError(message)
    return frozenset(named)


def after_violation(directory: Path) -> frozenset[str]:
    """Return the tools a proxy calls after a violation: ``"afterViolation": [...]``.

    Once a result takes a policy to a bad state the proxy refuses every later call but these,
    as a robot's stop; each is a tool no policy reads, which `valgebra_planning.proxy.Gate`
    holds when it is built.
    """
    return _named(directory, "afterViolation")


def non_text_blocks(directory: Path) -> frozenset[str]:
    """Return the tools a proxy answers with blocks other than text: ``"nonTextBlocks": [...]``.

    A proxy answers a result with the checked value as its text, and keeps a block that is not
    text, as a camera's image no check reads, only for these.
    """
    return _named(directory, "nonTextBlocks")


def forwards_elicitation(directory: Path) -> frozenset[str]:
    """Return the tools whose request for a person's answer a proxy forwards.

    ``"forwardsElicitation": [...]`` in ``tools.json``: a call to such a tool that its server
    answers by asking a person, a form or a URL, is answered with that request, and the answers
    that resume it are held to what was asked; any other request for input is refused.
    """
    return _named(directory, "forwardsElicitation")


def error_texts(directory: Path) -> dict[str, Any]:
    """Return the set each tool's error text lies in: ``"errorTexts": {tool: schema}``.

    A proxy passes on an error a tool answers where its text lies in the JSON Schema named for
    the tool, as an enum of the failures it reports, and answers any other with a fixed text.
    """
    named = _document(directory / "tools.json", _TOOLS_KEYS).get("errorTexts", {})
    if not isinstance(named, dict) or not all(
        isinstance(schema, (dict, bool)) for schema in named.values()
    ):
        message = f"tools.json: 'errorTexts' maps a tool's name to a JSON Schema, not {named!r}"
        raise TypeError(message)
    return dict(named)


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


_Rule = TypeVar("_Rule", Policy, Flow)


def _by_name(kinds: str, rules: Iterable[_Rule]) -> dict[str, _Rule]:
    """Return rules by name; two of one name are a `ValueError` naming it.

    A task names its rules by name, and a proxy keeps what a fresh rule's sources returned by
    the rule's name, so two rules of one name would hold one task to the last alone and share
    one store between two rules: the second of two ``measured`` rules from two sources admitted
    a value the first's source never returned.
    """
    named: dict[str, _Rule] = {}
    for rule in rules:
        if rule.name in named:
            message = f"two {kinds} are named {rule.name!r}: a name is one rule's"
            raise ValueError(message)
        named[rule.name] = rule
    return named


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
    ``tools.json``'s ``postconditions`` are read by
    `valgebra_planning.contracts.read_postcondition` over the tools read.
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
    # A program calls a tool Python does not spell by its alias; two tools one alias spells
    # would read as one, so the library is refused naming both.
    aliases(tools)
    declared = document.get("postconditions", [])
    if not isinstance(declared, list):
        msg = f"tools.json: 'postconditions' is a list, not {type(declared).__name__}"
        raise TypeError(msg)
    postconditions = tuple(
        read_postcondition(spec, tools, assert_format=asserted) for spec in declared
    )
    policies = _by_name(
        "policies",
        (
            read_policy(spec, assert_format=asserted)
            for spec in _listed(directory / "policies.json", "policies")
        ),
    )
    flows = _by_name(
        "flows",
        (
            read_flow(spec, assert_format=asserted)
            for spec in _listed(directory / "flows.json", "flows")
        ),
    )
    # A rule naming a tool, a hint or an argument the library lacks reads no call; a flow's
    # source may also be an input a task names.
    inputs = frozenset(name for spec in specs for name in spec.get("inputs", {}))
    for policy in policies.values():
        check_policy(policy, tools)
    for flow in flows.values():
        check_flow(flow, tools, inputs)
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
                postconditions=postconditions,
            )
        )
    return Library(tools, tuple(problems), policies, flows, postconditions)
