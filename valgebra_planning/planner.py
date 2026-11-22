"""Programs of calls and type tests, searched over the cells.

A state maps every reference the agent holds to the cells of the set it lies in. A call is
enabled when each required argument can be bound to a reference whose cells all lie inside
the argument's cells, and, where the input schema relates its arguments, valgebra proves the
whole record inside it. A guarded call handles a reference that only overlaps an argument:
it branches on the reference lying in the argument, calls the tool on the narrowed value,
and goes on with the rest in the other branch. A program is strong when every path returns
a reference whose cells lie inside the goal's: strong planning under nondeterministic
outcomes (Cimatti et al. 2003, Def. 2.10), the outcomes partitioned by the cells rather
than listed, and a branch on a runtime type test as an observation (Bertoli et al. 2006,
Def. 2.8).

The search deepens on the calls and tests along a path, so the first strong program found is a
shallowest one: a state that fails is remembered by what every rule reads of it, so a failure
through one origin is never taken for a state another reaches. Where none exists within the
bound, one search at the full bound lets a branch stop once its sibling returns and no
continuation exists within the depth its path has left, and each stop names the cells it leaves
uncovered. Where a policy names a tool, a call to it that nothing reads is left out wherever
`valgebra_planning.verify.verify`, which reads neither the cells nor the search, reads the
program the same without it; the command line verifies every program it prints.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from valgebra_planning._valgebra import Validator, intersection, nothing, union
from valgebra_planning.cells import Abstraction, Predicate, abstract, gather
from valgebra_planning.contracts import Contract, Slot, Task, record_of, unclashed
from valgebra_planning.flows import Flow
from valgebra_planning.mining import SlotKey, Types
from valgebra_planning.policy import Policy
from valgebra_planning.program import (
    Block,
    Branch,
    Call,
    For,
    Next,
    Ref,
    Return,
    Stop,
    render,
    renumber,
)
from valgebra_planning.run import retry_assumptions
from valgebra_planning.verify import Verification, verify

__all__ = ["DEPTH", "Plan", "Uncovered", "plan", "predicates"]

#: The calls and tests a path may take.
DEPTH = 6

#: How many bindings of one call are tried; a result's cells do not depend on the binding,
#: so one that checks stands for the rest.
MAX_BINDINGS = 16

_GOAL = "goal"
#: The expansions spent trying the other first calls once a weak program is found, so the weak
#: program leaving the fewest cells uncovered among them is the one returned.
RANKING = 2_000

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
    """A program for a task, whether it is strong, and what its stops leave uncovered.

    ``assumed`` names each binding that rests on a mined semantic type or on an output schema
    inferred from observed calls, and each retry, which rests on its tool's errors being
    transient and, unless its server is trusted, on its annotations.
    """

    program: Block | None
    strong: bool
    uncovered: tuple[Uncovered, ...]
    abstraction: Abstraction
    expanded: int
    assumed: tuple[str, ...] = ()
    #: Why no program was found, where none was: whether the depth bound cut a path, so a
    #: deeper search may find one, or none reached it.
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _State:
    refs: tuple[tuple[Ref, frozenset[int]], ...]
    made: frozenset[tuple[str, tuple[tuple[str, Ref], ...]]]
    fresh: int
    #: The states each policy may be in, one set per policy.
    policed: tuple[frozenset[str], ...] = ()
    #: Where each variable came from: a task input's name, or the tool whose result it is.
    origins: tuple[tuple[str, str], ...] = ()
    #: The sources of every value tested on the path so far.
    tested: frozenset[str] = frozenset()

    def cells(self, ref: Ref) -> frozenset[int]:
        return dict(self.refs)[ref]

    def origin(self, ref: Ref) -> str:
        return dict(self.origins).get(ref.variable, ref.variable)

    def but(self, **changes: Any) -> _State:
        return replace(self, **changes)

    def narrowed(self, ref: Ref, cells: frozenset[int]) -> _State:
        refs = tuple((r, cells if r == ref else held) for r, held in self.refs)
        return self.but(refs=refs, tested=self.tested | {self.origin(ref)})

    def signature(self) -> object:
        return frozenset(cells for _, cells in self.refs), self.policed, self.tested


@dataclass(slots=True)
class _Search:
    contracts: Mapping[str, Contract]
    abstraction: Abstraction
    goal: frozenset[int]
    errors: bool = False
    policies: tuple[Policy, ...] = ()
    flows: tuple[Flow, ...] = ()
    types: Types | None = None
    failed: set[tuple[object, object, int, bool]] = field(default_factory=set)
    expanded: int = 0
    #: How many paths the depth bound cut in the search last run, short of the goal.
    cut: int = 0
    #: The cells each stop leaves uncovered, by the stop's identity: a stop is made once
    #: and read back only from the program returned, which keeps it alive.
    stops: dict[int, frozenset[int]] = field(default_factory=dict)

    def cells_of(self, name: str) -> frozenset[int]:
        return self.abstraction.cells_of(self.abstraction.index(name))

    def holds(self, name: str) -> bool:
        """Return whether a named set is one the cells were cut by."""
        try:
            self.abstraction.index(name)
        except KeyError:
            return False
        return True

    def values(self, cells: frozenset[int]) -> Validator[Any]:
        return union(*(self.abstraction.cells[i].values for i in sorted(cells)))

    def settled(self, state: _State) -> bool:
        """Return whether a path may end here: no policy holds an obligation open."""
        return all(
            policy.reach(held, ended=True) == "never"
            for policy, held in zip(self.policies, state.policed, strict=True)
        )

    def settling(self, state: _State) -> list[Contract]:
        """Return the tools, first those a transition out of a held pending state reads."""
        leaving = [
            transition
            for policy, held in zip(self.policies, state.policed, strict=True)
            for transition in policy.transitions
            if transition.source in held & policy.pending
        ]
        return sorted(
            self.contracts.values(),
            key=lambda contract: not any(t.reads(contract.name, contract.hints) for t in leaving),
        )

    def read(self, state: _State) -> object:
        """Return what the rules read of each held value beside its cells, for the failure memo.

        A flow rule admits a binding by the value's origin and a mined type by its origin and
        path, so two states holding the same cells through origins a rule tells apart are two
        states: a failure of one is no failure of the other. A search with neither reads nothing
        more, and its key is the signature alone.
        """
        if not self.flows and self.types is None:
            return None
        return frozenset(
            (
                cells,
                tuple(flow.holds(state.origin(ref)) for flow in self.flows),
                None
                if self.types is None
                else self.types.of.get(_result_key(state.origin(ref), ref)),
            )
            for ref, cells in state.refs
        )

    def solve(self, state: _State, depth: int, *, weak: bool) -> Block | None:
        goal = next((ref for ref, cells in state.refs if cells <= self.goal), None)
        if goal is not None and (not self.policies or self.settled(state)):
            return Block((), Return(goal))
        key = (state.signature(), self.read(state), depth, weak)
        if key in self.failed:
            return None
        if depth == 0:
            self.cut += 1
            return None
        self.expanded += 1
        # Past the goal a path goes on only to settle an obligation, so the calls that may
        # settle one are tried first, and no call another branch needed pads this one.
        for contract in self.contracts.values() if goal is None else self.settling(state):
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
            refs = [(r, c) for r, c in state.refs if self.agree(state, r, slot)]
            full[slot.path[0]] = [r for r, c in refs if c and c <= arg]
            partial[slot.path[0]] = [r for r, c in refs if c & arg and not c <= arg]
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
            policed = self.policed(state, contract, bindings)
            if policed is None or not self.flowing(state, contract, bindings):
                continue
            # A tool that answered with an error may still have acted, so the block run then
            # starts from either set of policy states.
            either = tuple(a | b for a, b in zip(state.policed, policed, strict=True))
            result = f"c{state.fresh}"
            failing = state.but(policed=either)
            call = self.failure(failing, contract, bindings, result, depth, weak=weak)
            if call is None:
                continue
            stepped = state.but(policed=policed)
            after = self.after(stepped, contract, bindings, result)
            rest = self.solve(after, depth - 1, weak=weak)
            if rest is not None:
                return Block((call, *rest.calls), rest.end)
        return None

    def failure(
        self,
        state: _State,
        contract: Contract,
        bindings: tuple[tuple[str, Ref], ...],
        result: str,
        depth: int,
        *,
        weak: bool,
    ) -> Call | None:
        """Return the call with what it does on an error, or None where nothing covers one.

        A tool that is read-only or idempotent is retried. Any other gets a block searched
        from the state before the call, the call itself excluded, and a weak program may stop
        there on the value the call was given.
        """
        if not self.errors:
            return Call(contract.name, bindings, result)
        if contract.idempotent:
            return Call(contract.name, bindings, result, retry=True)
        without = state.but(made=state.made | {(contract.name, bindings)})
        fallback = self.solve(without, depth - 1, weak=weak)
        if fallback is None and weak and bindings and self.settled(state):
            stop = Stop(bindings[0][1])
            self.stops[id(stop)] = state.cells(bindings[0][1])
            fallback = Block((), stop)
        if fallback is None:
            return None
        return Call(contract.name, bindings, result, on_error=fallback)

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
        if otherwise is None and weak and self.settled(outside):
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
        record = record_of(
            {name: self.values(state.cells(ref)) for name, ref in bindings}, closed=True
        )
        return record.relation_to(contract.accepts) == "subset"

    def policed(
        self, state: _State, contract: Contract, bindings: tuple[tuple[str, Ref], ...]
    ) -> tuple[frozenset[str], ...] | None:
        """Return each policy's states after the call, or None if one may turn bad."""
        if not self.policies:
            return state.policed
        record = record_of(
            {name: self.values(state.cells(ref)) for name, ref in bindings}, closed=True
        )
        after = tuple(
            policy.step(held, contract.name, record, contract.hints)
            for policy, held in zip(self.policies, state.policed, strict=True)
        )
        if any(
            policy.reach(held) != "never" for policy, held in zip(self.policies, after, strict=True)
        ):
            return None
        return after

    def after(
        self,
        state: _State,
        contract: Contract,
        bindings: tuple[tuple[str, Ref], ...],
        result: str,
    ) -> _State:
        results = tuple(
            (Ref(result, slot.path), self.cells_of(_result_name(slot)))
            for slot in contract.results
            if self.holds(_result_name(slot))
        )
        made = state.made | {(contract.name, bindings)}
        return state.but(
            refs=state.refs + results,
            made=made,
            fresh=state.fresh + 1,
            origins=(*state.origins, (result, contract.name)),
        )

    def agree(self, state: _State, ref: Ref, slot: Slot) -> bool:
        """Return whether the mined types let ``ref`` fill ``slot``; always, with none mined."""
        if self.types is None:
            return True
        origins = dict(state.origins)
        source = (
            _result_key(origins[ref.variable], ref) if ref.variable in origins else _input_key(ref)
        )
        return self.types.agree(source, ("argument", slot.name))

    def flowing(
        self, state: _State, contract: Contract, bindings: tuple[tuple[str, Ref], ...]
    ) -> bool:
        """Return whether every flow rule lets this call's arguments reach it."""
        for flow in self.flows:
            if flow.tool != contract.name:
                continue
            if flow.implicit and any(map(flow.holds, state.tested)):
                return False
            for name, ref in bindings:
                if name != flow.argument or not flow.holds(state.origin(ref)):
                    continue
                if flow.endorsed is None:
                    return False
                if self.values(state.cells(ref)).relation_to(flow.endorsed) != "subset":
                    return False
        return True


