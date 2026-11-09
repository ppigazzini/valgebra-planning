"""Semantic types mined from observed calls, stated as assumptions.

Two slots with the same schema are one set to valgebra, so the planner may pass a customer's
name where a ticket's subject goes. Observed calls tell them apart: a value a tool returned
that a later call took as an argument links the two slots, and slots linked by any shared
value form one semantic type (APIphany, Guo et al. 2022). A binding across two types is then
refused, which narrows the planner's choices to what the observations show.

A mined type is an assumption, never a proof: the verifier reads none, every link of a
program stays valgebra's, and a plan names each binding that relied on one. A slot never
observed carries no type and is bound as its set alone allows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from valgebra_planning._valgebra import json_key
from valgebra_planning.contracts import Contract

#: A slot as the miner names it: ``("argument", "tool.name")`` or ``("result", "tool.path")``.
SlotKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Types:
    """The semantic type of every slot seen in the observations."""

    of: Mapping[SlotKey, int]

    def agree(self, one: SlotKey, other: SlotKey) -> bool:
        """Return whether two slots may be bound together: one is untyped, or both share one."""
        return one not in self.of or other not in self.of or self.of[one] == self.of[other]

    def typed(self, one: SlotKey, other: SlotKey) -> bool:
        """Return whether both slots carry a type, so binding them rests on the observations."""
        return one in self.of and other in self.of


def _walk(value: Any, path: tuple[str, ...]) -> tuple[bool, Any]:
    for name in path:
        if not isinstance(value, dict) or name not in value:
            return False, None
        value = value[name]
    return True, value


def _seen(value: Any) -> bool:
    """Return whether a value is specific enough to link two slots: no boolean, no null."""
    return value is not None and not isinstance(value, bool)


def _holders(
    observed: Iterable[Mapping[str, Any]], contracts: Mapping[str, Contract]
) -> dict[object, list[SlotKey]]:
    """Return, for every value observed, the slots it was seen in."""
    holders: dict[object, list[SlotKey]] = {}
    for call in observed:
        contract = contracts.get(call["tool"])
        if contract is None:
            continue
        seen = [
            (("argument", slot.name), _walk(call.get("arguments", {}), slot.path))
            for slot in contract.arguments
        ] + [
            (("result", slot.name), _walk(call.get("result"), slot.path))
            for slot in contract.results
            if slot.path
        ]
        for key, (found, value) in seen:
            if found and _seen(value):
                holders.setdefault(json_key(value), []).append(key)
    return holders


def mine(observed: Iterable[Mapping[str, Any]], contracts: Mapping[str, Contract]) -> Types:
    """Return the semantic types the observed calls show.

    Each observation is ``{"tool": name, "arguments": {...}, "result": ...}``. Every argument
    and every result field a contract names is a slot; two slots holding one value, under
    JSON equality, are one type.
    """
    parent: dict[SlotKey, SlotKey] = {}

    def root(key: SlotKey) -> SlotKey:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for keys in _holders(observed, contracts).values():
        for key in keys[1:]:
            parent[root(key)] = root(keys[0])
        root(keys[0])
    roots = sorted({root(key) for key in parent})
    return Types({key: roots.index(root(key)) for key in parent})


#: The JSON kind of a Python value `json.loads` returns, a bool being no integer.
def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def shape(values: Sequence[Any]) -> dict[str, Any]:
    """Return the JSON Schema of observed values: each kind they take, and nothing narrower.

    An object holds every key any observed object holds, the keys every one holds required;
    an array's items are the shape of every item seen; an integer seen beside a number is a
    number. No pattern, enum or bound is guessed: the shape is what was seen, generalized only
    to the kind.
    """
    kinds: dict[str, list[Any]] = {}
    for value in values:
        kinds.setdefault(_kind(value), []).append(value)
    if "integer" in kinds and "number" in kinds:
        kinds["number"] += kinds.pop("integer")
    found = []
    for kind, seen in sorted(kinds.items()):
        if kind == "object":
            keys = sorted({key for value in seen for key in value})
            found.append(
                {
                    "type": "object",
                    "properties": {k: shape([v[k] for v in seen if k in v]) for k in keys},
                    "required": [k for k in keys if all(k in v for v in seen)],
                }
            )
        elif kind == "array":
            items = [item for value in seen for item in value]
            found.append({"type": "array", **({"items": shape(items)} if items else {})})
        else:
            found.append({"type": kind})
    return found[0] if len(found) == 1 else {"anyOf": found}


def infer_outputs(
    observed: Iterable[Mapping[str, Any]], listing: Iterable[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Return an output schema for each listed tool declaring none, shaped from its results.

    An inferred schema is an assumption, as a mined type is: a contract carrying one is marked
    `valgebra_planning.contracts.Contract.inferred`, and every link and binding resting on it
    names it.
    """
    bare = {tool["name"] for tool in listing if "outputSchema" not in tool}
    results: dict[str, list[Any]] = {}
    for call in observed:
        if call.get("tool") in bare and "result" in call:
            results.setdefault(call["tool"], []).append(call["result"])
    return {name: shape(seen) for name, seen in results.items()}
