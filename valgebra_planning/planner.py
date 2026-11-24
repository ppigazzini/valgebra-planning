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

from valgebra_planning._valgebra import (
    ANNOTATIONS,
    Validator,
    anything,
    complement,
    intersection,
    nothing,
    union,
)
from valgebra_planning.cells import Abstraction, Predicate, abstract, gather
from valgebra_planning.contracts import (
    Contract,
    Postcondition,
    Slot,
    Task,
    field_slot,
    fixes,
    listed,
    record_of,
)
from valgebra_planning.flows import Flow
from valgebra_planning.mining import SlotKey, Types
from valgebra_planning.policy import Policy
from valgebra_planning.program import (
    Block,
    Branch,
    Call,
    Const,
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
#: What the reference a task's constant is held under starts with, which no program names.
_CONSTANT = "constant:"


@dataclass(frozen=True, slots=True)
class _Guard:
    """A policy's guard on a tool's result that the planner can test: a field and its members.

    ``whole`` is the guard over the whole result, which a policy reads; ``path`` and ``values``
    are the one field it reads and the members it lists, which a test writes.
    """

    path: tuple[str, ...]
    values: tuple[Any, ...]
    whole: Validator[Any]


def _listing(schema: object) -> tuple[tuple[str, ...], tuple[Any, ...]] | None:
    """Return the one field a guard's schema reads and the members it lists, or None.

    A guard of the form ``{"properties": {"confirmed": {"const": true}}}``, a property at a time
    down to a `const` or an `enum`, is a test a program writes: ``if c3.confirmed in (True,):``.
    An annotation constrains no instance (JSON Schema 2020-12 Validation, §9), so a
    ``description`` or a ``title`` beside the members hides none of them.
    """
    path: list[str] = []
    while isinstance(schema, dict):
        keys = set(schema) - {"type", "required"} - ANNOTATIONS
        properties = schema.get("properties")
        if keys == {"properties"} and isinstance(properties, dict) and len(properties) == 1:
            ((name, schema),) = properties.items()
            path.append(name)
        elif keys == {"const"}:
            return tuple(path), (schema["const"],)
        elif keys == {"enum"} and isinstance(schema["enum"], list):
            return tuple(path), tuple(schema["enum"])
        else:
            return None
    return None


def _members(schema: object) -> tuple[Any, ...] | None:
    """Return the members a schema lists, an ``enum`` or a ``const`` beside a ``type``, or None."""
    listing = _listing(schema)
    return None if listing is None or listing[0] else listing[1]


def _guards(policies: Sequence[Policy]) -> dict[str, tuple[_Guard, ...]]:
    """Return, by tool, each guard a policy's transition reads off its result and a test can."""
    found: dict[str, list[_Guard]] = {}
    for policy in policies:
        for transition in policy.transitions:
            listing = _listing(transition.result_schema)
            if transition.result is None or not transition.tool or listing is None:
                continue
            path, values = listing
            found.setdefault(transition.tool, []).append(_Guard(path, values, transition.result))
    return {tool: tuple(guards) for tool, guards in found.items()}


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
    #: Why no program was found, where none was: whether the depth bound cut a path or none
    #: reached it, and each argument only a task input fills, under a test.
    reason: str | None = None
    #: For a task naming goals in an order, where each return the verifier proves inside the
    #: first lies: the rest return in a goal the task accepts after it.
    preferred: tuple[str, ...] = ()


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
    #: The variables held before the last call on the path that may act.
    stale: frozenset[str] = frozenset()
    #: The arguments each call was given, by the variable its result is bound to.
    given: tuple[tuple[str, tuple[tuple[str, Ref], ...]], ...] = ()
    #: The calls on the path that answered with an error and may have acted: each tool, and the
    #: cells each argument it was given held.
    failed: tuple[tuple[str, tuple[tuple[str, frozenset[int]], ...]], ...] = ()
    #: For each fresh rule naming the calls that withdraw it, the variables held before the last
    #: of those on the path, by the rule's name.
    withdrawn: tuple[tuple[str, frozenset[str]], ...] = ()
    #: The inputs the task requires a program to read, and those of them a call or a test on
    #: the path read.
    reading: frozenset[str] = frozenset()
    read: frozenset[str] = frozenset()

    def cells(self, ref: Ref) -> frozenset[int]:
        return dict(self.refs)[ref]

    def origin(self, ref: Ref) -> str:
        return dict(self.origins).get(ref.variable, ref.variable)

    def but(self, **changes: Any) -> _State:
        return replace(self, **changes)

    def narrowed(self, ref: Ref, cells: frozenset[int]) -> _State:
        refs = tuple((r, cells if r == ref else held) for r, held in self.refs)
        read = self.read | ({ref.variable} & self.reading)
        return self.but(refs=refs, tested=self.tested | {self.origin(ref)}, read=read)

    def signature(self) -> object:
        old = frozenset(cells for ref, cells in self.refs if ref.variable in self.stale)
        listed = tuple(
            frozenset(cells for ref, cells in self.refs if ref.variable in names)
            for _, names in self.withdrawn
        )
        held = frozenset(cells for _, cells in self.refs)
        fed = frozenset(cells for ref, cells in self.refs if self.fed(ref.variable))
        return held, self.policed, self.tested, old, listed, self.read, fed

    def fed(self, variable: str) -> bool:
        """Return whether the value ``variable`` holds was read from every input required.

        A call's result was read from every input its arguments were, through the variables
        they read; an input from itself.
        """
        if not self.reading:
            return True
        given = dict(self.given)
        seen, found, pending = set(), set(), [variable]
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            if name in given:
                pending += [ref.variable for _, ref in given[name]]
            else:
                found.add(name)
        return self.reading <= found


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
    #: The search toward the goal the task accepts after this one's, which a weak program's
    #: stop is filled from where it finds a program.
    fallback: _Search | None = None
    #: The reference each constant the task fixes is held under, by its tool and argument, and
    #: the value each such reference holds: only its argument takes it (`call`).
    fixed: dict[tuple[str, str], Ref] = field(default_factory=dict)
    literals: dict[Ref, Any] = field(default_factory=dict)
    #: The guards a policy reads off each tool's result that a test can split on (`split`).
    guards: Mapping[str, tuple[_Guard, ...]] = field(default_factory=dict)
    #: The arguments the task reads fresh, as ``(tool, argument)``: no value held before the
    #: last call that may act fills one.
    fresh: frozenset[tuple[str, str]] = frozenset()
    #: The library's postconditions: an acted one gets the block verifying before a retry.
    postconditions: tuple[Postcondition, ...] = ()
    #: The goal's set, which a member a result field lists is read against (`observed`).
    goal_values: Validator[Any] = anything

    def bound(self, state: _State, ref: Ref) -> Validator[Any]:
        """Return the values a binding passes: a constant's own, or its reference's cells'."""
        if ref in self.literals:
            return listed([self.literals[ref]])
        return self.values(state.cells(ref))

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

        A flow rule admits a binding by the value's origin, and with a ``key`` by the argument
        its source call took there, and a mined type by its origin and path, so two states holding
        the same cells through origins a rule tells apart are two states: a failure of one is no
        failure of the other. A search with neither reads nothing more, and its key is the
        signature alone.
        """
        if not self.flows and self.types is None:
            return None
        given = dict(state.given)
        return frozenset(
            (
                cells,
                tuple(flow.holds(state.origin(ref)) for flow in self.flows),
                None
                if self.types is None
                else self.types.of.get(_result_key(state.origin(ref), ref)),
                tuple(
                    dict(given.get(ref.variable, ())).get(flow.key)
                    for flow in self.flows
                    if flow.key
                ),
            )
            for ref, cells in state.refs
        )

    def solve(self, state: _State, depth: int, *, weak: bool) -> Block | None:
        # A value meeting the goal is returned only where it was read from each input required.
        goal = next(
            (
                r
                for r, cells in state.refs
                if cells <= self.goal and r not in self.literals and state.fed(r.variable)
            ),
            None,
        )
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
        # An argument the task fixes is written whether the tool requires it or not: left out,
        # the tool reads its own default, which no task fixed.
        unread = state.reading - state.read
        reading = self.reading(state, contract, unread)
        required = [
            slot
            for slot in contract.arguments
            if slot.required
            or (contract.name, slot.path[0]) in self.fixed
            or slot.path[0] in reading
        ]
        full: dict[str, list[Ref]] = {}
        partial: dict[str, list[Ref]] = {}
        for slot in required:
            constant = self.fixed.get((contract.name, slot.path[0]))
            if constant is not None:
                # The task fixes this argument: its constant fills it, and nothing else does.
                full[slot.path[0]], partial[slot.path[0]] = [constant], []
                continue
            arg = self.cells_of(_argument_name(slot))
            fresh = (contract.name, slot.path[0]) in self.fresh
            refs = [
                (r, c)
                for r, c in state.refs
                if r not in self.literals
                and self.agree(state, r, slot)
                and not (fresh and r.variable in state.stale)
            ]
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

    def reading(self, state: _State, contract: Contract, unread: frozenset[str]) -> set[str]:
        """Return the optional arguments a call writes an input the task requires into.

        An input no call has read yet is written into the first optional argument it fills: the
        value the program returns must be read from it, and this call can read it.
        """
        reading: set[str] = set()
        for var in sorted(unread):
            slot = next(
                (
                    slot
                    for slot in contract.arguments
                    if not slot.required
                    and slot.path[0] not in reading
                    and any(r.variable == var and self.agree(state, r, slot) for r, _ in state.refs)
                ),
                None,
            )
            if slot is not None:
                reading.add(slot.path[0])
        return reading

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
            if (
                (contract.name, bindings) in state.made
                or self.repeats(state, contract, bindings)
                or not self.record(state, contract, bindings)
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
            # A weak search tests the result into the goal first: searching on past the call
            # reaches the same test deeper, after calls no path needs.
            rest = self.observed(after, contract, result, depth, weak=True) if weak else None
            if rest is None:
                rest = self.solve(after, depth - 1, weak=weak)
            if rest is None:
                rest = self.split(state, after, contract, bindings, result, depth, weak=weak)
            if rest is None and not weak:
                rest = self.observed(after, contract, result, depth, weak=False)
            if rest is not None:
                return Block((call, *rest.calls), rest.end)
        return None

    def split(
        self,
        before: _State,
        after: _State,
        contract: Contract,
        bindings: tuple[tuple[str, Ref], ...],
        result: str,
        depth: int,
        *,
        weak: bool,
    ) -> Block | None:
        """Return a test on a guard a policy reads off the call's result, and a program each side.

        Read without its result, a transition on the result is taken possibly, so a call it
        alone allows is never made after it. Tested, each side of the guard is a case, as an
        observation splits a belief execution tree (Bertoli et al. 2006, Def. 3.6): the result is
        held inside the guard on one side and outside it on the other, and each policy is stepped
        on that side's set, so the transition is taken surely or never there.
        """
        if depth <= _GUARD:
            return None
        record = record_of({name: self.bound(before, ref) for name, ref in bindings}, closed=True)
        whole = contract.returns if contract.returns is not None else anything
        held = dict(after.refs)
        for guard in self.guards.get(contract.name, ()):
            ref = Ref(result, guard.path)
            if ref not in held:
                continue
            cells = held[ref]
            tested = frozenset(i for i in cells if self._inside(i, guard.values))
            sides = []
            for narrowed, meets in (
                (tested, guard.whole),
                (cells - tested, complement(guard.whole)),
            ):
                policed = tuple(
                    policy.step(
                        states, contract.name, record, contract.hints, intersection(whole, meets)
                    )
                    for policy, states in zip(self.policies, before.policed, strict=True)
                )
                if not narrowed or any(
                    policy.reach(states) != "never"
                    for policy, states in zip(self.policies, policed, strict=True)
                ):
                    sides.append(None)
                else:
                    sides.append(after.narrowed(ref, narrowed).but(policed=policed))
            inside, outside = sides
            then = None if inside is None else self.solve(inside, depth - 2, weak=weak)
            if then is None:
                continue
            otherwise = None if outside is None else self.solve(outside, depth - 2, weak=weak)
            if otherwise is None and weak and outside is not None and self.settled(outside):
                otherwise = self.stopping(outside, ref, depth - 2)
            if otherwise is not None:
                return Block((), Branch(ref, "", "", then, otherwise, guard.values))
        return None

    def observed(
        self, after: _State, contract: Contract, result: str, depth: int, *, weak: bool
    ) -> Block | None:
        """Return a test of a field of the call's result into the goal, and a program each side.

        A field whose declared set lists its members, an ``enum`` or a ``const``, and meets the
        goal without lying inside it, is tested on the members it lists that the goal holds, an
        observation splitting the belief (Bertoli et al. 2006, Def. 3.6): its ``then`` side
        returns the field, and its other side takes a program reaching the goal on every path,
        or, in a weak search, stops, the case a weak plan leaves uncovered (Cimatti et al.
        2003, Def. 2.10). A weak program there would call on only to test again, so it is
        searched only where a policy holds an obligation the stop may not leave open. The
        members are the tool's own, so the test narrows what the tool returned and writes none
        the goal holds of itself; a task input, a constant and a field listing no members are
        never tested into the goal.
        """
        if depth < _GUARD:
            return None
        held = dict(after.refs)
        for slot in contract.results:
            ref = Ref(result, slot.path)
            members = _members(slot.schema)
            if ref not in held or members is None or held[ref] <= self.goal:
                continue
            values = tuple(v for v in members if self.goal_values.is_valid(v))
            cells = held[ref]
            tested = frozenset(i for i in cells if values and self._inside(i, values))
            if not tested:
                continue
            then = self.solve(after.narrowed(ref, tested), depth - 2, weak=weak)
            if then is None:
                continue
            outside = after.narrowed(ref, cells - tested)
            otherwise = self.solve(outside, depth - 2, weak=False)
            if otherwise is None and weak:
                otherwise = (
                    self.stopping(outside, ref, depth - 2)
                    if self.settled(outside)
                    else self.solve(outside, depth - 2, weak=True)
                )
            if otherwise is not None:
                return Block((), Branch(ref, "", "", then, otherwise, values))
        return None

    def _inside(self, cell: int, values: tuple[Any, ...]) -> bool:
        """Return whether valgebra proves a cell inside the members a guard lists."""
        return self.abstraction.cells[cell].values.relation_to(listed(list(values))) == "subset"

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
        if weak and (checked := self.checked(state, contract, bindings, depth)) is not None:
            return Call(contract.name, bindings, result, on_error=checked)
        without = self.failing(state, contract, bindings)
        fallback = self.solve(without, depth - 1, weak=weak)
        if fallback is None and weak:
            fallback = self.stopped(without, bindings, depth - 1)
        if fallback is None:
            return None
        return Call(contract.name, bindings, result, on_error=fallback)

    def failing(
        self, state: _State, contract: Contract, bindings: tuple[tuple[str, Ref], ...]
    ) -> _State:
        """Return the state an error of a call leaves, as the verifier reads its block.

        The call may have acted before it answered, so every value held is one read before an
        action, and a read made before it may be made again (Mansoor et al. 2026, §4.1); a later
        call to its tool on values that may be the ones it was given is a retry of it, which a
        tool neither read-only nor idempotent is not given (`_Search.repeats`).
        """
        record = tuple((name, state.cells(ref)) for name, ref in bindings)
        made, stale = state.made | {(contract.name, bindings)}, state.stale
        if "readOnlyHint" not in contract.hints:
            # As after a call that returned (`_Search.after`): a read made before it reads
            # something new when made again.
            stale |= {ref.variable for ref, _ in state.refs if ref not in self.literals}
            made = frozenset(
                (tool, given)
                for tool, given in made
                if "readOnlyHint" not in self.contracts[tool].hints
            )
        return state.but(
            made=made,
            failed=(*state.failed, (contract.name, record)),
            stale=stale,
            withdrawn=self.withdrawn(state, contract),
        )

    def withdrawn(
        self, state: _State, contract: Contract
    ) -> tuple[tuple[str, frozenset[str]], ...]:
        """Return what each fresh rule naming its withdrawing calls holds stale after a call.

        A call the rule names makes every variable held before it stale for that rule.
        """
        held = dict(state.withdrawn)
        names = frozenset(ref.variable for ref, _ in state.refs if ref not in self.literals)
        return tuple(
            (
                flow.name,
                held.get(flow.name, frozenset())
                | (names if flow.withdraws(contract.name, contract.hints) else frozenset()),
            )
            for flow in self.flows
            if flow.withdrawn_by
        )

    def repeats(
        self, state: _State, contract: Contract, bindings: tuple[tuple[str, Ref], ...]
    ) -> bool:
        """Return whether a call may repeat one that answered with an error on the path.

        It may unless, at some argument both take, the cells the two values lie in are
        disjoint: the cells partition the JSON values, so no value lies in both.
        """
        given = dict(bindings)
        return any(
            tool == contract.name
            and all(name not in given or cells & state.cells(given[name]) for name, cells in record)
            for tool, record in state.failed
        )

    def stopped(
        self, state: _State, bindings: tuple[tuple[str, Ref], ...], depth: int
    ) -> Block | None:
        """Return a stop on a value a call was given, after its error, or None.

        A path ends only where no policy holds an obligation open, and a stop names a value the
        program holds, never a constant the task fixes.
        """
        held = [ref for _, ref in bindings if ref not in self.literals]
        if not held or not self.settled(state):
            return None
        return self.stopping(state, held[0], depth)

    def checked(
        self,
        state: _State,
        contract: Contract,
        bindings: tuple[tuple[str, Ref], ...],
        depth: int,
    ) -> Block | None:
        """Return the block verifying a call's effect before it is made again, or None.

        No retry is issued before a verifier reads the effect absent (Mansoor et al. 2026,
        §4.1). Where an acted postcondition of the call requires one key in what its read
        shows, the block reads it on the values the call was given, tests the key, and, where
        the read lacks it, makes the call once more, a stop on its error, and plans the rest as
        after a first success; where the read holds it, the field it opens is held, and a
        program is searched from there. A read that may act gets no block.
        """
        if depth <= _GUARD:
            return None
        given = dict(bindings)
        for post in self.postconditions:
            key = _checked_key(post) if post.after == contract.name else None
            reader = self.contracts.get(post.read)
            if key is None or reader is None or any(t not in given for _, t in post.given):
                continue
            if "readOnlyHint" not in reader.hints:
                # A read that may act shows nothing of the world before it.
                continue
            reading = tuple((mine, given[theirs]) for mine, theirs in post.given)
            if any(s.required and s.path[0] not in dict(reading) for s in reader.arguments):
                continue
            policed = self.policed(state, reader, reading)
            if policed is None:
                continue
            read = f"c{state.fresh}"
            seen = self.after(state.but(policed=policed), reader, reading, read)
            name = _result_name(field_slot(reader.result(), key))
            then = None
            if self.holds(name):
                # The key test makes the field it opens a value the program holds.
                opened = (Ref(read, (key,)), self.cells_of(name))
                then = self.solve(seen.but(refs=(*seen.refs, opened)), depth - 2, weak=True)
            if then is None and self.settled(seen) and bindings:
                then = self.stopping(seen, bindings[0][1], depth - 2)
            again = self.again(seen, contract, bindings, depth - 2)
            if then is not None and again is not None:
                test = Branch(Ref(read), "", "", then, again, key=key)
                # A read-only read is retried on its error, as any read-only call is.
                return Block((Call(reader.name, reading, read, retry=self.errors),), test)
        return None

    def again(
        self,
        state: _State,
        contract: Contract,
        bindings: tuple[tuple[str, Ref], ...],
        depth: int,
    ) -> Block | None:
        """Return the call made once more on its values, a stop on its error, then the rest."""
        policed = self.policed(state, contract, bindings)
        if policed is None:
            return None
        result = f"c{state.fresh}"
        either = tuple(a | b for a, b in zip(state.policed, policed, strict=True))
        stop = self.stopped(state.but(policed=either), bindings, depth - 1)
        if stop is None:
            return None
        after = self.after(state.but(policed=policed), contract, bindings, result)
        rest = self.solve(after, depth - 1, weak=True)
        if rest is None:
            return None
        return Block((Call(contract.name, bindings, result, on_error=stop), *rest.calls), rest.end)

    def stopping(self, state: _State, ref: Ref, depth: int) -> Block:
        """Return what a weak program does where it would stop on ``ref``.

        Where the task accepts a goal after this search's, a program toward it from here takes
        the stop's place, a strong one first; otherwise the path stops, naming its cells. Any set
        of states is a goal (Cimatti et al. 2003, Def. 2.9), so a program each path of which
        returns in one of the task's goals is a strong solution for their union (Cimatti et al.
        2003, Def. 2.10), as one table is strong for one goal and weak for a part of it (Cimatti
        et al. 2003, Ex. 2.12).
        """
        if self.fallback is not None:
            filled = self.fallback.toward(state, depth)
            if filled is not None:
                return filled
        stop = Stop(ref)
        self.stops[id(stop)] = state.cells(ref)
        return Block((), stop)

    def toward(self, state: _State, depth: int) -> Block | None:
        """Return a program from ``state`` toward this search's goal: strong, else weak."""
        return self.solve(state, depth, weak=False) or self.solve(state, depth, weak=True)

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
            otherwise = self.stopping(outside, ref, depth - 1)
        if otherwise is None:
            return None
        return Block((), Branch(ref, contract.name, name, then, otherwise))

    def record(
        self, state: _State, contract: Contract, bindings: tuple[tuple[str, Ref], ...]
    ) -> bool:
        if contract.plain:
            return True
        record = record_of({name: self.bound(state, ref) for name, ref in bindings}, closed=True)
        return record.relation_to(contract.accepts) == "subset"

    def policed(
        self, state: _State, contract: Contract, bindings: tuple[tuple[str, Ref], ...]
    ) -> tuple[frozenset[str], ...] | None:
        """Return each policy's states after the call, or None if one may turn bad."""
        if not self.policies:
            return state.policed
        record = record_of({name: self.bound(state, ref) for name, ref in bindings}, closed=True)
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
        stale = state.stale
        if "readOnlyHint" not in contract.hints:
            # A call that may act may change what every value held so far read, so a read-only
            # call made before it reads something new when made again (Mansoor et al. 2026, §4.1).
            stale |= {ref.variable for ref, _ in state.refs if ref not in self.literals}
            made = frozenset(
                (tool, given)
                for tool, given in made
                if "readOnlyHint" not in self.contracts[tool].hints
            )
        return state.but(
            refs=state.refs + results,
            made=made,
            fresh=state.fresh + 1,
            origins=(*state.origins, (result, contract.name)),
            stale=stale,
            given=(*state.given, (result, bindings)),
            withdrawn=self.withdrawn(state, contract),
            read=state.read | ({ref.variable for _, ref in bindings} & state.reading),
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

    @staticmethod
    def current(
        state: _State,
        flow: Flow,
        bindings: tuple[tuple[str, Ref], ...],
        ref: Ref,
        origin: str | None,
    ) -> bool:
        """Return whether a source's value was returned when a rule reading that requires.

        With ``fresh``, after the last call on the path that may act, or that the rule names
        where it names the calls that withdraw it; with ``key``, by a call
        that took at the key the reference this call takes there. A value from elsewhere is the
        rest of the rule's.
        """
        if not (flow.fresh or flow.key) or origin not in flow.sources:
            return True
        stale = dict(state.withdrawn).get(flow.name, ()) if flow.withdrawn_by else state.stale
        if flow.fresh and ref.variable in stale:
            return False
        source = dict(dict(state.given).get(ref.variable, ())).get(flow.key)
        return not flow.key or (source is not None and source == dict(bindings).get(flow.key))

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
                if name != flow.argument:
                    continue
                origin = None if ref in self.literals else state.origin(ref)
                if not flow.holds(origin) and self.current(state, flow, bindings, ref, origin):
                    continue
                if flow.endorsed is None:
                    return False
                if self.bound(state, ref).relation_to(flow.endorsed) != "subset":
                    return False
        return True


def _meets(one: Validator[Any], other: Validator[Any]) -> bool:
    """Return whether two sets may share a value: not proven disjoint."""
    return intersection(one, other).relation_to(nothing) != "subset"


def _reason(
    cut: int,
    depth: int,
    contracts: Mapping[str, Contract],
    task: Task,
    mattering: frozenset[str],
) -> str:
    """Return why a search found no program: the bound, and what keeps the goal out of reach.

    Beside the bound, it names each required argument of a tool on a path toward the goal
    (``mattering``) that only a tested input fills, then those of a tool only a policy names,
    and each result field that meets the goal and lies outside it and lists no members, which
    the search tests into the goal on none (`_Search.observed`).
    """
    if cut:
        reason = (
            f"no program within the bound of {depth} calls and tests, which cut {cut} paths "
            "short of the goal: whether a deeper search finds one is not known"
        )
    else:
        reason = (
            f"no program: no path reached the bound of {depth}, so no deeper search finds one "
            f"from the first {MAX_BINDINGS} bindings of each call the search tries"
        )
    if sourceless := _sourceless(contracts, task, mattering):
        reason += (
            f"; no tool's result guarantees a value for {', '.join(sourceless)}, which a task "
            "input fills only under a test"
        )
    if beside := _sourceless(contracts, task, frozenset(contracts) - mattering):
        reason += (
            f"; nor for {', '.join(beside)}, of tools only a policy names, whose results "
            "lead to no goal"
        )
    if near := _near_the_goal(contracts, task, mattering):
        meets = near[0] if len(near) == 1 else f"each of {', '.join(near)}"
        reason += (
            f"; {meets} meets the goal and lies outside it, and lists no members a test into "
            "the goal could name"
        )
    return reason


def _near_the_goal(
    contracts: Mapping[str, Contract], task: Task, mattering: frozenset[str]
) -> list[str]:
    """Return each result field listing no members that meets the goal and lies outside it."""
    return sorted(
        slot.name
        for name, contract in contracts.items()
        if name in mattering
        for slot in contract.results
        if slot.path
        and _members(slot.schema) is None
        and _meets(slot.values, task.goal)
        and slot.values.relation_to(task.goal) != "subset"
    )


def _literal(block: Block, literals: Mapping[Ref, Any]) -> Block:
    """Return ``block`` with each reference a constant is held under written as its literal."""
    if not literals:
        return block
    calls = tuple(
        Call(
            call.tool,
            tuple(
                (name, Const.of(literals[ref]) if isinstance(ref, Ref) and ref in literals else ref)
                for name, ref in call.bindings
            ),
            call.result,
            call.retry,
            None if call.on_error is None else _literal(call.on_error, literals),
        )
        for call in block.calls
    )
    end = block.end
    if isinstance(end, Branch):
        end = Branch(
            end.ref,
            end.tool,
            end.argument,
            _literal(end.then, literals),
            _literal(end.otherwise, literals),
            end.values,
            end.key,
        )
    elif isinstance(end, For):
        end = For(end.ref, end.item, _literal(end.body, literals), _literal(end.after, literals))
    return Block(calls, end)


def _sourceless(contracts: Mapping[str, Contract], task: Task, among: frozenset[str]) -> list[str]:
    """Return each required argument of a tool of ``among`` only a tested task input reaches.

    The search fills an argument from a value the program holds, never a constant (`call`). An
    argument no result a tool guarantees meets, and no value held fills whole, is filled only by
    testing a task input into it: a free-text name tested into `left|right`, which names a
    constant the task does not give. A field a result declares and does not guarantee is no
    value a program holds (`valgebra_planning.contracts.read_contract`).
    """
    held = [slot.values for slots in task.held.values() for slot in slots]
    results = [slot.values for contract in contracts.values() for slot in contract.results]
    fixed = {f"{tool}.{argument}" for tool, argument, _ in task.constants}
    return sorted(
        slot.name
        for name, contract in contracts.items()
        if name in among
        for slot in contract.arguments
        if slot.required
        and slot.name not in fixed
        and not any(_meets(value, slot.values) for value in results)
        and not any(value.relation_to(slot.values) == "subset" for value in held)
    )


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
    held += [listed([value]) for _, _, value in task.constants]
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
    if source is not None and contracts[source].declared is not None:
        found.append(f"{ref} -> {argument}, from the output of {source} read closed")
    return found


def _readable(values: Validator[Any], consumers: Sequence[Validator[Any]]) -> bool:
    """Return whether some argument or the goal may hold a value of ``values``."""
    return any(
        intersection(values, consumer).relation_to(nothing) != "subset" for consumer in consumers
    )


def predicates(
    contracts: Mapping[str, Contract],
    task: Task,
    policies: Sequence[Policy] = (),
    postconditions: Sequence[Postcondition] = (),
) -> list[Predicate]:
    """Return every argument and the goal, and each result or input some of them may read.

    A value no argument and no goal can hold is never passed on, so its cells cost the
    search and decide nothing; it is left out, with its reference. The sets are gathered in
    the order of their names, which fixes the cut: valgebra leaves some meets undecided, and
    which ones depends on the order the cut splits by and draws its members in, so a listing
    order of its own would make a library's count that order's. The members a policy's guard on
    a result lists cut the cells too, so the field it reads is held and a test can split on it,
    and so does the field a key test on an acted postcondition's read opens.
    """
    listings = [
        (f"guard:{tool}:{index}", guard.values)
        for tool, held in sorted(_guards(policies).items())
        for index, guard in enumerate(held)
    ]
    arguments = [slot for contract in contracts.values() for slot in contract.arguments]
    consumers = [slot.values for slot in arguments] + [task.goal]
    consumers += [listed(list(values)) for _, values in listings]
    entries = [(_argument_name(slot), slot.values, slot.schema, slot.root) for slot in arguments]
    entries += [
        (_result_name(slot), slot.values, slot.schema, slot.root)
        for contract in contracts.values()
        for slot in (*contract.results, *_opened({contract.name: contract}, postconditions))
        if _readable(slot.values, consumers)
    ]
    entries += [
        (_held_name(slot), slot.values, slot.schema, slot.root)
        for slots in task.held.values()
        for slot in slots
        if _readable(slot.values, consumers)
    ]
    entries.append((_GOAL, task.goal, task.goal_schema, task.goal_schema))
    entries += [
        (f"{_GOAL}:{index}", values, schema, schema)
        for index, (values, schema) in enumerate(task.preferred)
    ]
    entries += [
        (name, listed(list(values)), {"enum": list(values)}, {"enum": list(values)})
        for name, values in listings
    ]
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


def _changed(block: Block, at: _Address, change: Callable[[Block, int], Block]) -> Block:
    """Return ``block`` with ``change`` applied to the block holding the call ``at`` names.

    ``change`` takes that block and the call's index in it; every other part is kept as it is.
    """
    (kind, index), *rest = at
    calls, end = list(block.calls), block.end
    if kind == "call":
        return change(block, index)
    if kind == "error" and (call := calls[index]).on_error is not None:
        kept = _changed(call.on_error, tuple(rest), change)
        calls[index] = Call(call.tool, call.bindings, call.result, call.retry, kept)
    elif isinstance(end, Branch):
        then = _changed(end.then, tuple(rest), change) if kind == "then" else end.then
        otherwise = (
            _changed(end.otherwise, tuple(rest), change) if kind == "else" else end.otherwise
        )
        end = Branch(end.ref, end.tool, end.argument, then, otherwise, end.values, end.key)
    elif isinstance(end, For):
        body = _changed(end.body, tuple(rest), change) if kind == "body" else end.body
        after = _changed(end.after, tuple(rest), change) if kind == "after" else end.after
        end = For(end.ref, end.item, body, after)
    return Block(tuple(calls), end)


def _without(block: Block, at: _Address) -> Block:
    """Return ``block`` with the call ``at`` names left out; every other part is kept as it is."""

    def left_out(holder: Block, index: int) -> Block:
        return Block(holder.calls[:index] + holder.calls[index + 1 :], holder.end)

    return _changed(block, at, left_out)


def _sinkable(block: Block, at: _Address = ()) -> list[tuple[_Address, str]]:
    """Return each call before a test that may move into one of its branches, and which.

    A call may move where neither the test nor a call after it reads its result, into the one
    branch reading it, or, where neither does, as a call a policy keeps, into either.
    """
    found: list[tuple[_Address, str]] = []
    end = block.end
    if isinstance(end, Branch):
        for index, call in enumerate(block.calls):
            if call.result == end.ref.variable or call.result in _reads(
                Block(block.calls[index + 1 :], Next())
            ):
                continue
            read = {
                side: call.result in _reads(branch)
                for side, branch in (("then", end.then), ("else", end.otherwise))
            }
            if all(read.values()):
                continue
            sides = [side for side, reads in read.items() if reads] or list(read)
            found += [((*at, ("call", index)), side) for side in sides]
    for index, call in enumerate(block.calls):
        if call.on_error is not None:
            found += _sinkable(call.on_error, (*at, ("error", index)))
    if isinstance(end, Branch):
        found += _sinkable(end.then, (*at, ("then", 0)))
        found += _sinkable(end.otherwise, (*at, ("else", 0)))
    elif isinstance(end, For):
        found += _sinkable(end.body, (*at, ("body", 0))) + _sinkable(end.after, (*at, ("after", 0)))
    return found


def _sunk(side: str) -> Callable[[Block, int], Block]:
    """Return the change moving a block's call into the ``side`` branch of its test."""

    def sink(holder: Block, index: int) -> Block:
        call, end = holder.calls[index], holder.end
        if not isinstance(end, Branch):
            return holder
        then, otherwise = end.then, end.otherwise
        if side == "then":
            then = Block((call, *then.calls), then.end)
        else:
            otherwise = Block((call, *otherwise.calls), otherwise.end)
        branch = Branch(end.ref, end.tool, end.argument, then, otherwise, end.values, end.key)
        return Block(holder.calls[:index] + holder.calls[index + 1 :], branch)

    return sink


def _test_first(program: Block, reading: Callable[[Block], object]) -> Block:
    """Move each call the next test does not read into the branch reading it, if nothing else moves.

    The search makes a call where it first may, so a call whose result one branch alone reads
    stands before the test, made on the path that never reads it: a confirmation asked before
    the distance test that decides whether the grasp it confirms is made at all. Observing first
    is the order to prefer, since an action can always be postponed to observing, while
    information may be lost after acting (Bertoli et al. 2006, §6.2). Moving a call may move an
    implicit flow, an obligation or an error block, so the program is verified again and the move
    kept only where every link and every stop read as before.
    """
    held = reading(program)
    while True:
        for at, side in _sinkable(program):
            moved = _changed(program, at, _sunk(side))
            if reading(moved) == held:
                program = moved
                break
        else:
            return program


def _reading(checked: Verification) -> object:
    """Return what a verification says of a program: its links not proven, and where it stops.

    Each is read once: a walk split into cases at a call a result guard reads meets one stop
    once a case, and a call nothing needs makes more cases, not another stop.
    """
    unproven = sorted(
        {(link.claim, link.verdict) for link in checked.links if link.verdict != "proven"}
    )
    return unproven, sorted({gap.ref for gap in checked.gaps})


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


def _preferred(program: Block, contracts: Mapping[str, Contract], task: Task) -> tuple[str, ...]:
    """Return where each return the verifier proves inside a task's first goal lies."""
    if not task.preferred:
        return ()
    first = replace(task, goal=task.preferred[0][0], preferred=())
    return tuple(
        link.where
        for link in verify(program, contracts, first).links
        if link.claim.startswith("return ") and link.verdict == "proven"
    )


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


def _key_of(schema: object) -> str | None:
    """Return the one key a schema requires and asserts nothing else of, or None.

    ``{"required": ["label_id"]}`` is a test a program writes, ``if "label_id" in c3:``, whose
    side lacking the key holds no value of the schema.
    """
    if not isinstance(schema, dict) or set(schema) - {"required", "type"} - ANNOTATIONS:
        return None
    if schema.get("type", "object") != "object":
        return None
    required = schema.get("required")
    if isinstance(required, list) and len(required) == 1 and isinstance(required[0], str):
        return required[0]
    return None


def _checked_key(post: Postcondition) -> str | None:
    """Return the key the block verifying before a retry tests a read for, or None.

    Only a postcondition of a call's acting says what a read after its error shows.
    """
    return _key_of(post.schemas[1]) if post.acted else None


def _opened(
    contracts: Mapping[str, Contract], postconditions: Sequence[Postcondition]
) -> list[Slot]:
    """Return the field of each acted postcondition's read its key test makes the program hold."""
    opened = []
    for post in postconditions:
        key = _checked_key(post)
        if key is not None and post.read in contracts and contracts[post.read].results:
            opened.append(field_slot(contracts[post.read].result(), key))
    return opened


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
    postconditions: Sequence[Postcondition] = (),
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
    names every binding that rests on one. Where ``errors`` is set, a weak program answers an
    error of a call an acted postcondition covers by reading its effect first (`_Search.checked`).
    Raises `ValueError` for a task naming an input as a tool.
    """
    fixes(task, contracts)
    # The tools are tried in their names' order, so a server listing them in another order
    # gets the same program.
    contracts = dict(sorted(contracts.items()))
    kept = frozenset(tool for policy in policies for tool in policy.tools()) | frozenset(
        name
        for name, contract in contracts.items()
        if any(policy.hints() & contract.hints for policy in policies)
    )
    kept |= frozenset(post.read for post in postconditions if _checked_key(post) is not None)
    mattering = frozenset(_reachable(contracts, task, frozenset()))
    contracts = _reachable(contracts, task, kept)
    guards = _guards(policies)
    abstraction = abstract(predicates(contracts, task, policies, postconditions), seed=seed)
    # A task naming goals in an order gets a search toward each, the first's stops filled from
    # the next's: one toward their union would return in whichever is shallowest.
    names = [f"{_GOAL}:{index}" for index in range(len(task.preferred))] or [_GOAL]
    searches = [
        _Search(
            contracts,
            abstraction,
            abstraction.cells_of(abstraction.index(name)),
            errors=errors,
            policies=tuple(policies),
            flows=tuple(flows),
            types=types,
        )
        for name in names
    ]
    # Each constant the task fixes is held under a reference no program names, in the cell its
    # value lies in, and written as the literal once the search returns.
    fixed = {
        (tool, argument): Ref(f"{_CONSTANT}{index}")
        for index, (tool, argument, _) in enumerate(task.constants)
    }
    literals = {fixed[tool, argument]: value for tool, argument, value in task.constants}
    goals = [values for values, _ in task.preferred] or [task.goal]
    for one, goal in zip(searches, goals, strict=True):
        one.fixed, one.literals, one.guards = fixed, literals, guards
        one.fresh, one.postconditions = frozenset(task.fresh), tuple(postconditions)
        one.goal_values = goal
    for one, after in itertools.pairwise(searches):
        one.fallback = after
        after.stops = searches[0].stops
    search = searches[0]
    start = _State(
        tuple(
            (Ref(var, slot.path), search.cells_of(_held_name(slot)))
            for var, slots in task.held.items()
            for slot in slots
            if search.holds(_held_name(slot))
        )
        + tuple(
            (ref, frozenset(i for i, c in enumerate(abstraction.cells) if c.values.is_valid(value)))
            for ref, value in literals.items()
        ),
        frozenset(),
        1,
        tuple(frozenset({policy.initial}) for policy in policies),
        reading=task.reads,
    )

    def reading(program: Block) -> object:
        checked = verify(program, contracts, task, errors=errors, policies=policies, flows=flows)
        return _reading(checked)

    def tidied(program: Block) -> Block:
        program = _tidy(_literal(program, literals), search.stops, kept)
        unpadded = _tidy(_unpadded(program, reading), search.stops, kept) if kept else program
        return _tidy(_test_first(unpadded, reading), search.stops, kept)

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
                preferred=_preferred(program, contracts, task),
            )
    if weak:
        # One search at the full bound: a branch stops only where no continuation exists
        # within the depth its path has left, not where a shallower bound ran out.
        search.failed.clear()
        search.cut = 0
        program = _ranked(search, start, depth, tidied)
        if program is None and search.fallback is not None:
            found = search.fallback.toward(start, depth)
            program = None if found is None else tidied(found)
        if program is not None:
            gaps = tuple(_uncovered(program, search))
            return Plan(
                program,
                strong=bool(task.preferred) and not gaps,
                uncovered=gaps,
                abstraction=abstraction,
                expanded=sum(one.expanded for one in searches),
                assumed=tuple(_assumed(program, types, contracts)),
                preferred=_preferred(program, contracts, task),
            )
    return Plan(
        None,
        strong=False,
        uncovered=(),
        abstraction=abstraction,
        expanded=search.expanded,
        reason=_reason(search.cut, depth, contracts, task, mattering),
    )