def _meets(one: Validator[Any], other: Validator[Any]) -> bool:
    """Return whether two sets may share a value: not proven disjoint."""
    return intersection(one, other).relation_to(nothing) != "subset"


def _reachable(
    contracts: Mapping[str, Contract], task: Task, kept: frozenset[str]
) -> dict[str, Contract]:
    """Return the tools a program for ``task`` may call to some end, and those in ``kept``.

    Forward, a tool can be called once every required argument meets a value the agent may
    hold: the task's inputs, then the results of each tool found callable. Backward, a tool
    matters once one of its results meets the goal or an argument of a tool that matters.
    A tool failing either is never part of a program, so its slots are never cut into cells:
    the graph-reachability pruning of component synthesis, which SyPet takes backward from
    the goal alone (Feng et al. 2017, §6.3), with valgebra deciding each overlap.
    """
    held = [slot.values for slots in task.held.values() for slot in slots]
    callable_: dict[str, Contract] = {}
    grown = True
    while grown:
        grown = False
        for name, contract in contracts.items():
            if name in callable_:
                continue
            required = [slot for slot in contract.arguments if slot.required]
            if all(any(_meets(slot.values, value) for value in held) for slot in required):
                callable_[name] = contract
                held += [slot.values for slot in contract.results]
                grown = True
    needed = [task.goal]
    mattering: dict[str, Contract] = {}
    grown = True
    while grown:
        grown = False
        for name, contract in callable_.items():
            if name in mattering:
                continue
            if any(_meets(slot.values, want) for slot in contract.results for want in needed):
                mattering[name] = contract
                needed += [slot.values for slot in contract.arguments]
                grown = True
    return {
        name: contract
        for name, contract in contracts.items()
        if name in mattering or (name in kept and name in callable_)
    }


