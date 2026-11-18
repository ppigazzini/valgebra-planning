"""The cells: the minterms of every set a plan reads, within each kind of JSON value.

Every slot, input and goal is a predicate on JSON values. Each predicate taken inside or
outside cuts the values into cells, and each predicate is exactly a union of cells, so an
inclusion between two predicates' cells implies the inclusion between the predicates: the
canonical monomials of the predicates as abstract states (Graf and Saidi 1997, §2.2), the
minterms of an effective Boolean algebra (D'Antoni and Veanes 2017, §2.1). The values split
first into seven disjoint kinds, and a predicate cuts only the
cells of the kinds it meets.

A part of a cell is dropped only where valgebra proves it empty. It is inhabited where a
concrete value lies in it, or where valgebra refutes its emptiness. Every other part is
kept as undecided, so it can cost a plan, never admit a wrong one. Most cuts are settled by
relations between two predicates, each asked of valgebra once: a cell inside a predicate
disjoint from the new one lies outside it, a cell inside a predicate below the new one lies
inside it, and a cell outside a predicate above it lies outside it.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from valgebra_planning._valgebra import (
    ARRAY,
    BOOLEAN,
    INTEGER,
    NULL,
    NUMBER,
    OBJECT,
    STRING,
    Sampler,
    Unsupported,
    Validator,
    complement,
    finite,
    intersection,
    nothing,
)

__all__ = ["Abstraction", "Cell", "Predicate", "Status", "abstract", "gather"]

#: The seven kinds of JSON value, which partition every value `json.loads` returns that holds
#: no NaN: JSON has none (RFC 8259 §6), and valgebra-jsonschema compiles no set holding one.
KINDS: dict[str, Validator[Any]] = {
    "null": NULL,
    "boolean": BOOLEAN,
    "integer": INTEGER,
    "fraction": intersection(NUMBER, complement(INTEGER)),
    "string": STRING,
    "array": ARRAY,
    "object": OBJECT,
}

#: Values at the edges of common bounds, tried in every cell before any draw.
EDGES: tuple[Any, ...] = (
    None,
    True,
    False,
    0,
    1,
    -1,
    2,
    100,
    1000,
    0.5,
    -0.5,
    1.5,
    float("inf"),
    "",
    "a",
    "0",
    [],
    [None],
    {},
)

#: How many values are drawn from each predicate's schema.
DRAWS = 24

#: How many objects of each schema's draws are merged with every other schema's.
MERGED = 2

#: How many seeded merges of a random subset of the object schemas are tried.
SUBSETS = 64

Status = Literal["inhabited", "undecided"]


@dataclass(frozen=True, slots=True)
class Predicate:
    """A set a plan reads, with the schema it compiles from and the names standing for it."""

    names: tuple[str, ...]
    values: Validator[Any]
    schema: object
    root: object


@dataclass(frozen=True, slots=True)
class Cell:
    """One minterm: a kind, the predicates it lies inside, and whether it holds a value.

    The cell lies outside every predicate of its kind not in ``inside``. ``witness`` holds
    a member where one is known; an inhabited cell without one was proven non-empty.
    """

    kind: str
    inside: frozenset[int]
    values: Validator[Any]
    status: Status
    witness: tuple[Any, ...] = ()


@dataclass(slots=True)
class _Split:
    kind: str
    inside: set[int]
    outside: set[int]
    values: Validator[Any]
    witnesses: list[Any]
    proven: bool = False


@dataclass(slots=True)
class Abstraction:
    """The predicates of a plan and the cells they cut the JSON values into."""

    predicates: tuple[Predicate, ...]
    cells: tuple[Cell, ...]
    asked: int = 0
    _members: dict[int, frozenset[int]] = field(default_factory=dict)

    def index(self, name: str) -> int:
        """Return the predicate a name stands for, or raise `KeyError`."""
        for i, predicate in enumerate(self.predicates):
            if name in predicate.names:
                return i
        raise KeyError(name)

    def cells_of(self, predicate: int) -> frozenset[int]:
        """Return the cells lying inside a predicate."""
        if predicate not in self._members:
            self._members[predicate] = frozenset(
                i for i, cell in enumerate(self.cells) if predicate in cell.inside
            )
        return self._members[predicate]

    def members(self, cell: int) -> tuple[Any, ...]:
        """Return the values a cell holds where a predicate it lies inside lists them all.

        Elsewhere, the member found for it, if any.
        """
        held = self.cells[cell]
        for i in sorted(held.inside):
            predicate = self.predicates[i]
            listed = finite(predicate.schema, predicate.root)
            if listed is not None:
                return tuple(v for v in listed if held.values.is_valid(v))
        return held.witness

    def undecided(self, cells: Iterable[int]) -> int:
        """Return how many of ``cells`` may be empty."""
        return sum(self.cells[i].status == "undecided" for i in cells)


def gather(entries: Iterable[tuple[str, Validator[Any], object, object]]) -> list[Predicate]:
    """Merge named sets valgebra holds equal into one predicate each, keeping order.

    Two schemas differing in a description, a title or a default compile to one set, and a
    predicate per spelling would only ask each relation again; the first spelling stays, to
    draw members from.
    """
    merged: dict[Validator[Any], Predicate] = {}
    for name, values, schema, root in entries:
        if values in merged:
            held = merged[values]
            merged[values] = Predicate((*held.names, name), held.values, held.schema, held.root)
        else:
            merged[values] = Predicate((name,), values, schema, root)
    return list(merged.values())


class _Relations:
    """Relations between two predicates, each asked of valgebra at most once."""

    def __init__(self, predicates: Sequence[Predicate]) -> None:
        self.predicates = predicates
        self.below: dict[tuple[int, int], bool] = {}
        self.apart: dict[tuple[int, int], bool] = {}
        self.asked = 0

    def is_below(self, i: int, j: int) -> bool:
        """Return whether predicate ``i`` is proven inside ``j``."""
        if (i, j) not in self.below:
            self.asked += 1
            answer = self.predicates[i].values.relation_to(self.predicates[j].values)
            self.below[i, j] = answer == "subset"
        return self.below[i, j]

    def are_apart(self, i: int, j: int) -> bool:
        """Return whether the two predicates are proven to share no value."""
        key = (min(i, j), max(i, j))
        if key not in self.apart:
            self.asked += 1
            meet = intersection(self.predicates[i].values, self.predicates[j].values)
            self.apart[key] = meet.relation_to(nothing) == "subset"
        return self.apart[key]


def _pool(predicates: Sequence[Predicate], rng: random.Random) -> list[Any]:
    """Return candidate members: the edges, every listed value, and draws from each schema.

    An open record meets another wherever a value carries both their keys, which no draw from
    one schema does, so the objects drawn are also merged: every pair, and seeded subsets of
    up to every one. Membership alone judges each candidate.
    """
    pool = list(EDGES)
    objects: list[list[dict[str, Any]]] = []
    for predicate in predicates:
        listed = finite(predicate.schema, predicate.root)
        if listed:
            pool.extend(listed)
        try:
            drawn = Sampler(predicate.root, rng).draw(predicate.schema, DRAWS)
        except (Unsupported, RecursionError):
            continue
        pool.extend(drawn)
        records = [value for value in drawn if isinstance(value, dict)][:MERGED]
        if records:
            objects.append(records)
    for first, second in itertools.combinations(objects, 2):
        pool.extend({**a, **b} for a in first for b in second)
    for _ in range(SUBSETS):
        chosen = rng.sample(objects, rng.randint(2, len(objects))) if len(objects) > 1 else []
        merged: dict[str, Any] = {}
        for records in chosen:
            merged.update(rng.choice(records))
        if merged:
            pool.append(merged)
    return pool


def _emptiness(values: Validator[Any]) -> Literal["empty", "inhabited", "undecided"]:
    answer = values.relation_to(nothing)
    if answer == "subset":
        return "empty"
    return "inhabited" if answer == "not_subset" else "undecided"


def _cut(split: _Split, i: int, predicate: Predicate, relations: _Relations) -> list[_Split]:
    """Cut one cell by one predicate, keeping each part that may hold a value."""
    if any(relations.are_apart(i, j) for j in split.inside) or any(
        relations.is_below(i, j) for j in split.outside
    ):
        split.outside.add(i)
        return [split]
    if any(relations.is_below(j, i) for j in split.inside):
        split.inside.add(i)
        return [split]
    parts = []
    for inside in (True, False):
        held = split.inside | {i} if inside else set(split.inside)
        out = set(split.outside) if inside else split.outside | {i}
        values = intersection(
            split.values, predicate.values if inside else complement(predicate.values)
        )
        witnesses = [v for v in split.witnesses if predicate.values.is_valid(v) == inside]
        proven = bool(witnesses)
        if not witnesses:
            emptiness = _emptiness(values)
            if emptiness == "undecided":
                whole = _meet(split.kind, held, out, relations)
                # An equal validator is the question already asked: one set, one answer.
                if whole != values:
                    emptiness = _emptiness(whole)
            if emptiness == "empty":
                continue
            proven = emptiness == "inhabited"
        parts.append(_Split(split.kind, held, out, values, witnesses, proven))
    return parts


def _meet(kind: str, inside: set[int], outside: set[int], relations: _Relations) -> Validator[Any]:
    """Return a part's set spelled whole: its kind, and each predicate it lies inside or outside.

    A part is asked first as the meet its cell holds with the new predicate or its complement,
    and in this spelling where valgebra leaves that one undecided. The two are one set, and
    each is decided where the other is not: this one holds the complement of each predicate
    the cell lies outside by a relation, which some meets of arrays of records need, and the
    meet built cut by cut holds the complements of the predicates it was cut by alone, which
    keeps a meet of many overlapping ranges within what valgebra decides.

    The complement of a predicate sharing no value with one the cell lies inside is left out:
    that one lies inside the complement already, so the set is the same and the question
    smaller.
    """
    predicates = relations.predicates
    kept = [q for q in sorted(outside) if not any(relations.are_apart(q, p) for p in inside)]
    return intersection(
        KINDS[kind],
        *(predicates[p].values for p in sorted(inside)),
        *(complement(predicates[q].values) for q in kept),
    )


def abstract(predicates: Sequence[Predicate], *, seed: int = 0) -> Abstraction:
    """Cut the JSON values into the cells of ``predicates``."""
    rng = random.Random(seed)
    relations = _Relations(predicates)
    pool = _pool(predicates, rng)
    cells: list[Cell] = []
    for kind, kind_values in KINDS.items():
        meets = [
            i
            for i, predicate in enumerate(predicates)
            if _emptiness(intersection(kind_values, predicate.values)) != "empty"
        ]
        members = [v for v in pool if kind_values.is_valid(v)]
        splits = [_Split(kind, set(), set(), kind_values, members, proven=bool(members))]
        for i in meets:
            splits = [part for split in splits for part in _cut(split, i, predicates[i], relations)]
        cells.extend(
            Cell(
                kind=split.kind,
                inside=frozenset(split.inside),
                values=split.values,
                status="inhabited" if split.proven else "undecided",
                witness=tuple(split.witnesses[:1]),
            )
            for split in splits
        )
    return Abstraction(tuple(predicates), tuple(cells), asked=relations.asked)
