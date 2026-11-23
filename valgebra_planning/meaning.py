"""Contracts wider than their author likely means, found over a library's sets.

A plan passes a value to an argument wherever valgebra proves it inside the argument's set, so
a plan means what its contracts let it mean: an argument taking any string takes any string a
program holds. Each finding here names a contract whose set is wider than its keywords suggest,
which `check` reports beside the vacuous specifications:

- an argument a `format` alone types, which takes every string where the library does not
  assert `format`;
- an argument with a numeric side no bound closes, which takes a number past any bound the
  author meant, infinity among them where it is a number;
- a `const` with no `type` beside it in an input schema, which a template showing a model the
  schema with `const` dropped shows as any value;
- a tool marked destructive, or left unmarked as MCP defaults to, whose results meet only
  arguments taking one value or a boolean and no task's goal: a plan calls it only to fill a
  gate, a motion made to answer a confirmation;
- a whole result, read as declared, that meets an argument or a task's goal only through keys
  its output schema does not declare: an open record takes any value at a key no field names,
  and a closed one none (Castagna 2023, §3.2), so a plan may pass the record where its declared
  keys never fit, as a detection whose extra keys a distance would need.

A finding decides nothing: a plan, a proof and a run read the contracts as written, and what a
finding names is the author's to narrow or keep.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from valgebra_planning._valgebra import (
    Unsupported,
    Validator,
    closed,
    compile_schema,
    finite,
    intersection,
    interval,
    json_key,
    nothing,
)
from valgebra_planning.contracts import Contract, Slot
from valgebra_planning.library import Library
from valgebra_planning.vacuity import Certainty, Finding

__all__ = ["findings"]

#: The keywords whose value maps names to schemas, each of a part of the instance or of it.
_NAMED = frozenset({"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"})

_STRINGS: Validator[Any] = compile_schema({"type": "string"})


def _certainty(answer: str) -> Certainty | None:
    """Return a finding's certainty from valgebra's answer that makes it, none where it parts."""
    return "proven" if answer == "subset" else ("undecided" if answer == "undecided" else None)


def _format_alone(slot: Slot) -> Certainty | None:
    """Return whether ``slot`` takes every string though its schema writes a `format`."""
    schema = slot.schema if isinstance(slot.schema, dict) else {}
    if "format" not in schema or slot.asserted:
        return None
    return _certainty(_STRINGS.relation_to(slot.values))


def _open_sides(slot: Slot) -> list[str]:
    """Return the sides of the range ``slot`` takes that no bound closes, where it is one."""
    reading = interval(slot.schema)
    if reading is None:
        return []
    return [
        side for side, bound in (("lower", reading.low), ("upper", reading.high)) if bound is None
    ]


def _untyped_consts(schema: object, path: str = "") -> Iterator[str]:
    """Yield the place of each `const` in ``schema`` with no `type` beside it."""
    if isinstance(schema, list):
        for index, item in enumerate(schema):
            yield from _untyped_consts(item, f"{path}/{index}")
        return
    if not isinstance(schema, dict):
        return
    if "const" in schema and "type" not in schema:
        yield path or "/"
    for keyword, value in schema.items():
        if keyword in ("const", "enum", "default", "examples"):
            continue
        if keyword in _NAMED and isinstance(value, dict):
            for name, part in value.items():
                yield from _untyped_consts(part, f"{path}/{keyword}/{name}")
        else:
            yield from _untyped_consts(value, f"{path}/{keyword}")


def _gate(slot: Slot) -> bool:
    """Return whether ``slot`` takes one JSON value, or booleans alone."""
    listed = finite(slot.schema, slot.root)
    if not listed:
        return False
    return len({json_key(value) for value in listed}) == 1 or all(
        isinstance(value, bool) for value in listed
    )


def _meets(one: Validator[Any], other: Validator[Any]) -> str:
    """Return valgebra's answer on whether two sets share no value."""
    return intersection(one, other).relation_to(nothing)


def _gate_only(
    contract: Contract, tools: dict[str, Contract], goals: list[Validator[Any]]
) -> Certainty | None:
    """Return whether every argument a destructive tool's results meet is a gate, and no goal."""
    if "destructiveHint" not in contract.hints or not contract.results:
        return None
    met, undecided = [], False
    for result in contract.results:
        for other in goals:
            answer = _meets(result.values, other)
            if answer == "not_subset":
                return None
            undecided |= answer == "undecided"
        for tool in tools.values():
            for argument in tool.arguments:
                answer = _meets(result.values, argument.values)
                undecided |= answer == "undecided"
                if answer == "not_subset":
                    met.append(argument)
    if not met or not all(_gate(argument) for argument in met):
        return None
    return "undecided" if undecided else "proven"


def _undeclared_only(
    contract: Contract, targets: list[tuple[str, Validator[Any]]]
) -> Iterator[tuple[str, Certainty]]:
    """Yield each target the whole result meets only through keys its schema does not declare.

    The meet is asked of the result as declared and of it read closed at its root, where a
    nested record stays as written: `unevaluatedProperties: false` refuses a key exactly where no
    keyword evaluated it (Attouche et al. 2024, §2.2), so a target the first meets and the second
    misses is met through undeclared keys alone. A result the library reads closed meets no such
    target, and one the companion cannot close is not asked.
    """
    if contract.returns is None or contract.declared is not None or not contract.results:
        return
    whole = contract.result()
    try:
        shut = compile_schema(closed(whole.schema), assert_format=whole.asserted)
    except Unsupported:
        return
    for name, target in targets:
        if _meets(contract.returns, target) != "not_subset":
            continue
        certainty = _certainty(_meets(shut, target))
        if certainty is not None:
            yield name, certainty


def findings(library: Library) -> list[Finding]:
    """Return every contract of ``library`` wider than its keywords suggest, or that may be."""
    tools = dict(library.tools)
    goals = [problem.task.goal for problem in library.problems]
    targets = [(slot.name, slot.values) for tool in tools.values() for slot in tool.arguments] + [
        (f"the goal of {p.task.name}", p.task.goal) for p in library.problems
    ]
    found: list[Finding] = []
    for name, contract in sorted(tools.items()):
        where = f"tool {name}"
        for slot in contract.arguments:
            certainty = _format_alone(slot)
            if certainty is not None:
                found.append(
                    Finding(
                        where,
                        f"{slot.name} takes every string: its `format` is read as an annotation",
                        certainty,
                    )
                )
            found += [
                Finding(where, f"{slot.name} has no {side} bound", "proven")
                for side in _open_sides(slot)
            ]
        if contract.arguments:
            found += [
                Finding(
                    where,
                    f"the input schema's `const` at {place} has no `type` beside it",
                    "proven",
                )
                for place in _untyped_consts(contract.arguments[0].root)
            ]
        found += [
            Finding(
                where,
                f"its result meets {target} only through keys its output schema does not declare",
                certainty,
            )
            for target, certainty in _undeclared_only(contract, targets)
        ]
        gate = _gate_only(contract, tools, goals)
        if gate is not None:
            found.append(
                Finding(
                    where,
                    "it is destructive, and its results meet only arguments taking one value or"
                    " a boolean, and no goal: a plan calls it only to fill a gate",
                    gate,
                )
            )
    return found