def _result_key(origin: str, ref: Ref) -> SlotKey:
    """Return the slot a reference reads, as the miner names it."""
    return ("result", ".".join((origin, *ref.path)))


def _input_key(ref: Ref) -> SlotKey:
    """Return the slot a reference to a task input reads, as the miner names it."""
    return ("input", ".".join((ref.variable, *ref.path)))


def _assumed(
    block: Block,
    types: Types | None,
    contracts: Mapping[str, Contract],
    made: Mapping[str, str] | None = None,
) -> list[str]:
    """Return every binding and retry resting on an assumption, as `Plan.assumed` names them."""
    made = dict(made or {})
    found = []
    for call in block.calls:
        for name, ref in call.bindings:
            if isinstance(ref, Ref):
                found += _resting(
                    ref, f"{call.tool}.{name}", made.get(ref.variable), types, contracts
                )
        if call.retry:
            found += [f"retry {call.tool}: {a}" for a in retry_assumptions(contracts[call.tool])]
        if call.on_error is not None:
            found += _assumed(call.on_error, types, contracts, made)
        made[call.result] = call.tool
    end = block.end
    if isinstance(end, Branch):
        found += _assumed(end.then, types, contracts, made)
        found += _assumed(end.otherwise, types, contracts, made)
    return found


def _resting(
    ref: Ref,
    argument: str,
    source: str | None,
    types: Types | None,
    contracts: Mapping[str, Contract],
) -> list[str]:
    """Return what a binding of ``ref`` into ``argument`` rests on: a mined type, an output seen.

    ``source`` is the tool whose result ``ref`` reads, None for a task input.
    """
    key = _input_key(ref) if source is None else _result_key(source, ref)
    found = []
    if types is not None and types.typed(key, ("argument", argument)):
        found.append(f"{ref} -> {argument}")
    if source is not None and contracts[source].inferred:
        found.append(f"{ref} -> {argument}, from the output of {source} as observed")
    return found


