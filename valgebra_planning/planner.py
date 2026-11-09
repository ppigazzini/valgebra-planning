"""Programs of calls and type tests, searched over the cells.

A state maps every reference the agent holds to the cells of the set it lies in. A call is
enabled when each required argument can be bound to a reference whose cells all lie inside
the argument's cells, and, where the input schema relates its arguments, valgebra proves the
whole record inside it. A guarded call handles a reference that only overlaps an argument:
it branches on the reference lying in the argument, calls the tool on the narrowed value,
and goes on with the rest in the other branch. A program is strong when every path returns
a reference whose cells lie inside the goal's: strong planning under nondeterministic
outcomes (Cimatti, Pistore, Roveri and Traverso 2003), the outcomes partitioned by the cells
rather than listed, and a branch on a runtime type test as an observation (Bertoli,
Cimatti, Roveri and Traverso 2006).

The search deepens on the calls and tests along a path, so the first strong program found is
a shallowest one. Where none exists within the bound, one search at the full bound lets a
branch stop once its sibling returns and no continuation exists within the depth its path has
left, and each stop names the cells it leaves uncovered. Every program
returned is checked again by `valgebra_planning.verify.verify`, which reads neither the cells
nor the search.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from valgebra_planning._valgebra import Validator, union
from valgebra_planning.cells import Abstraction, Predicate, abstract, gather
from valgebra_planning.contracts import Contract, Slot, Task
from valgebra_planning.program import Block, Branch, Call, Ref, Return, Stop, render, renumber

#: The calls and tests a path may take.
DEPTH = 6

#: How many bindings of one call are tried; a result's cells do not depend on the binding,
#: so one that checks stands for the rest.
MAX_BINDINGS = 16

_GOAL = "goal"

#: A guarded call takes a test and a call on its path.
_GUARD = 2


def _argument_name(slot: Slot) -> str:
    return f"argument:{slot.name}"


def _result_name(slot: Slot) -> str:
    return f"result:{slot.name}"


def _held_name(slot: Slot) -> str:
    return f"held:{slot.name}"


@dataclass(frozen=True, slots=True)
class Uncovered:
    """A stop of a weak program: where, the reference, and the values it leaves uncovered.

    ``examples`` holds a member of each uncovered cell that has one; ``undecided`` counts
    the uncovered cells that may be empty.
    """

    where: str
    ref: str
    examples: tuple[Any, ...]
    undecided: int


@dataclass(frozen=True, slots=True)
class Plan:
    """A program for a task, whether it is strong, and what its stops leave uncovered."""

    program: Block | None
    strong: bool
    uncovered: tuple[Uncovered, ...]
    abstraction: Abstraction
    expanded: int


@dataclass(frozen=True, slots=True)
class _State:
    refs: tuple[tuple[Ref, frozenset[int]], ...]
    made: frozenset[tuple[str, tuple[tuple[str, Ref], ...]]]
    fresh: int

    def cells(self, ref: Ref) -> frozenset[int]:
        return dict(self.refs)[ref]

    def narrowed(self, ref: Ref, cells: frozenset[int]) -> _State:
        refs = tuple((r, cells if r == ref else held) for r, held in self.refs)
        return _State(refs, self.made, self.fresh)

    def signature(self) -> frozenset[frozenset[int]]:
        return frozenset(cells for _, cells in self.refs)


@dataclass(slots=True)
class _Search:
    contracts: Mapping[str, Contract]
    abstraction: Abstraction
    goal: frozenset[int]
    failed: set[tuple[frozenset[frozenset[int]], int, bool]] = field(default_factory=set)
    expanded: int = 0
    #: The cells each stop leaves uncovered, by the stop's identity: a stop is made once
    #: and read back only from the program returned, which keeps it alive.
    stops: dict[int, frozenset[int]] = field(default_factory=dict)

    def cells_of(self, name: str) -> frozenset[int]:
        return self.abstraction.cells_of(self.abstraction.index(name))

    def values(self, cells: frozenset[int]) -> Validator[Any]:
        return union(*(self.abstraction.cells[i].values for i in sorted(cells)))

    def solve(self, state: _State, depth: int, *, weak: bool) -> Block | None:
        for ref, cells in state.refs:
            if cells <= self.goal:
                return Block((), Return(ref))
        key = (state.signature(), depth, weak)
        if depth == 0 or key in self.failed:
            return None
        self.expanded += 1
        for contract in self.contracts.values():
            found = self.call(state, contract, depth, weak=weak)
            if found is not None:
                return found
        self.failed.add(key)
        return None

    def call(self, state: _State, contract: Contract, depth: int, *, weak: bool) -> Block | None:
        required = [slot for slot in contract.arguments if slot.required]
        full: dict[str, list[Ref]] = {}
        partial: dict[str, list[Ref]] = {}
        for slot in required:
            arg = self.cells_of(_argument_name(slot))
            full[slot.path[0]] = [r for r, c in state.refs if c and c <= arg]
            partial[slot.path[0]] = [r for r, c in state.refs if c & arg and not c <= arg]
        if any(not full[name] and not partial[name] for name in full):
            return None
        if all(full.values()):
            block = self.direct(state, contract, full, depth, weak=weak)
            if block is not None:
                return block
        if depth < _GUARD:
            return None
        for name, refs in partial.items():
            for ref in refs:
                block = self.guarded(state, contract, name, ref, depth, weak=weak)
                if block is not None:
                    return block
        return None

    def direct(
        self,
        state: _State,
        contract: Contract,
        choices: dict[str, list[Ref]],
        depth: int,
        *,
        weak: bool,
    ) -> Block | None:
        names = list(choices)
        for picked in itertools.islice(itertools.product(*choices.values()), MAX_BINDINGS):
            bindings = tuple(zip(names, picked, strict=True))
            if (contract.name, bindings) in state.made or not self.record(
                state, contract, bindings
            ):
                continue
            result = f"c{state.fresh}"
            after = self.after(state, contract, bindings, result)
            rest = self.solve(after, depth - 1, weak=weak)
            if rest is not None:
                return Block((Call(contract.name, bindings, result), *rest.calls), rest.end)
        return None

    def guarded(
        self,
        state: _State,
        contract: Contract,
        name: str,
        ref: Ref,
        depth: int,
        *,
        weak: bool,
    ) -> Block | None:
        arg = self.cells_of(_argument_name(contract.argument(name)))
        cells = state.cells(ref)
        inside, outside = state.narrowed(ref, cells & arg), state.narrowed(ref, cells - arg)
        # The narrowed value now fills `name`; another argument still overlapping is guarded
        # in turn, inside this branch.
        then = self.call(inside, contract, depth - 1, weak=weak)
        if then is None:
            return None
        otherwise = self.solve(outside, depth - 1, weak=weak)
        if otherwise is None and weak:
            stop = Stop(ref)
            self.stops[id(stop)] = outside.cells(ref)
            otherwise = Block((), stop)
        if otherwise is None:
            return None
        return Block((), Branch(ref, contract.name, name, then, otherwise))

    def record(
        self, state: _State, contract: Contract, bindings: tuple[tuple[str, Ref], ...]
    ) -> bool:
        if contract.plain:
            return True
        record = Validator({name: self.values(state.cells(ref)) for name, ref in bindings}).close()
        return record.relation_to(contract.accepts) == "subset"

    def after(
        self,
        state: _State,
        contract: Contract,
        bindings: tuple[tuple[str, Ref], ...],
        result: str,
    ) -> _State:
        results = tuple(
            (Ref(result, slot.path), self.cells_of(_result_name(slot))) for slot in contract.results
        )
        made = state.made | {(contract.name, bindings)}
        return _State(state.refs + results, made, state.fresh + 1)


def _predicates(contracts: Mapping[str, Contract], task: Task) -> list[Predicate]:
    entries = [
        (_argument_name(slot), slot.values, slot.schema, slot.root)
        for contract in contracts.values()
        for slot in contract.arguments
    ]
    entries += [
        (_result_name(slot), slot.values, slot.schema, slot.root)
        for contract in contracts.values()
        for slot in contract.results
    ]
    entries += [
        (_held_name(slot), slot.values, slot.schema, slot.root)
        for slots in task.held.values()
        for slot in slots
    ]
    entries.append((_GOAL, task.goal, task.goal_schema, task.goal_schema))
    return gather(entries)


def _reads(block: Block) -> set[str]:
    """Return every variable a block reads, in its calls, its ending and below."""
    read = {ref.variable for call in block.calls for _, ref in call.bindings}
    end = block.end
    read.add(end.ref.variable)
    if isinstance(end, Branch):
        read |= _reads(end.then) | _reads(end.otherwise)
    return read


def _pair_stops(first: Block, second: Block, stops: dict[int, frozenset[int]]) -> None:
    """Give each stop of ``first`` the uncovered cells of the stop beside it in ``second``."""
    if isinstance(first.end, Stop) and isinstance(second.end, Stop):
        held = stops.get(id(first.end), frozenset())
        stops[id(first.end)] = held | stops.get(id(second.end), frozenset())
    elif isinstance(first.end, Branch) and isinstance(second.end, Branch):
        _pair_stops(first.end.then, second.end.then, stops)
        _pair_stops(first.end.otherwise, second.end.otherwise, stops)


def _prune(block: Block, stops: dict[int, frozenset[int]]) -> Block:
    """Drop calls nothing reads, then collapse a branch whose two sides read the same.

    A call can act on the world, so one whose result no later step reads is not made. A
    branch whose sides are one program holds on the union of the two narrowed values, since
    each link held on each side; its test is dropped and its stops cover both sides.
    """
    end = block.end
    if isinstance(end, Branch):
        then, otherwise = _prune(end.then, stops), _prune(end.otherwise, stops)
        if render(then) == render(otherwise):
            _pair_stops(then, otherwise, stops)
            block = Block((*block.calls, *then.calls), then.end)
        else:
            block = Block(block.calls, Branch(end.ref, end.tool, end.argument, then, otherwise))
    calls = list(block.calls)
    for at in range(len(calls) - 1, -1, -1):
        later = Block(tuple(calls[at + 1 :]), block.end)
        if calls[at].result not in _reads(later):
            del calls[at]
    return Block(tuple(calls), block.end)


def _carry(old: Block, new: Block, stops: dict[int, frozenset[int]]) -> None:
    """Move each stop's uncovered cells to the stop beside it in a renumbered copy."""
    if isinstance(old.end, Stop) and isinstance(new.end, Stop):
        stops[id(new.end)] = stops.get(id(old.end), frozenset())
    elif isinstance(old.end, Branch) and isinstance(new.end, Branch):
        _carry(old.end.then, new.end.then, stops)
        _carry(old.end.otherwise, new.end.otherwise, stops)


