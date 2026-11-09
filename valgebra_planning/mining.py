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

from collections.abc import Iterable, Mapping
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
