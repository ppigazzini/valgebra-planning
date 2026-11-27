"""Semantic types mined from observed calls, stated as assumptions.

Two slots with the same schema are one set to valgebra, so the planner may pass a customer's
name where a ticket's subject goes. Observed calls tell them apart: a value a tool returned
that a later call took as an argument links the two slots, and slots linked by any shared
value form one semantic type (APIphany, Guo et al. 2022, §4). A binding across two types is then
refused, which narrows the planner's choices to what the observations show.

A witness is a call a server answered, its arguments chosen elsewhere: APIphany collects them
ahead of time, in a sandbox or from production traffic (Guo et al. 2022, §1), and a value two
locations share by chance merges them spuriously (Guo et al. 2022, §7.2). A value from a small
domain is shared by chance most: an integer of 1000 or less links nothing, and a slot seen with
one keeps a type of its own, as APIphany merges strings and large integers alone (Guo et al.
2022, §7.4). A call a planned
program made, marked ``"planned": true``, shares a value with the result it was bound from by
construction, so its arguments link nothing; its result is the server's and links as any.

A task input is a value from outside the tools. A log may record the inputs a run started
with, ``{"inputs": {...}}``, and an input seen so is typed as any slot is. An input never seen
fills an argument only where no tool's result was seen in the argument's type: where the
observations show a slot's values coming from a tool, a value from outside is not one of them.

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

__all__ = ["SlotKey", "Types", "infer_outputs", "mine"]

#: A slot as the miner names it: ``("argument", "tool.name")``, ``("result", "tool.path")``, or
#: ``("input", "name.path")`` for a task input.
SlotKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Types:
    """The semantic type of every slot seen in the observations."""

    of: Mapping[SlotKey, int]
    #: The types a tool's result was seen in.
    returned: frozenset[int] = frozenset()

    def agree(self, value: SlotKey, argument: SlotKey) -> bool:
        """Return whether a value's slot may fill an argument.

        Two typed slots agree where they share a type. A task input never observed agrees with
        an argument whose type holds no result, its values being seen to come from outside the
        tools; any other untyped slot agrees with every argument.
        """
        if value in self.of and argument in self.of:
            return self.of[value] == self.of[argument]
        if value[0] == "input" and value not in self.of:
            return argument not in self.of or self.of[argument] not in self.returned
        return True

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


#: The integer past which a value is taken to name something rather than count it.
_LARGE = 1000


def _linking(value: Any, key: SlotKey) -> object:
    """Return what two slots share to be one type: the value, or the slot's own for a small integer.

    A page and a limit both hold 1, a total and a core count 8: value-based merging works for
    strings and fails for small integers, so APIphany merges "only for strings and large
    integers (> 1000)" (Guo et al. 2022, §7.4). An integral number of 1000 or less links
    nothing; the slot it was seen in is typed, alone.
    """
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and float(value).is_integer()
        and value <= _LARGE
    ):
        return ("alone", key)
    return json_key(value)


def _inputs(value: Any, path: tuple[str, ...]) -> list[tuple[SlotKey, tuple[bool, Any]]]:
    """Return a task input's slots: the whole value, and each field of a record, as held."""
    found = [(("input", ".".join(path)), (True, value))]
    if isinstance(value, dict):
        for name, field in value.items():
            found += _inputs(field, (*path, name))
    return found


def _holders(
    observed: Iterable[Mapping[str, Any]], contracts: Mapping[str, Contract]
) -> dict[object, list[SlotKey]]:
    """Return, for every value observed, the slots it was seen in."""
    holders: dict[object, list[SlotKey]] = {}
    for call in observed:
        if "inputs" in call:
            for name, value in call["inputs"].items():
                for key, (_, held) in _inputs(value, (name,)):
                    if _seen(held):
                        holders.setdefault(_linking(held, key), []).append(key)
            continue
        contract = contracts.get(call["tool"])
        if contract is None:
            continue
        chosen = () if call.get("planned") else contract.arguments
        seen = [
            (("argument", slot.name), _walk(call.get("arguments", {}), slot.path))
            for slot in chosen
        ] + [
            (("result", slot.name), _walk(call.get("result"), slot.path))
            for slot in contract.results
            if slot.path
        ]
        for key, (found, value) in seen:
            if found and _seen(value):
                holders.setdefault(_linking(value, key), []).append(key)
    return holders


def mine(observed: Iterable[Mapping[str, Any]], contracts: Mapping[str, Contract]) -> Types:
    """Return the semantic types the observed calls show.

    Each observation is ``{"tool": name, "arguments": {...}, "result": ...}``, with
    ``"planned": true`` where a planned program made the call, or ``{"inputs": {...}}``, the
    values a run started with. Every argument and every result field a contract names is a slot,
    and so is each input and each of its fields; two slots holding one value, under JSON
    equality, are one type. A planned call's arguments are no slot.
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
    of = {key: roots.index(root(key)) for key in parent}
    return Types(of, frozenset(of[key] for key in of if key[0] == "result"))


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
