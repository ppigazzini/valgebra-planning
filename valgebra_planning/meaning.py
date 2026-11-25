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
  keys never fit, as a detection whose extra keys a distance would need;
- a result, or a field it guarantees, that any string fills: a proxy answers it to an agent
  inside the checked value, so no contract bounds the text a model reads there;
- a lapse out of a pending state into one that is not: the obligation the state holds ends with
  the time, and not with the call that meets it, as an object held that no call released.

A finding decides nothing: a plan, a proof and a run read the contracts as written, and what a
finding names is the author's to narrow or keep.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from valgebra_planning._valgebra import (
    Compiler,
    Unsupported,
    Validator,
    closed,
    compile_schema,
    intersection,
    interval,
    json_key,
    union,
)
from valgebra_planning.contracts import Contract, Slot, meet
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


def _named(path: tuple[str, ...]) -> str:
    """Return a place in a result as the note names it: ``[]`` an item, ``*`` a key unnamed."""
    return ".".join(path)


def _open_strings(contract: Contract) -> dict[tuple[str, ...], Certainty]:
    """Return each place in a contract's result that may hold any string, by its path.

    The note's reason is what a checked result may carry to an agent, so every place its output
    schema puts a value is read, required or not, at any depth, in an item, or under a key no
    property names: each subschema of ``properties``, ``patternProperties``,
    ``additionalProperties``, ``items`` and ``prefixItems``, through ``allOf``, whose branches
    constrain one value together, ``anyOf`` and ``oneOf``, whose branches are alternatives, and
    ``$ref``, each reference followed once. A place the companion will not compile apart is
    left out, as the planner's slots leave it.
    """
    whole = next((slot for slot in contract.results if not slot.path), None)
    if whole is None:
        return {}
    compiled = {id(s.schema): s.values for s in contract.results if s.root is whole.root}
    read = _Read(whole, compiled)
    found: dict[tuple[str, ...], Certainty] = {}
    for path, values in _places(read, whole.schema, frozenset()).items():
        if (certainty := _certainty(_STRINGS.relation_to(values))) is not None:
            found[path] = certainty
    return found


_Places = dict[tuple[str, ...], Validator[Any]]


@dataclass(slots=True)
class _Read:
    """A result as its places are read, and the sets its slots compiled already.

    ``compiled`` holds each slot's set by its subschema's identity, read against the same root.
    """

    whole: Slot
    compiled: Mapping[int, Validator[Any]]
    #: What compiles a place no slot compiled, made at the first.
    compiler: Compiler | None = None

    def set_of(self, schema: object) -> Validator[Any]:
        """Return the set ``schema`` compiles to; raises `Unsupported` where it does not."""
        known = self.compiled.get(id(schema)) if isinstance(schema, dict) else None
        if known is not None:
            return known
        if self.compiler is None:
            self.compiler = Compiler(self.whole.root, assert_format=self.whole.asserted)
        return self.compiler.compile(schema)


def _places(read: _Read, schema: object, followed: frozenset[str]) -> _Places:
    """Return the set of each place below ``schema`` a value may sit, by its path below it."""
    if not isinstance(schema, dict):
        return {}
    found: _Places = {}
    _meet(found, _own(read, schema, followed))
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference not in followed:
        target = _pointed(read.whole.root, reference)
        if target is not None:
            _meet(found, _places(read, target, followed | {reference}))
    for branch in schema.get("allOf") or []:
        _meet(found, _places(read, branch, followed))
    for key in ("anyOf", "oneOf"):
        branches = [_places(read, b, followed) for b in schema.get(key) or []]
        _meet(found, _either(branches))
    return found


