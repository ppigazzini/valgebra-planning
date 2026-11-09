"""Specifications that hold for a trivial reason, decided over a library's sets.

A goal no value meets makes every task over it unsolvable, and one every value meets makes any
program a solution; a policy transition whose guard no argument record of its tool meets never
fires, and a policy whose bad states no transition that can fire reaches is never violated; a
flow rule whose endorsed set misses the sink's argument lets nothing through, and one that holds
it refuses nothing. Each is a specification satisfied vacuously (Kupferman and Vardi 2003), a
check that passes without testing what its author meant, and each is a set question valgebra
decides: emptiness, inclusion, a meet.

The cheap question is asked first: values drawn from a schema are tried for membership, and a
member found is a proof the set is inhabited, so valgebra is asked only where none is. A question
valgebra leaves undecided is reported as undecided, never as vacuous.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from valgebra_planning._valgebra import JSON, Sampler, Unsupported, Validator, intersection, nothing
from valgebra_planning.cells import EDGES
from valgebra_planning.contracts import Contract
from valgebra_planning.flows import Flow
from valgebra_planning.library import Library
from valgebra_planning.policy import Policy

Certainty = Literal["proven", "undecided"]

#: How many values are drawn from a schema to look for a member before valgebra is asked.
DRAWS = 24


@dataclass(frozen=True, slots=True)
class Finding:
    """A specification holding vacuously, where it is written, and whether valgebra proves it."""

    where: str
    finding: str
    certainty: Certainty


def _drawn(schema: object, root: object) -> list[Any]:
    """Return values drawn from a schema with a fixed seed, none where it cannot be drawn from."""
    try:
        return Sampler(root, random.Random(0)).draw(schema, DRAWS)
    except (Unsupported, RecursionError):
        return []


def _records(contract: Contract) -> list[Any]:
    """Return argument records drawn from a tool's input schema."""
    if not contract.arguments:
        return [{}]
    root = contract.arguments[0].root
    return _drawn(root, root)


def _empty(values: Validator[Any], candidates: Iterable[Any] = ()) -> Certainty | None:
    """Return whether ``values`` is empty: not where a candidate lies in it, else valgebra's."""
    if any(values.is_valid(candidate) for candidate in candidates):
        return None
    answer = values.relation_to(nothing)
    return "proven" if answer == "subset" else ("undecided" if answer == "undecided" else None)


def _below(
    subject: Validator[Any], target: Validator[Any], candidates: Iterable[Any] = ()
) -> Certainty | None:
    """Return whether ``subject`` lies in ``target``: not where a candidate parts them."""
    if any(subject.is_valid(c) and not target.is_valid(c) for c in candidates):
        return None
    answer = subject.relation_to(target)
    return "proven" if answer == "subset" else ("undecided" if answer == "undecided" else None)


def _found(where: str, finding: str, certainty: Certainty | None) -> list[Finding]:
    return [] if certainty is None else [Finding(where, finding, certainty)]


def _tools(tools: dict[str, Contract]) -> list[Finding]:
    found = []
    for name, contract in tools.items():
        accepted = _empty(contract.accepts, _records(contract))
        found += _found(f"tool {name}", "no argument record is accepted", accepted)
        if contract.returns is not None:
            whole = contract.result()
            returned = _empty(contract.returns, _drawn(whole.schema, whole.root))
            found += _found(f"tool {name}", "no result is returned", returned)
    return found


def _policy(policy: Policy, tools: dict[str, Contract]) -> list[Finding]:
    where = f"policy {policy.name}"
    found, live = [], []
    for at, transition in enumerate(policy.transitions):
        step = f"{where}, transition {at} ({transition.source} -> {transition.target})"
        if transition.hint:
            if any(transition.hint in c.hints for c in tools.values()):
                live.append(transition)
            else:
                found.append(
                    Finding(step, f"reads {transition.hint}, which no tool sets", "proven")
                )
            continue
        contract = tools.get(transition.tool)
        if contract is None:
            found.append(Finding(step, f"names {transition.tool}, no tool", "proven"))
            continue
        dead = _empty(intersection(contract.accepts, transition.guard), _records(contract))
        found += _found(step, f"no argument record of {transition.tool} meets its guard", dead)
        if dead != "proven":
            live.append(transition)
    reached, frontier = {policy.initial}, [policy.initial]
    while frontier:
        state = frontier.pop()
        for transition in live:
            if transition.source == state and transition.target not in reached:
                reached.add(transition.target)
                frontier.append(transition.target)
    if not reached & policy.bad:
        found.append(Finding(where, "no transition that can fire reaches a bad state", "proven"))
    return found


def _flow(flow: Flow, tools: dict[str, Contract], inputs: set[str]) -> list[Finding]:
    where = f"flow {flow.name}"
    found = [
        Finding(where, f"source {source} names no tool and no task input", "proven")
        for source in sorted(flow.sources - set(tools) - inputs)
    ]
    contract = tools.get(flow.tool)
    names = (
        [] if contract is None else [s for s in contract.arguments if s.path == (flow.argument,)]
    )
    if not names:
        return [*found, Finding(where, f"sink {flow.sink} is no tool's argument", "proven")]
    if flow.endorsed is not None:
        sink, taken = names[0].values, _drawn(names[0].schema, names[0].root)
        meets = _empty(intersection(sink, flow.endorsed), taken)
        found += _found(where, f"the endorsed set meets nothing {flow.sink} takes", meets)
        holds = _below(sink, flow.endorsed, taken)
        found += _found(where, f"the endorsed set holds everything {flow.sink} takes", holds)
    return found


def vacuities(library: Library) -> list[Finding]:
    """Return every specification of ``library`` that holds vacuously, or may."""
    tools = dict(library.tools)
    found = _tools(tools)
    inputs = {name for problem in library.problems for name in problem.task.held}
    named_policies = {rule.name for problem in library.problems for rule in problem.policies}
    named_flows = {rule.name for problem in library.problems for rule in problem.flows}
    for problem in library.problems:
        task = problem.task
        where = f"task {task.name}"
        goal = _empty(task.goal, _drawn(task.goal_schema, task.goal_schema))
        found += _found(where, "no value meets the goal", goal)
        found += _found(where, "every JSON value meets the goal", _below(JSON, task.goal, EDGES))
        for name, slots in task.held.items():
            whole = next(slot for slot in slots if slot.path == ())
            held = _empty(whole.values, _drawn(whole.schema, whole.root))
            found += _found(where, f"no value meets the input {name}", held)
    for name, policy in library.policies.items():
        if name not in named_policies:
            found.append(Finding(f"policy {name}", "no task is held to it", "proven"))
        found += _policy(policy, tools)
    for name, flow in library.flows.items():
        if name not in named_flows:
            found.append(Finding(f"flow {name}", "no task is held to it", "proven"))
        found += _flow(flow, tools, inputs)
    return found