def _readable(values: Validator[Any], consumers: Sequence[Validator[Any]]) -> bool:
    """Return whether some argument or the goal may hold a value of ``values``."""
    return any(
        intersection(values, consumer).relation_to(nothing) != "subset" for consumer in consumers
    )


def predicates(contracts: Mapping[str, Contract], task: Task) -> list[Predicate]:
    """Return every argument and the goal, and each result or input some of them may read.

    A value no argument and no goal can hold is never passed on, so its cells cost the
    search and decide nothing; it is left out, with its reference. The sets are gathered in
    the order of their names, which fixes the cut: valgebra leaves some meets undecided, and
    which ones depends on the order the cut splits by and draws its members in, so a listing
    order of its own would make a library's count that order's.
    """
    arguments = [slot for contract in contracts.values() for slot in contract.arguments]
    consumers = [slot.values for slot in arguments] + [task.goal]
    entries = [(_argument_name(slot), slot.values, slot.schema, slot.root) for slot in arguments]
    entries += [
        (_result_name(slot), slot.values, slot.schema, slot.root)
        for contract in contracts.values()
        for slot in contract.results
        if _readable(slot.values, consumers)
    ]
    entries += [
        (_held_name(slot), slot.values, slot.schema, slot.root)
        for slots in task.held.values()
        for slot in slots
        if _readable(slot.values, consumers)
    ]
    entries.append((_GOAL, task.goal, task.goal_schema, task.goal_schema))
    return gather(sorted(entries, key=lambda entry: entry[0]))