def _numbered(block: Block, stops: dict[int, frozenset[int]]) -> Block:
    numbered = renumber(block)
    _carry(block, numbered, stops)
    return numbered


def _tidy(block: Block, stops: dict[int, frozenset[int]]) -> Block:
    """Prune and renumber until the program no longer changes."""
    block = _numbered(block, stops)
    while True:
        tidied = _numbered(_prune(block, stops), stops)
        if render(tidied) == render(block):
            return tidied
        block = tidied


def _uncovered(block: Block, search: _Search, where: str = "") -> list[Uncovered]:
    """Return every stop of a program with the values it leaves uncovered."""
    end = block.end
    if isinstance(end, Branch):
        then = f"{where}.then" if where else "then"
        otherwise = f"{where}.else" if where else "else"
        return _uncovered(end.then, search, then) + _uncovered(end.otherwise, search, otherwise)
    if not isinstance(end, Stop):
        return []
    cells = search.stops.get(id(end), frozenset())
    examples = tuple(v for i in sorted(cells) for v in search.abstraction.members(i))
    return [Uncovered(where, str(end.ref), examples, search.abstraction.undecided(cells))]


def plan(
    contracts: Mapping[str, Contract],
    task: Task,
    *,
    depth: int = DEPTH,
    seed: int = 0,
    weak: bool = True,
) -> Plan:
    """Search a program for ``task`` over ``contracts``: strong first, then weak if asked."""
    abstraction = abstract(_predicates(contracts, task), seed=seed)
    search = _Search(contracts, abstraction, abstraction.cells_of(abstraction.index(_GOAL)))
    start = _State(
        tuple(
            (Ref(var, slot.path), search.cells_of(_held_name(slot)))
            for var, slots in task.held.items()
            for slot in slots
        ),
        frozenset(),
        1,
    )
    for bound in range(1, depth + 1):
        program = search.solve(start, bound, weak=False)
        if program is not None:
            program = _tidy(program, search.stops)
            return Plan(
                program,
                strong=True,
                uncovered=(),
                abstraction=abstraction,
                expanded=search.expanded,
            )
    if weak:
        # One search at the full bound: a branch stops only where no continuation exists
        # within the depth its path has left, not where a shallower bound ran out.
        search.failed.clear()
        program = search.solve(start, depth, weak=True)
        if program is not None:
            program = _tidy(program, search.stops)
            gaps = tuple(_uncovered(program, search))
            return Plan(
                program,
                strong=False,
                uncovered=gaps,
                abstraction=abstraction,
                expanded=search.expanded,
            )
    return Plan(None, strong=False, uncovered=(), abstraction=abstraction, expanded=search.expanded)