def _own(read: _Read, schema: dict[str, Any], followed: frozenset[str]) -> _Places:
    """Return the places a schema's own keywords put a value, and the places below each."""
    subs: list[tuple[str, object]] = [
        *((name, sub) for name, sub in (schema.get("properties") or {}).items()),
        *(("*", sub) for sub in (schema.get("patternProperties") or {}).values()),
        *(("[]", sub) for sub in schema.get("prefixItems") or []),
    ]
    for step, key in (("*", "additionalProperties"), ("[]", "items")):
        if isinstance(schema.get(key), dict):
            subs.append((step, schema[key]))
    places: list[_Places] = []
    for step, sub in subs:
        try:
            here = read.set_of(sub)
        except Unsupported:
            continue
        below = _places(read, sub, followed)
        places.append({(step,): here} | {(step, *path): values for path, values in below.items()})
    # Two subschemas naming one place apart, as two patterns, are two keys' sets: either holds.
    return _either(places)


def _meet(found: _Places, places: _Places) -> None:
    """Meet each place's set with the one ``places`` gives it: both constrain one value."""
    for path, values in places.items():
        found[path] = intersection(found[path], values) if path in found else values


def _either(branches: list[_Places]) -> _Places:
    """Return each place's set as the union of the sets the branches give it: either holds."""
    joined: dict[tuple[str, ...], list[Validator[Any]]] = {}
    for places in branches:
        for path, values in places.items():
            joined.setdefault(path, []).append(values)
    return {path: sets[0] if len(sets) == 1 else union(*sets) for path, sets in joined.items()}


def _pointed(root: object, reference: str) -> object:
    """Return the subschema a reference into ``root`` names, or None for any other."""
    if not reference.startswith("#"):
        return None
    here = root
    for part in [p for p in reference[1:].split("/") if p]:
        step = part.replace("~1", "/").replace("~0", "~")
        if isinstance(here, dict) and step in here:
            here = here[step]
        elif isinstance(here, list) and step.isdigit() and int(step) < len(here):
            here = here[int(step)]
        else:
            return None
    return here


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
    listed = slot.listed()
    if not listed:
        return False
    return len({json_key(value) for value in listed}) == 1 or all(
        isinstance(value, bool) for value in listed
    )


def _gate_only(
    contract: Contract, tools: dict[str, Contract], goals: list[Validator[Any]]
) -> Certainty | None:
    """Return whether every argument a destructive tool's results meet is a gate, and no goal."""
    if "destructiveHint" not in contract.hints or not contract.results:
        return None
    met, undecided = [], False
    for result in contract.results:
        for other in goals:
            answer = meet(result.values, other)
            if answer == "not_subset":
                return None
            undecided |= answer == "undecided"
        for tool in tools.values():
            for argument in tool.arguments:
                answer = meet(result.values, argument.values)
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
        if meet(contract.returns, target) != "not_subset":
            continue
        certainty = _certainty(meet(shut, target))
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
        found += [
            Finding(
                where,
                f"{name}.{_named(path)} may return any string: an agent reads it unbounded",
                c,
            )
            for path, c in sorted(_open_strings(contract).items())
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
    return found + _unlisted(library) + _discharged(library)


def _discharged(library: Library) -> list[Finding]:
    """Return each lapse that leaves a pending state for one that is not.

    A pending state is an obligation a path may not end in, met by the call that leaves it; a
    lapse leaves it with no call, so the run ends with the obligation unmet and nothing open.
    """
    return [
        Finding(
            f"policy {name}",
            f"the lapse out of the pending {lapse.source} into {lapse.target} ends an obligation"
            f" after {lapse.seconds:g} s, with no call that meets it",
            "proven",
        )
        for name, policy in sorted(library.policies.items())
        for lapse in policy.lapses
        if lapse.source in policy.pending and lapse.target not in policy.pending
    ]


def _unlisted(library: Library) -> list[Finding]:
    """Return each tool that may act a fresh rule's list of withdrawing calls leaves out.

    The list is the author's claim that no other call changes what the rule's sources read, so
    each tool it trusts so is named, for the author to read.
    """
    found = []
    for name, flow in sorted(library.flows.items()):
        if not flow.withdrawn_by:
            continue
        for tool, contract in sorted(library.tools.items()):
            if "readOnlyHint" not in contract.hints and not flow.withdraws(tool, contract.hints):
                sources = ", ".join(sorted(flow.sources))
                said = f"{tool} may act, and its calls are taken to leave what {sources} read"
                found.append(Finding(f"flow {name}", said, "proven"))
    return found
