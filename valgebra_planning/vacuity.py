"""Specifications that hold for a trivial reason, decided over a library's sets.

A goal no value meets makes every task over it unsolvable, and one every value meets makes any
program a solution; a policy transition whose guard no argument record of its tool meets never
fires, and a policy whose bad and pending states no transition that can fire reaches is never
violated; a flow rule whose endorsed set misses the sink's argument lets nothing through, and
one that holds it refuses nothing; a postcondition whose condition meets no result of its call
covers no read, and one whose read's set meets no result, or holds every one, says nothing
possible or nothing at all. Each is a specification satisfied vacuously (Kupferman and
Vardi 1999, Def. 2), a check that passes without testing what its author meant, and each is a
set question valgebra decides: emptiness, inclusion, a meet.

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
from valgebra_planning.contracts import Contract, Postcondition
from valgebra_planning.flows import Flow
from valgebra_planning.library import Library
from valgebra_planning.policy import Policy, Transition

__all__ = ["Certainty", "Finding", "vacuities"]

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
            drawn = _drawn(whole.schema, whole.root)
            returned = _empty(contract.returns, drawn)
            if returned and contract.declared is not None and not _empty(contract.declared, drawn):
                # The server's set is inhabited: the closed reading empties it, so every link
                # over the result holds vacuously under the assumption alone.
                said = "no result is returned read closed, though its output schema admits one"
                found += _found(f"tool {name}", said, returned)
            else:
                found += _found(f"tool {name}", "no result is returned", returned)
    return found


def _reached(policy: Policy, live: list[Transition]) -> set[str]:
    """Return the states the transitions that can fire reach; a bad state is never left."""
    reached, frontier = {policy.initial}, [policy.initial]
    while frontier:
        state = frontier.pop()
        if state in policy.bad:
            continue
        for transition in live:
            if transition.source == state and transition.target not in reached:
                reached.add(transition.target)
                frontier.append(transition.target)
    return reached


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
    if not _reached(policy, live) & (policy.bad | policy.pending):
        found.append(
            Finding(where, "no transition that can fire reaches a bad or pending state", "proven")
        )
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


def _postcondition(post: Postcondition, tools: dict[str, Contract]) -> list[Finding]:
    """Return what holds vacuously in a postcondition: a claim never made, or made of nothing.

    One whose ``when`` meets no result of its ``after`` covers no read, one whose ``shows``
    meets no result of its ``read`` says no read is possible, and one whose ``shows`` holds
    every such result narrows nothing (Kupferman and Vardi 1999, Def. 2).
    """
    where = f"postcondition of {post.after} on {post.read}"
    after, read = tools[post.after], tools[post.read]
    returned = JSON if after.returns is None else after.returns
    read_back = JSON if read.returns is None else read.returns
    covers = _empty(intersection(returned, post.when))
    found = _found(where, f"its when meets no result of {post.after}", covers)
    possible = _empty(intersection(read_back, post.shows))
    found += _found(where, f"its shows meets no result of {post.read}", possible)
    narrows = _below(read_back, post.shows)
    return found + _found(where, f"its shows holds every result of {post.read}", narrows)


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
    for post in library.postconditions:
        found += _postcondition(post, tools)
    return found