def _reads(block: Block) -> set[str]:
    """Return every variable a block reads, in its calls, its ending and below."""
    read = {
        ref.variable for call in block.calls for _, ref in call.bindings if isinstance(ref, Ref)
    }
    for call in block.calls:
        if call.on_error is not None:
            read |= _reads(call.on_error)
    end = block.end
    if not isinstance(end, Next):
        read.add(end.ref.variable)
    if isinstance(end, Branch):
        read |= _reads(end.then) | _reads(end.otherwise)
    elif isinstance(end, For):
        read |= _reads(end.body) | _reads(end.after)
    return read


def _pair_stops(first: Block, second: Block, stops: dict[int, frozenset[int]]) -> None:
    """Give each stop of ``first`` the uncovered cells of the stop beside it in ``second``."""
    for one, other in zip(first.calls, second.calls, strict=False):
        if one.on_error is not None and other.on_error is not None:
            _pair_stops(one.on_error, other.on_error, stops)
    if isinstance(first.end, Stop) and isinstance(second.end, Stop):
        held = stops.get(id(first.end), frozenset())
        stops[id(first.end)] = held | stops.get(id(second.end), frozenset())
    elif isinstance(first.end, Branch) and isinstance(second.end, Branch):
        _pair_stops(first.end.then, second.end.then, stops)
        _pair_stops(first.end.otherwise, second.end.otherwise, stops)


def _prune(block: Block, stops: dict[int, frozenset[int]], kept: frozenset[str]) -> Block:
    """Drop calls nothing reads, then collapse a branch whose two sides read the same.

    A call can act on the world, so one whose result no later step reads is not made. A
    branch whose sides are one program holds on the union of the two narrowed values, since
    each link held on each side; its test is dropped and its stops cover both sides.
    """
    end = block.end
    if isinstance(end, Branch):
        then, otherwise = _prune(end.then, stops, kept), _prune(end.otherwise, stops, kept)
        if render(then) == render(otherwise):
            _pair_stops(then, otherwise, stops)
            block = Block((*block.calls, *then.calls), then.end)
        else:
            branch = Branch(end.ref, end.tool, end.argument, then, otherwise, end.values, end.key)
            block = Block(block.calls, branch)
    calls = [
        call
        if call.on_error is None
        else Call(
            call.tool, call.bindings, call.result, call.retry, _prune(call.on_error, stops, kept)
        )
        for call in block.calls
    ]
    for at in range(len(calls) - 1, -1, -1):
        later = Block(tuple(calls[at + 1 :]), block.end)
        if calls[at].tool not in kept and calls[at].result not in _reads(later):
            del calls[at]
    return Block(tuple(calls), block.end)


#: Where a call stands in a program: each step a block index, a branch side, an error block or a
#: loop's body or what follows it.
_Address = tuple[tuple[str, int], ...]


def _unread(block: Block, at: _Address = ()) -> list[_Address]:
    """Return where each call whose result nothing after it reads stands, below ``block``."""
    found: list[_Address] = []
    for index, call in enumerate(block.calls):
        if call.result not in _reads(Block(block.calls[index + 1 :], block.end)):
            found.append((*at, ("call", index)))
        if call.on_error is not None:
            found += _unread(call.on_error, (*at, ("error", index)))
    end = block.end
    if isinstance(end, Branch):
        found += _unread(end.then, (*at, ("then", 0))) + _unread(end.otherwise, (*at, ("else", 0)))
    elif isinstance(end, For):
        found += _unread(end.body, (*at, ("body", 0))) + _unread(end.after, (*at, ("after", 0)))
    return found


def _without(block: Block, at: _Address) -> Block:
    """Return ``block`` with the call ``at`` names left out; every other part is kept as it is."""
    (kind, index), *rest = at
    calls, end = list(block.calls), block.end
    if kind == "call":
        del calls[index]
    elif kind == "error" and (call := calls[index]).on_error is not None:
        kept = _without(call.on_error, tuple(rest))
        calls[index] = Call(call.tool, call.bindings, call.result, call.retry, kept)
    elif isinstance(end, Branch):
        then = _without(end.then, tuple(rest)) if kind == "then" else end.then
        otherwise = _without(end.otherwise, tuple(rest)) if kind == "else" else end.otherwise
        end = Branch(end.ref, end.tool, end.argument, then, otherwise, end.values, end.key)
    elif isinstance(end, For):
        body = _without(end.body, tuple(rest)) if kind == "body" else end.body
        after = _without(end.after, tuple(rest)) if kind == "after" else end.after
        end = For(end.ref, end.item, body, after)
    return Block(tuple(calls), end)


