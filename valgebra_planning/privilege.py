"""Privilege policies in Progent's format, read as valgebra sets (Shi et al. 2025, §4.1).

A policy maps each tool to its rules, each a priority, an effect, a condition and a fallback
(Shi et al. 2025, Fig. 9), read here as ``[priority, effect, condition, fallback]``: effect 0
allows and 1 forbids, and the condition maps an argument to the JSON Schema its value must meet
or to a regular expression a string value must match from its start. A condition checks only the
arguments a call passes. Rules are tried by priority, a forbidding rule before an allowing one
of the same priority, and the first whose condition holds decides; a call no rule matches, and a
call to a tool the policy names no rule for, is denied. Within one priority that is the order
Progent applies a policy in (Shi et al. 2025, §4.2, Alg. 2), which reads no priority. A rule
asking the user (fallback 2) is read as denied, the reading that admits no call the user did not
approve.

The calls a policy allows to a tool are then a set (Shi et al. 2025, §4.3): each allowing rule's
condition, minus the conditions of every rule tried before it, joined. Progent asks an SMT
solver whether an update narrows a policy, a tool at a time (Shi et al. 2025, §4.3); here the
question is an inclusion between two such sets, which valgebra decides, refutes with a call
that one policy allows and the other does not, or leaves undecided.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from valgebra_planning._valgebra import (
    Sampler,
    Unsupported,
    Validator,
    anything,
    compile_schema,
    complement,
    intersection,
    nothing,
    union,
)
from valgebra_planning.cells import EDGES
from valgebra_planning.policy import Policy, Transition

__all__ = [
    "Change",
    "Rule",
    "Update",
    "allowed",
    "as_policy",
    "describe",
    "read_privileges",
    "read_rules",
    "updates",
]

Change = Literal["narrowing", "expansion", "undecided"]


@dataclass(frozen=True, slots=True)
class Rule:
    """One rule of a tool: its priority, whether it allows, and the condition as a schema."""

    priority: int
    allows: bool
    condition: dict[str, Any]


def _restriction(restriction: object) -> object:
    """Return the schema an argument's restriction is: a schema, or a match from the start."""
    if isinstance(restriction, dict):
        return restriction
    if isinstance(restriction, str):
        return {"type": "string", "pattern": f"^(?:{restriction})"}
    # Progent's check raises on any other restriction, so its rule never matches.
    return False


def read_rules(rules: Iterable[Sequence[Any]]) -> tuple[Rule, ...]:
    """Read a tool's rules in the order they are tried: by priority, forbidding first."""
    read = [
        Rule(
            int(rule[0]),
            rule[1] == 0,
            {
                "type": "object",
                "properties": {name: _restriction(r) for name, r in rule[2].items()},
            },
        )
        for rule in rules
    ]
    return tuple(sorted(read, key=lambda rule: (rule.priority, rule.allows)))


def allowed(rules: Sequence[Rule]) -> Validator[Any]:
    """Return the argument records the rules allow: each allowing one's, minus those tried first."""
    allows, tried = nothing, nothing
    for rule in rules:
        matched = compile_schema(rule.condition)
        if rule.allows:
            allows = union(allows, intersection(matched, complement(tried)))
        tried = union(tried, matched)
    return allows


def read_privileges(policy: Mapping[str, Iterable[Sequence[Any]]]) -> dict[str, tuple[Rule, ...]]:
    """Read a policy in Progent's format: each tool's rules, in the order they are tried."""
    return {tool: read_rules(rules) for tool, rules in policy.items()}


@dataclass(frozen=True, slots=True)
class Update:
    """Whether a new policy narrows an old one for a tool, and a call showing an expansion."""

    tool: str
    change: Change
    witness: tuple[Any, ...] = ()


def _drawn(rules: Sequence[Rule], restricted: Iterable[str]) -> list[Any]:
    """Return candidate calls: drawn from each rule's condition, and edges for every argument.

    The draws use a fixed seed; the edges are one-argument records holding each edge value of
    the cells, for every argument a rule restricts.
    """
    drawn: list[Any] = [{name: edge} for name in sorted(restricted) for edge in EDGES]
    for rule in rules:
        try:
            drawn += Sampler(rule.condition, random.Random(0)).draw(rule.condition, 24)
        except (Unsupported, RecursionError):
            continue
    return drawn


def updates(old: Mapping[str, Sequence[Rule]], new: Mapping[str, Sequence[Rule]]) -> list[Update]:
    """Return, for each tool the new policy names, whether it narrows the old policy's calls.

    A narrowing is applied without asking, and an expansion needs approval, as Progent has it
    (Shi et al. 2025, §5).
    """
    found = []
    for tool, rules in sorted(new.items()):
        before, after = allowed(old.get(tool, ())), allowed(rules)
        restricted = {
            name for rule in (*old.get(tool, ()), *rules) for name in rule.condition["properties"]
        }
        witness = [
            call
            for call in _drawn(rules, restricted)
            if after.is_valid(call) and not before.is_valid(call)
        ]
        if witness:
            found.append(Update(tool, "expansion", (witness[0],)))
            continue
        answer = after.relation_to(before)
        change: Change = "undecided"
        if answer == "subset":
            change = "narrowing"
        elif answer == "not_subset":
            change = "expansion"
        found.append(Update(tool, change))
    return found


def as_policy(name: str, privileges: Mapping[str, Sequence[Rule]], tools: Iterable[str]) -> Policy:
    """Return the privilege policy as an automaton the verifier, planner and runner read.

    A call outside a tool's allowed set reaches the bad state ``denied``; a tool the policy names
    no rule for is denied every call.
    """
    transitions = tuple(
        Transition(
            "allowed",
            tool,
            complement(allowed(privileges[tool])) if tool in privileges else anything,
            "denied",
        )
        for tool in sorted(set(tools) | set(privileges))
    )
    return Policy(name, "allowed", frozenset({"denied"}), transitions)


def describe(update: Update) -> str:
    """Return an update as a line: the tool, the change, and the call showing an expansion."""
    shown = f", allowing {json.dumps(update.witness[0])}" if update.witness else ""
    return f"{update.tool}: {update.change}{shown}"