def _reading(checked: Verification) -> object:
    """Return what a verification says of a program: its links not proven, and where it stops."""
    unproven = sorted(
        (link.claim, link.verdict) for link in checked.links if link.verdict != "proven"
    )
    return unproven, sorted(gap.ref for gap in checked.gaps)


def _unpadded(program: Block, reading: Callable[[Block], object]) -> Block:
    """Leave out each call nothing reads wherever the program reads the same without it.

    A call a policy names is kept by the search even where nothing reads its result, since it
    may move the policy, and the search holds no policy state to tell. The verifier does: a
    call the program needs leaves a policy's link refuted, undecided or pending without it, and
    one it does not need leaves every link and every stop as they were.
    """
    held = reading(program)
    while True:
        for at in _unread(program):
            smaller = _without(program, at)
            if reading(smaller) == held:
                program = smaller
                break
        else:
            return program


def _carry(old: Block, new: Block, stops: dict[int, frozenset[int]]) -> None:
    """Move each stop's uncovered cells to the stop beside it in a renumbered copy."""
    for one, other in zip(old.calls, new.calls, strict=True):
        if one.on_error is not None and other.on_error is not None:
            _carry(one.on_error, other.on_error, stops)
    if isinstance(old.end, Stop) and isinstance(new.end, Stop):
        stops[id(new.end)] = stops.get(id(old.end), frozenset())
    elif isinstance(old.end, Branch) and isinstance(new.end, Branch):
        _carry(old.end.then, new.end.then, stops)
        _carry(old.end.otherwise, new.end.otherwise, stops)


def _numbered(block: Block, stops: dict[int, frozenset[int]]) -> Block:
    numbered = renumber(block)
    _carry(block, numbered, stops)
    return numbered


def _tidy(block: Block, stops: dict[int, frozenset[int]], kept: frozenset[str]) -> Block:
    """Prune and renumber until the program no longer changes."""
    block = _numbered(block, stops)
    while True:
        tidied = _numbered(_prune(block, stops, kept), stops)
        if render(tidied) == render(block):
            return tidied
        block = tidied


def _stopped(block: Block, stops: dict[int, frozenset[int]]) -> int:
    """Return how many cells the stops of ``block`` leave uncovered, its error blocks included."""
    count = sum(_stopped(call.on_error, stops) for call in block.calls if call.on_error)
    end = block.end
    if isinstance(end, Stop):
        count += len(stops.get(id(end), frozenset()))
    elif isinstance(end, Branch):
        count += _stopped(end.then, stops) + _stopped(end.otherwise, stops)
    return count


def _ranked(
    search: _Search, start: _State, depth: int, tidied: Callable[[Block], Block]
) -> Block | None:
    """Return the weak program leaving the fewest cells uncovered among the first calls tried.

    The search returns the first weak program it finds, which the tools' order decides, so each
    tool the start may call first is tried for its own; once one is found, the others are tried
    until `RANKING` more expansions are spent. A weak plan for a goal is one some execution
    reaches it on (Cimatti et al. 2003, Def. 2.10), and of two such, the one stopping on fewer
    cells covers more of what the inputs may hold. A tie goes to the earlier tool by name.
    """
    goal = next((ref for ref, cells in start.refs if cells <= search.goal), None)
    if goal is not None:
        found = search.solve(start, depth, weak=True)
        return None if found is None else tidied(found)
    search.expanded += 1
    ranked: list[tuple[int, int, Block]] = []
    budget: int | None = None
    for order, contract in enumerate(search.contracts.values()):
        if budget is not None and search.expanded >= budget:
            break
        found = search.call(start, contract, depth, weak=True)
        if found is None:
            continue
        program = tidied(found)
        ranked.append((_stopped(program, search.stops), order, program))
        if budget is None:
            budget = search.expanded + RANKING
    return min(ranked, key=lambda row: row[:2])[2] if ranked else None


def _uncovered(block: Block, search: _Search, where: str = "") -> list[Uncovered]:
    """Return every stop of a program with the values it leaves uncovered."""
    found = [
        gap
        for call in block.calls
        if call.on_error is not None
        for gap in _uncovered(
            call.on_error,
            search,
            f"{where}.error:{call.tool}" if where else f"error:{call.tool}",
        )
    ]
    return found + _ending_uncovered(block, search, where)


def _ending_uncovered(block: Block, search: _Search, where: str) -> list[Uncovered]:
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
    errors: bool = False,
    policies: Sequence[Policy] = (),
    flows: Sequence[Flow] = (),
    types: Types | None = None,
) -> Plan:
    """Search a program for ``task`` over ``contracts``: strong first, then weak if asked.

    With ``errors``, any tool may answer with an error, and every call is retried or given
    a block for it, as `valgebra_planning.verify.verify` holds with the same flag. A call is
    made only where valgebra proves it keeps every policy away from its bad states, and a
    call a policy needs is kept even where no result of it is read, a call it names and does
    not need is left out; a path ends, in a return or a stop, only where no policy holds a
    pending state. A value reaches a flow
    rule's sink only as `valgebra_planning.verify.verify` holds it with the same rules. With
    mined ``types``, a value fills an argument only where their types agree, and the plan
    names every binding that rests on one. Raises `ValueError` for a task naming an input as
    a tool.
    """
    unclashed(task, contracts)
    # The tools are tried in their names' order, so a server listing them in another order
    # gets the same program.
    contracts = dict(sorted(contracts.items()))
    kept = frozenset(tool for policy in policies for tool in policy.tools()) | frozenset(
        name
        for name, contract in contracts.items()
        if any(policy.hints() & contract.hints for policy in policies)
    )
    contracts = _reachable(contracts, task, kept)
    abstraction = abstract(predicates(contracts, task), seed=seed)
    goal = abstraction.cells_of(abstraction.index(_GOAL))
    search = _Search(
        contracts,
        abstraction,
        goal,
        errors=errors,
        policies=tuple(policies),
        flows=tuple(flows),
        types=types,
    )
    start = _State(
        tuple(
            (Ref(var, slot.path), search.cells_of(_held_name(slot)))
            for var, slots in task.held.items()
            for slot in slots
            if search.holds(_held_name(slot))
        ),
        frozenset(),
        1,
        tuple(frozenset({policy.initial}) for policy in policies),
    )

    def reading(program: Block) -> object:
        checked = verify(program, contracts, task, errors=errors, policies=policies, flows=flows)
        return _reading(checked)

    def tidied(program: Block) -> Block:
        program = _tidy(program, search.stops, kept)
        return _tidy(_unpadded(program, reading), search.stops, kept) if kept else program

    for bound in range(1, depth + 1):
        search.cut = 0
        program = search.solve(start, bound, weak=False)
        if program is not None:
            program = tidied(program)
            return Plan(
                program,
                strong=True,
                uncovered=(),
                abstraction=abstraction,
                expanded=search.expanded,
                assumed=tuple(_assumed(program, types, contracts)),
            )
    if weak:
        # One search at the full bound: a branch stops only where no continuation exists
        # within the depth its path has left, not where a shallower bound ran out.
        search.failed.clear()
        search.cut = 0
        program = _ranked(search, start, depth, tidied)
        if program is not None:
            gaps = tuple(_uncovered(program, search))
            return Plan(
                program,
                strong=False,
                uncovered=gaps,
                abstraction=abstraction,
                expanded=search.expanded,
                assumed=tuple(_assumed(program, types, contracts)),
            )
    if search.cut:
        reason = (
            f"no program within the bound of {depth} calls and tests, which cut {search.cut} "
            "paths: a deeper search may find one"
        )
    else:
        reason = (
            f"no program: no path reached the bound of {depth}, so no deeper search finds one "
            f"from the first {MAX_BINDINGS} bindings of each call the search tries"
        )
    return Plan(
        None,
        strong=False,
        uncovered=(),
        abstraction=abstraction,
        expanded=search.expanded,
        reason=reason,
    )
