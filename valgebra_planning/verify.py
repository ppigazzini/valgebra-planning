"""Every link of a program, asked of valgebra alone.

A link is an inclusion a program rests on: a reference inside the argument it fills, a call's
whole argument record inside the tool's input set where the arguments are related, and the
returned reference inside the goal. Each is asked of valgebra with the set the reference lies in
at that point of the program, narrowed by every test on the way there, and nothing else, not the
cells, not the search: the belief execution along a path (Bertoli et al. 2006, Def. 3.4), each
reference's set read apart from the others'. A program whose every link is proven and on which
no path stops is a strong solution, applicable at each call and inside the goal at the end of
each path (Bertoli et al. 2006, Thm. 3.5). A refuted link carries a value outside the inclusion
where one is found among the examples given and the values drawn from the reference's own
schema. A value found that way refutes an undecided link too, since a concrete member is a
proof.
"""

from __future__ import annotations

import itertools
import json
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from valgebra_planning._valgebra import (
    ARRAY,
    Sampler,
    Unsupported,
    Validator,
    anything,
    complement,
    intersection,
    nothing,
    union,
)
from valgebra_planning.contracts import (
    Contract,
    Slot,
    Task,
    field_slot,
    having,
    item_slots,
    listed,
    record_of,
    selecting,
    unclashed,
    unread,
    within,
)
from valgebra_planning.flows import Flow
from valgebra_planning.policy import Policy
from valgebra_planning.program import Block, Branch, Call, Const, For, Next, Ref, Return, Stop
from valgebra_planning.run import annotated, retry_assumptions

__all__ = ["Gap", "Link", "Verdict", "Verification", "verify"]

Verdict = Literal["proven", "refuted", "undecided"]

#: How many values are drawn from a reference's schema in search of a counterexample.
DRAWS = 40
#: How many values a loop's item is proven held on as templates, and how many records lacking
#: a key are tried for an item.
_BUILT = 4


@dataclass(frozen=True, slots=True)
class Link:
    """One inclusion a program rests on, where it sits, and what valgebra says of it.

    ``where`` names the branches leading to it, ``then`` and ``else`` joined by dots, the
    empty string being the top. ``counterexample`` holds the value refuting it, when one is
    found.
    """

    where: str
    claim: str
    verdict: Verdict
    counterexample: tuple[Any, ...] = ()
    #: What the verdict rests on beside the contracts, never a proof: an output schema inferred
    #: from observed calls or read closed, the annotation hints a policy or a retry reads. The
    #: verdict is valgebra's over the contracts and these.
    assumed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Gap:
    """A path the program stops on, and the reference it leaves uncovered there."""

    where: str
    ref: str
    #: Whether valgebra proves the value stopped on inside a set the task tolerates there.
    tolerated: bool = False


@dataclass(frozen=True, slots=True)
class Verification:
    """The links of a program and the gaps it leaves."""

    links: tuple[Link, ...]
    gaps: tuple[Gap, ...]

    @property
    def proven(self) -> bool:
        """Return whether every link is proven and no path stops."""
        return not self.gaps and all(link.verdict == "proven" for link in self.links)

    @property
    def verified(self) -> bool:
        """Return whether every link is proven, whatever paths stop."""
        return all(link.verdict == "proven" for link in self.links)

    @property
    def weak(self) -> bool:
        """Return whether the program reaches the goal on some path and stops only as tolerated.

        Every link is proven, some path returns, and each stop is on a value the task tolerates
        there: a weak solution, one reaching the goal on some execution (Cimatti et al. 2003,
        Def. 2.10), whose every other end the task accepts.
        """
        returns = any(link.claim.startswith("return ") for link in self.links)
        return self.verified and returns and all(gap.tolerated for gap in self.gaps)

    def count(self, verdict: Verdict) -> int:
        """Return how many links read ``verdict``."""
        return sum(link.verdict == verdict for link in self.links)


@dataclass(frozen=True, slots=True)
class _Held:
    """The set a reference lies in at a point of a program, and the slot it came from."""

    values: Validator[Any]
    origin: Slot
    #: Where the value is a loop's item: the array it is read from.
    loop: _Loop | None = field(default=None, compare=False)
    #: Whether the value is a call's result narrowed to one case of a split on a policy's
    #: result guard, so a branch none of its values reaches is no path.
    case: bool = field(default=False, compare=False)


@dataclass(frozen=True, slots=True, eq=False)
class _Loop:
    """The array a loop reads its item from, the item's name, and what the program held then."""

    array: Ref
    item: str
    env: Mapping[Ref, _Held]


@dataclass(slots=True)
class _Walk:
    contracts: Mapping[str, Contract]
    task: Task
    examples: Mapping[str, Sequence[Any]]
    rng: random.Random
    errors: bool
    policies: tuple[Policy, ...]
    flows: tuple[Flow, ...]
    links: list[Link] = field(default_factory=list)
    #: The policy states each iteration of the loop being walked ends in.
    ends: list[_States] | None = None
    #: The origins tested on each path that ends the run inside the loop being walked.
    exits: list[frozenset[str]] | None = None
    gaps: list[Gap] = field(default_factory=list)
    #: The values drawn for each value holding a loop's array, by the set held for it; drawn
    #: apart from `rng`, so a witness drawn elsewhere is the same with loops or without.
    drawn: dict[int, tuple[_Held, list[Any]]] = field(default_factory=dict)
    builder: random.Random | None = None
    seed: int = 0

    def block(self, block: Block, env: dict[Ref, _Held], where: str, path: _Path) -> None:
        for index, call in enumerate(block.calls):
            cases = self.call(call, env, where, path)
            if len(cases) > 1:
                rest = Block(block.calls[index + 1 :], block.end)
                for number, (case_env, case_path) in enumerate(cases):
                    self.block(
                        rest, case_env, _join(where, f"case:{call.result}:{number}"), case_path
                    )
                return
            env, path = cases[0]
        end = block.end
        if isinstance(end, Return):
            held = self.held(env, end.ref, where)
            if held is not None:
                claim = f"return {end.ref} <= goal"
                self.inclusion(where, claim, end.ref, held, self.task.goal, env)
            self.obeyed(where, path.states)
            self.ended(path)
        elif isinstance(end, Stop):
            self.gaps.append(Gap(where, str(end.ref), self.tolerated(env, end.ref)))
            self.obeyed(where, path.states)
            self.ended(path)
        elif isinstance(end, Next):
            if self.ends is not None:
                self.ends.append(path.states)
        elif isinstance(end, For):
            self.loop(end, env, where, path)
        else:
            self.branch(end, env, where, path)

    def tolerated(self, env: dict[Ref, _Held], ref: Ref) -> bool:
        """Return whether the task tolerates a stop on ``ref`` here.

        It does where valgebra proves every whole value the stopped reference's root may hold
        here, met with each field tested on the way, inside the set the task gives for the tool
        or input it came from. A loop's item is read as no whole value of its tool: a stop on it
        is never tolerated.
        """
        root = env.get(Ref(ref.variable))
        if root is None or root.loop is not None:
            return False
        allowed = dict(self.task.stops).get(root.origin.tool)
        if allowed is None:
            return False
        return self.belief(env, ref.variable).relation_to(allowed) == "subset"

    def loop(self, end: For, env: dict[Ref, _Held], where: str, path: _Path) -> None:
        """Walk a loop's body for an arbitrary item, the policies at a fixpoint; then what follows.

        Nothing crosses iterations, so one walk over the item's set holds for every item. The
        body may run any number of times, so the states a policy may be in when it starts are
        taken to a fixpoint, the states being finite. How many times it runs is the array's, so
        its calls run under a test on the array's source (Debenedetti et al. 2025, §5.4). What
        follows runs only where no iteration ended the run, so it runs under every test a path
        ending the run in the body is under, the array's source among them: an early exit is a
        test, as a statement after an operation that may end a run early depends on what that
        operation read (Debenedetti et al. 2025, §7). The item's name binds the item alone:
        nothing the program held under that name, nor any field of it, is read in the body. The
        item keeps the array it is read from, so a question its set leaves open is asked of
        every item of the array, `_Walk.asked`.

        A body that may act runs after its own action from the second item on, so every value
        held before the loop, the item among them, is read before a call that may act there, and
        after the loop too (Mansoor et al. 2026, §4.1): the body and what follows are walked with
        each of them stale.
        """
        held = self.held(env, end.ref, where)
        if held is None:
            return
        item = self.element(held)
        inner = {ref: held for ref, held in env.items() if ref.variable != end.item}
        inner[Ref(end.item)] = _Held(item.values, item, _Loop(end.ref, end.item, env))
        inside, tested = _join(where, f"for:{end.item}"), path.tested | {held.origin.tool}
        if _acts(end.body, self.contracts):
            path = replace(path, stale=path.stale | {ref.variable for ref in inner})
        states = path.states
        while self.policies:
            ends, _ = self.iterate(
                end.body, inner, inside, replace(path, states=states, tested=tested), silent=True
            )
            grown = tuple(s.union(*(e[i] for e in ends)) for i, s in enumerate(states))
            if grown == states:
                break
            states = grown
        _, exits = self.iterate(
            end.body, inner, inside, replace(path, states=states, tested=tested), silent=False
        )
        after = path.tested.union(*exits)
        self.block(end.after, env, where, replace(path, states=states, tested=after))

    def element(self, held: _Held) -> Slot:
        """Return the slot of an element of the array ``held``, as far as valgebra proves it.

        The walk reads ``items`` itself, so what it reads is kept only where valgebra proves
        every array held there holds its elements inside it. An element it does not read, as
        under ``prefixItems`` or an ``items`` an ``allOf`` or a ``$ref`` beside other keywords
        declares, one the companion refuses apart from its resource, and one valgebra does not
        prove takes any value: every question that set leaves open is asked of the value holding
        the array, `_Walk.asked`, so a link on the element is decided as exactly, and only the
        proof from the element's own set and the witness drawn from its schema are lost.
        """
        try:
            items = item_slots(held.origin)
        except Unsupported:
            items = ()
        if items and intersection(held.values, ARRAY).relation_to(Validator([items[0].values])) == (
            "subset"
        ):
            return items[0]
        origin = held.origin
        path = (*origin.path, "[]")
        return Slot(
            origin.tool,
            path,
            schema=True,
            values=anything,
            required=True,
            root=origin.root,
            asserted=origin.asserted,
        )

    def iterate(
        self, body: Block, env: dict[Ref, _Held], where: str, path: _Path, *, silent: bool
    ) -> tuple[list[_States], list[frozenset[str]]]:
        """Walk a loop's body; return the states its iterations end in, and what ends the run.

        The second list holds, for each path ending the run in the body, the origins it tested.
        """
        links, gaps, ends, exits = self.links, self.gaps, self.ends, self.exits
        self.ends, self.exits = [], []
        if silent:
            self.links, self.gaps = [], []
        self.block(body, env, where, path)
        found, left = self.ends, self.exits
        self.links, self.gaps, self.ends, self.exits = links, gaps, ends, exits
        if exits is not None:
            # A path ending the run in a loop inside this one ends it in this one too.
            exits.extend(left)
        return found, left

    def ended(self, path: _Path) -> None:
        """Record the origins a path ending the run inside a loop tested, for what follows it."""
        if self.exits is not None:
            self.exits.append(path.tested)

    def obeyed(self, where: str, states: _States) -> None:
        """Add a link per policy where a path ends: whether it may end in a bad or pending state."""
        for policy, held in zip(self.policies, states, strict=True):
            verdict = _OBEYED[policy.reach(held, ended=True)]
            hinted = policy.hints() and not all(c.trusted for c in self.contracts.values())
            assumed = (f"the annotation hints policy {policy.name} reads",) if hinted else ()
            self.links.append(Link(where, f"policy {policy.name} holds", verdict, (), assumed))

    def branch(self, test: Branch, env: dict[Ref, _Held], where: str, path: _Path) -> None:
        held = self.held(env, test.ref, where)
        if test.key:
            tested = having(test.key)
        elif test.tool:
            slot = self.argument(test.tool, test.argument, where)
            tested = None if slot is None else slot.values
        else:
            tested = listed(test.values)
        if held is None or tested is None:
            return
        inside = _Held(intersection(held.values, tested), held.origin, held.loop)
        outside = _Held(intersection(held.values, complement(tested)), held.origin, held.loop)
        # Which branch runs carries the tested value's influence: an implicit flow.
        tested = replace(path, tested=path.tested | {held.origin.tool})
        for side, narrowed, name in (
            (test.then, inside, "then"),
            (test.otherwise, outside, "else"),
        ):
            branched = {**env, test.ref: narrowed}
            if not self.reached(branched, test.ref.variable):
                continue
            self.block(side, branched, _join(where, name), tested)

    def reached(self, env: dict[Ref, _Held], variable: str) -> bool:
        """Return whether a branch is a path: not where a case's value is proven to miss it.

        A call's result narrowed to one case of a split on a policy's result guard reaches a
        branch only where some value of the case meets every test on the way; a case and a
        branch valgebra proves disjoint are no execution (Bertoli et al. 2006, Def. 3.4).
        """
        root = env.get(Ref(variable))
        if root is None or not root.case:
            return True
        return self.belief(env, variable).relation_to(nothing) != "subset"

    def call(
        self, call: Call, env: dict[Ref, _Held], where: str, path: _Path
    ) -> list[tuple[dict[Ref, _Held], _Path]]:
        """Walk a call; return the cases the path goes on in, one where nothing splits it.

        A policy transition reading the call's result splits the path at the call, as an
        observation splits a belief execution tree in two (Bertoli et al. 2006, Def. 3.6): each
        case holds the result in one cell of the guards' sets and their complements, the belief
        met with the observation (Bertoli et al. 2006, Def. 3.4), and the policy stepped on that
        cell; a cell valgebra proves empty is dropped.
        """
        contract = self.contracts.get(call.tool)
        if contract is None:
            self.links.append(Link(where, f"{call.tool} is a tool", "refuted"))
            return [(env, path)]
        self.flowing(call, env, where, path)
        acted = path.acted
        failing = self.repeated(call, contract, where, acted) if acted or call.on_error else acted
        states = path.states
        bound = self.bind(call, env, where)
        for slot in contract.arguments:
            if slot.required and slot.path[0] not in dict(call.bindings):
                self.links.append(Link(where, f"{slot.name} is bound", "refuted"))
        self.fixed(call, env, where)
        self.read_fresh(call, where, path)
        if not contract.plain and len(bound) == len(call.bindings):
            self.record(call, contract, bound, where, env)
        after = states
        record = record_of({name: held.values for name, held in bound.items()}, closed=True)
        if self.policies:
            after = tuple(
                policy.step(held, call.tool, record, contract.hints)
                for policy, held in zip(self.policies, states, strict=True)
            )
        # A tool that answered with an error may still have acted, so its block reads both.
        either = tuple(before | then for before, then in zip(states, after, strict=True))
        stale = path.stale - {call.result}
        if "readOnlyHint" not in contract.hints:
            # A call that may act may change what every value held so far read.
            stale |= {ref.variable for ref in env if ref.variable != call.result}
        self.failure(call, contract, env, where, _Path(either, path.tested, failing, stale))
        defined = {ref: held for ref, held in env.items() if ref.variable != call.result}
        for slot in contract.results:
            defined[Ref(call.result, slot.path)] = _Held(slot.values, slot)
        left = _unread(acted, call.result) if acted else acted
        whole = defined.get(Ref(call.result))
        guards = [
            transition.result
            for policy in self.policies
            for transition in policy.transitions
            if transition.result is not None and transition.reads(call.tool, contract.hints)
        ]
        if whole is None or not guards:
            return [(defined, _Path(after, path.tested, left, stale))]
        cases = []
        for cell in _cells(whole.values, guards):
            stepped = tuple(
                policy.step(held, call.tool, record, contract.hints, cell)
                for policy, held in zip(self.policies, states, strict=True)
            )
            narrowed = {**defined, Ref(call.result): _Held(cell, whole.origin, case=True)}
            cases.append((narrowed, _Path(stepped, path.tested, left, stale)))
        return cases

    def repeated(self, call: Call, contract: Contract, where: str, acted: _Acted) -> _Acted:
        """Check a call against those that may have acted on this path; return what its block holds.

        A call made again on the values one that may have acted was given is a retry written
        out, and holds as one: only for a read-only or idempotent tool, on its annotations unless
        they are trusted. A call on a value read again after the error is not one: whether it
        repeats the first rests on what the read shows. Only an error block holds a call that
        may have acted, so a path outside one asks nothing.
        """
        made = (call.tool, frozenset(call.bindings))
        if made in acted:
            verdict: Verdict = "proven" if contract.idempotent else "refuted"
            assumed = annotated(contract) if contract.idempotent else ()
            self.links.append(Link(where, f"{call.tool} may be retried", verdict, (), assumed))
        return acted | {made} if call.on_error is not None else acted

    def bind(self, call: Call, env: dict[Ref, _Held], where: str) -> dict[str, _Held]:
        """Add the link of each binding inside its argument; return the set each argument holds."""
        bound: dict[str, _Held] = {}
        for name, ref in call.bindings:
            slot = self.argument(call.tool, name, where)
            if isinstance(ref, Const):
                if slot is not None:
                    bound[name] = _Held(listed([ref.value]), slot)
                    self.constant(where, ref, slot)
                continue
            held = self.held(env, ref, where)
            if slot is None or held is None:
                continue
            bound[name] = held
            self.inclusion(where, f"{ref} <= {slot.name}", ref, held, slot.values, env)
        return bound

    def flowing(self, call: Call, env: dict[Ref, _Held], where: str, path: _Path) -> None:
        """Add a link for every flow rule whose sink this call fills."""
        for flow in self.flows:
            if flow.tool != call.tool:
                continue
            if flow.implicit and (tested := sorted(filter(flow.holds, path.tested))):
                claim = f"flow {flow.name}: {call.tool} runs under a test on {', '.join(tested)}"
                self.links.append(Link(where, claim, "refuted"))
            for name, ref in call.bindings:
                if name != flow.argument:
                    continue
                if isinstance(ref, Const):
                    if flow.holds(None):
                        self.literal(where, flow, ref)
                    continue
                held = self.resolve(env, ref).held
                if held is None or not flow.holds(held.origin.tool):
                    continue
                claim = f"flow {flow.name}: {ref} from {held.origin.tool} reaches {flow.sink}"
                if flow.endorsed is None:
                    self.links.append(Link(where, claim, "refuted"))
                else:
                    self.inclusion(where, claim, ref, held, flow.endorsed, env)

    def literal(self, where: str, flow: Flow, const: Const) -> None:
        """Add the link of a literal a rule holds: proven only inside the endorsed set."""
        inside = flow.endorsed is not None and flow.endorsed.is_valid(const.value)
        found = () if inside else (const.value,)
        claim = f"flow {flow.name}: the literal {const} reaches {flow.sink}"
        self.links.append(Link(where, claim, "proven" if inside else "refuted", found))

    def failure(
        self,
        call: Call,
        contract: Contract,
        env: dict[Ref, _Held],
        where: str,
        path: _Path,
    ) -> None:
        """Check what a call does when its tool answers with an error."""
        if call.retry:
            verdict: Verdict = "proven" if contract.idempotent else "refuted"
            assumed = retry_assumptions(contract) if contract.idempotent else ()
            self.links.append(Link(where, f"{call.tool} may be retried", verdict, (), assumed))
        if call.on_error is not None:
            # The result is never bound on this path, so the block reads what the call read.
            self.block(call.on_error, env, _join(where, f"error:{call.tool}"), path)
        elif not call.retry and self.errors:
            self.gaps.append(Gap(where, f"an error of {call.tool}"))

    def record(
        self,
        call: Call,
        contract: Contract,
        bound: dict[str, _Held],
        where: str,
        env: dict[Ref, _Held],
    ) -> None:
        """Add the link of a call's argument record inside the tool's input set.

        The record is asked as the product of each argument's set, which holds every record the
        call can make, so a proof over it stands. Two arguments read from one value may move
        together, and a field's set may admit what its record refuses, so the product admits
        records no call makes: it refutes only where each argument is a constant or a whole value
        the program holds, no two the same, and otherwise a record refutes only where valgebra
        proves the values the program holds carry it, jointly.
        """
        record = record_of({name: held.values for name, held in bound.items()}, closed=True)
        claim = f"the arguments of {call.tool} <= its input schema"
        verdict = _relation(record, contract.accepts)
        found: tuple[Any, ...] = ()
        if verdict != "proven":
            refs = dict(call.bindings)
            choices = [self.candidates(Ref(name), held)[:3] for name, held in bound.items()]
            for values in itertools.islice(itertools.product(*choices), 64):
                value = dict(zip(bound, values, strict=True))
                if not contract.accepts.is_valid(value) and self.jointly(env, refs, value):
                    found = (value,)
                    break
            if not found and verdict == "refuted" and not _apart(env, refs.values()):
                verdict = "undecided"
        assumed = tuple(dict.fromkeys(a for held in bound.values() for a in self.assumption(held)))
        self.links.append(Link(where, claim, "refuted" if found else verdict, found, assumed))

    def jointly(
        self, env: dict[Ref, _Held], refs: Mapping[str, Ref | Const], record: Mapping[str, Any]
    ) -> bool:
        """Return whether valgebra proves the values the program holds carry ``record`` jointly.

        Arguments read from one value must be carried by one value of its belief; arguments
        read from distinct values, and constants, are apart.
        """
        held: dict[str, list[tuple[tuple[str, ...], Any]]] = {}
        for name, ref in refs.items():
            if isinstance(ref, Ref):
                held.setdefault(ref.variable, []).append((ref.path, record[name]))
        return all(self.member(env, variable, read) for variable, read in held.items())

    def inclusion(
        self,
        where: str,
        claim: str,
        ref: Ref,
        held: _Held,
        target: Validator[Any],
        env: dict[Ref, _Held],
    ) -> None:
        """Add the link of ``ref`` inside ``target``, with a value outside it where one is found.

        A field's set is what its record's schema declares, which another keyword may narrow,
        so a link that set leaves open is asked of the root: the field lies inside ``target``
        on every value of the root carrying it, the inclusion that types a selected field
        (Castagna 2023, §4.2). The root is asked through its belief, the program's tests on
        the way included (Bertoli et al. 2006, Def. 3.4), so valgebra's answer is exact, and a
        value drawn for the reference refutes only where valgebra proves a value of that
        belief carries it there.
        """
        answer = held.values.relation_to(target)
        found: tuple[Any, ...] = ()
        if answer != "subset":
            # Realize a witness first: a wrong program is refuted on one question of valgebra,
            # and the root is asked only where none is, the case where it may prove the link.
            outside = [v for v in self.candidates(ref, held) if not target.is_valid(v)][:8]
            found = self.realized(env, ref, outside)
            if not found:
                whole = None if ref.path else answer
                answer = self.asked(env, ref.variable, within(ref.path, target), whole)
        verdict = "refuted" if found else _VERDICTS[answer]
        self.links.append(Link(where, claim, verdict, found, self.assumption(held)))

    def assumption(self, held: _Held) -> tuple[str, ...]:
        """Return what a held value rests on: its tool's output schema inferred, or read closed.

        A field's set is the one its record declares, but a link it leaves open is asked of the
        whole result, so every value a tool returns rests on how its whole result is read.
        """
        contract = self.contracts.get(held.origin.tool)
        if contract is None:
            return ()
        named = []
        if contract.inferred:
            named.append(f"the output of {contract.name} as observed")
        if contract.declared is not None:
            named.append(f"the output of {contract.name} read closed")
        return tuple(named)

    def resolve(self, env: dict[Ref, _Held], ref: Ref) -> _Read:
        """Read ``ref`` from the nearest value the program holds above it, a field at a time.

        Selection requires the selected field to be defined (Castagna 2023, §3.2.2): a field is
        read where valgebra proves every value held for its record an object holding the key,
        as a test showing the key narrows the record to (Bertoli et al. 2006, Def. 3.4), and
        otherwise where it proves every value of the root's belief carries the path. The field
        lies in what its record's schema declares of it,
        `valgebra_planning.contracts.field_slot`.
        """
        if ref in env:
            return _Read(env[ref])
        for depth in range(len(ref.path) - 1, -1, -1):
            held = env.get(Ref(ref.variable, ref.path[:depth]))
            if held is None:
                continue
            for at in range(depth, len(ref.path)):
                key = ref.path[at]
                answer = held.values.relation_to(having(key))
                if answer != "subset":
                    known = None if at else answer
                    answer = self.present(env, ref.variable, ref.path[: at + 1], known)
                if answer != "subset":
                    return _Read(None, answer, held, at)
                slot = self.field(held, key)
                held = _Held(slot.values, slot)
            return _Read(held)
        return _Read(None, "unbound")

    def field(self, held: _Held, key: str) -> Slot:
        """Return the slot of field ``key`` of the record ``held``, as far as valgebra proves it.

        The walk reads the record's schema itself, so what it reads is kept only where valgebra
        proves the record's values carry the key nowhere outside it; a reading it does not
        prove, or one the companion refuses apart from its resource, takes any value.
        """
        try:
            slot = field_slot(held.origin, key)
        except Unsupported:
            path = (*held.origin.path, key)
            return Slot(
                held.origin.tool,
                path,
                schema=True,
                values=anything,
                required=True,
                root=held.origin.root,
                asserted=held.origin.asserted,
            )
        if held.values.relation_to(within((key,), slot.values)) != "subset":
            return unread(slot)
        return slot

    def present(
        self, env: dict[Ref, _Held], variable: str, path: tuple[str, ...], answer: str | None
    ) -> str:
        """Return valgebra's answer on every value of the root carrying ``path``.

        A record's declared set may admit a value lacking the key that another keyword of the
        root requires, so presence the record's set leaves open is asked of the root's belief
        (Bertoli et al. 2006, Def. 3.4). ``answer`` is valgebra's on the root's own set, where
        it was asked.
        """
        return self.asked(env, variable, selecting(path, anything), answer)

    def asked(
        self,
        env: Mapping[Ref, _Held],
        variable: str,
        question: Validator[Any],
        answer: str | None,
        now: Mapping[Ref, _Held] | None = None,
    ) -> str:
        """Return valgebra's answer on whether every value of ``variable``'s belief lies inside.

        The value's own set is asked first, ``answer`` where it was; where that leaves the
        question open and a test narrowed a field of the value, its belief is, the set of
        values every test on the way admits (Bertoli et al. 2006, Def. 3.4). A value of the
        belief outside ``question`` is one the program may hold here, so a refutation is exact.

        A loop's item lies in the array's declared `items`, which another keyword of the array
        may narrow, so a question its set leaves open is asked of the value holding the array:
        every item of the array the item's belief admits lies inside, the inclusion that types
        a selected field (Castagna 2023, §4.2) with the array's items for the field. The
        value holding the array is read as the program holds it ``now``, `_holder`; where that
        is only what it held when the loop began, a refutation is undecided.
        """
        held = env.get(Ref(variable))
        if held is None:
            return "undecided"
        if answer is None:
            answer = held.values.relation_to(question)
        tested = _tested(env, variable)
        if answer != "subset" and tested:
            answer = self.belief(env, variable).relation_to(question)
        if answer == "subset" or held.loop is None:
            return answer
        loop, now = held.loop, env if now is None else now
        holder, exact = _holder(now, loop)
        if tested or held.values is not held.origin.values:
            question = union(complement(self.belief(env, variable)), question)
        every = within(loop.array.path, union(complement(ARRAY), Validator([question])))
        answer = self.asked(holder, loop.array.variable, every, None, now)
        return "undecided" if answer == "not_subset" and not exact else answer

    def belief(self, env: Mapping[Ref, _Held], variable: str) -> Validator[Any]:
        """Return the values ``variable`` may hold here: its set, met with each field tested.

        A test narrows the reference it reads, a field of the value among them (Bertoli et al.
        2006, Def. 3.4), so a value the program may hold here carries each field a test
        narrowed inside that field's narrowed set.
        """
        meet = env[Ref(variable)].values
        for path, values in _tested(env, variable):
            meet = intersection(meet, selecting(path, values))
        return meet

    def lacking(self, env: dict[Ref, _Held], ref: Ref, read: _Read) -> tuple[Any, ...]:
        """Return a value of the record ``read`` stopped at, lacking the next key of ``ref``.

        A value drawn for the record is a witness only where valgebra proves a value of the
        root's belief carries it there: the record's declared set holds every value the record
        takes, as a belief holds every state possible (Bertoli et al. 2006, §3), and, unlike a
        belief, may admit one no member carries.
        """
        if read.record is None:
            return ()
        key, path = ref.path[read.at], ref.path[: read.at]
        root = env.get(Ref(ref.variable))
        building = root is not None and root.loop is not None
        seen: list[str] = []
        for value in self.candidates(Ref(ref.variable, path), read.record):
            if not isinstance(value, dict):
                continue
            bare = {name: item for name, item in value.items() if name != key}
            if not read.record.values.is_valid(bare) or _text(bare) in seen:
                continue
            seen.append(_text(bare))
            if self.member(env, ref.variable, [(path, bare)]):
                return (bare,)
            if building and len(seen) == _BUILT:
                break
        return ()

    def realized(self, env: dict[Ref, _Held], ref: Ref, values: Sequence[Any]) -> tuple[Any, ...]:
        """Return one of ``values`` valgebra proves a value of the root's belief holds there."""
        for value in values:
            if self.member(env, ref.variable, [(ref.path, value)]):
                return (value,)
        return ()

    def member(
        self,
        env: Mapping[Ref, _Held],
        variable: str,
        placed: Sequence[tuple[tuple[str, ...], Any]],
        now: Mapping[Ref, _Held] | None = None,
    ) -> bool:
        """Return whether valgebra proves a value of ``variable``'s belief holds each one placed.

        Each value is placed at its path, the empty path being the whole value; the value
        also carries every field a test on the way narrowed inside its narrowed set. A loop's
        item's belief may admit a value no item of the array takes, so for an item the value
        is built, `_Walk.built`.
        """
        held = env.get(Ref(variable))
        if held is None:
            return False
        if held.loop is not None or now is not None:
            return self.built(env, variable, placed, env if now is None else now)
        belief = self.belief(env, variable)
        if [path for path, _ in placed] == [()]:
            return belief.is_valid(placed[0][1])
        for path, value in placed:
            belief = intersection(belief, selecting(path, listed([value])))
        return belief.relation_to(nothing) == "not_subset"

    def built(
        self,
        env: Mapping[Ref, _Held],
        variable: str,
        placed: Sequence[tuple[tuple[str, ...], Any]],
        now: Mapping[Ref, _Held],
    ) -> bool:
        """Return whether valgebra proves a value of ``variable``'s belief holds each one placed.

        For a loop's item, valgebra proves the values of its belief holding each one placed
        not empty, then a value of its holder's belief (Bertoli et al. 2006, Def. 3.4) holding a
        non-empty array of them at the array's path, a level at a time up to a value no loop
        reads. There a value given or drawn for it serves as a template: valgebra proves it,
        with any such array in place of its own, inside the belief, or the values holding one
        not empty. Where the program holds the holder ``now`` only as it did when the loop
        began, `_holder`, nothing is proven.
        """
        held = env[Ref(variable)]
        loop = held.loop
        holder, exact = (env, True) if loop is None else _holder(now, loop)
        if not exact:
            return False
        belief = self.belief(env, variable)
        if loop is None and any(
            _template(base, path, among.values).relation_to(belief) == "subset"
            for path, among in placed
            for base in self.bases(variable, held, belief)
        ):
            return True
        found = belief
        for path, value in placed:
            inner = value.values if isinstance(value, _Among) else listed([value])
            found = intersection(found, selecting(path, inner))
        if found.relation_to(nothing) != "not_subset":
            return False
        if loop is None:
            return True
        return self.member(holder, loop.array.variable, [(loop.array.path, _Among(found))], now)

    def bases(self, variable: str, held: _Held, belief: Validator[Any]) -> list[Any]:
        """Return a few values to template one of ``variable``'s on, those in its belief first."""
        drawn = self.draws(variable, held)
        inside = [value for value in drawn if belief.is_valid(value)]
        return (inside or drawn)[:_BUILT]

    def draws(self, variable: str, held: _Held) -> list[Any]:
        """Return values to template one of ``variable``'s on: the examples given, then draws.

        They are drawn from the schema of the slot ``held`` comes from, once for each set and
        apart from the draws a counterexample is searched in. None need lie in the set: a
        template replaces the array, and what it proves is valgebra's.
        """
        known = self.drawn.get(id(held))
        if known is None:
            if self.builder is None:
                self.builder = random.Random(f"items {self.seed}")
            try:
                drawn = Sampler(held.origin.root, self.builder).draw(held.origin.schema, DRAWS)
            except (Unsupported, RecursionError):
                drawn = []
            known = self.drawn[id(held)] = (held, [*self.examples.get(variable, ()), *drawn])
        return known[1]

    def candidates(self, ref: Ref, held: _Held) -> list[Any]:
        """Return values the reference may hold there: the examples given, then draws."""
        given = [v for v in self.examples.get(str(ref), ()) if held.values.is_valid(v)]
        try:
            drawn = Sampler(held.origin.root, self.rng).draw(held.origin.schema, DRAWS)
        except (Unsupported, RecursionError):
            drawn = []
        return given + [v for v in drawn if held.values.is_valid(v)]

    def read_fresh(self, call: Call, where: str, path: _Path) -> None:
        """Add a link for each argument the task reads fresh: no call that may act came between.

        An action changes the world a reading reports, and whether its effect holds is read in
        the world again, not off what was read before it (Mansoor et al. 2026, §4.1). So a value
        read before a call that may act, one whose annotations do not say it is read-only, is no
        reading of the world after it, and a constant is no reading at all. Which calls may act
        is read off their annotations, a hint their server gives, so a link proven over them
        lists that assumption unless every server is trusted.
        """
        given = dict(call.bindings)
        for tool, argument in self.task.fresh:
            if tool != call.tool or argument not in given:
                continue
            ref = given[argument]
            claim = f"{tool}.{argument} is read after every call before it that may act"
            fresh = isinstance(ref, Ref) and ref.variable not in path.stale
            hinted = fresh and not all(c.trusted for c in self.contracts.values())
            assumed = ("the read-only annotations of the calls before it",) if hinted else ()
            self.links.append(Link(where, claim, "proven" if fresh else "refuted", (), assumed))

    def fixed(self, call: Call, env: dict[Ref, _Held], where: str) -> None:
        """Add a link for each argument the task fixes: the call passes the task's constant.

        A task's intent fixes the value, as the action a confirmation asks about, so a program
        passing another, a literal or a held value that may differ, is refuted there.
        """
        given = dict(call.bindings)
        for tool, argument, value in self.task.constants:
            if tool != call.tool or argument not in given:
                continue
            ref = given[argument]
            claim = f"{call.tool}.{argument} is the task's {Const.of(value)}"
            if isinstance(ref, Const):
                same = ref == Const.of(value)
                found = () if same else (ref.value,)
                self.links.append(Link(where, claim, "proven" if same else "refuted", found))
                continue
            held = self.held(env, ref, where)
            if held is not None:
                self.inclusion(where, claim, ref, held, listed([value]), env)

    def constant(self, where: str, const: Const, slot: Slot) -> None:
        """Add the link of a constant inside the argument it fills, decided by membership."""
        inside = slot.values.is_valid(const.value)
        found = () if inside else (const.value,)
        self.links.append(
            Link(where, f"{const} <= {slot.name}", "proven" if inside else "refuted", found)
        )

    def held(self, env: dict[Ref, _Held], ref: Ref, where: str) -> _Held | None:
        """Return the set ``ref`` lies in, or None with the link it fails added.

        A reference no value of the program reaches, or a field valgebra proves absent from
        some value, or one a member of the root is found lacking, is refuted; a field whose
        presence valgebra leaves undecided is undecided.
        """
        read = self.resolve(env, ref)
        if read.held is not None:
            return read.held
        claim = f"{ref} is a value the program holds"
        lacking = self.lacking(env, ref, read)
        refuted = bool(lacking) or read.answer in ("unbound", "not_subset")
        self.links.append(Link(where, claim, "refuted" if refuted else "undecided", lacking))
        return None

    def argument(self, tool: str, name: str, where: str) -> Slot | None:
        contract = self.contracts.get(tool)
        try:
            return None if contract is None else contract.argument(name)
        except KeyError:
            self.links.append(Link(where, f"{tool}.{name} is an argument", "refuted"))
            return None


@dataclass(frozen=True, slots=True)
class _Read:
    """A reference read: its set, or valgebra's answer where it stopped, and the record there."""

    held: _Held | None
    answer: str = "subset"
    record: _Held | None = None
    at: int = 0


def _apart(env: dict[Ref, _Held], refs: Iterable[Ref | Const]) -> bool:
    """Return whether each argument is a constant or a whole result or input, no two the same.

    Such values are read in the sets their schemas compile to, narrowed by the tests on them,
    and are independent, so their product is every record the call can make. A loop's item is
    read in the `items` the walk read, which may hold more than the array's items, and a value
    whose field a test narrowed holds more than its belief.
    """
    variables = []
    for ref in refs:
        if isinstance(ref, Const):
            continue
        held = env.get(Ref(ref.variable))
        if ref.path or held is None or held.origin.path or _tested(env, ref.variable):
            return False
        variables.append(ref.variable)
    return len(variables) == len(set(variables))


def _holder(now: Mapping[Ref, _Held], loop: _Loop) -> tuple[Mapping[Ref, _Held], bool]:
    """Return what holds the value the array of ``loop`` is read from, and whether exactly.

    Where the program ``now`` holds that value in the set it held when the loop began, it holds
    it with every test the body made on its fields, exactly. Where the item took its name, the
    body reaches nothing of it, so what the loop began with is exact. Otherwise the body rebound
    it or tested it whole, and what the loop began with holds every value it may hold, but may
    hold one the body excluded.
    """
    variable = loop.array.variable
    if now.get(Ref(variable)) is loop.env.get(Ref(variable)):
        return now, True
    return loop.env, variable == loop.item


@dataclass(frozen=True, slots=True)
class _Among:
    """The non-empty arrays of ``items``' values, placed where a loop reads its item."""

    items: Validator[Any]

    @property
    def values(self) -> Validator[Any]:
        return intersection(Validator([self.items]), complement(listed([[]])))


def _template(base: Any, path: tuple[str, ...], values: Validator[Any]) -> Validator[Any]:
    """Return the values equal to ``base`` but at ``path``, which lies in ``values`` there."""
    if not path:
        return values
    record = base if isinstance(base, dict) else {}
    fields = {key: listed([value]) for key, value in record.items() if key != path[0]}
    fields[path[0]] = _template(record.get(path[0]), path[1:], values)
    return record_of(fields, closed=True)


def _text(value: Any) -> str:
    """Return ``value`` as JSON text, so two values read equal only where JSON tells them so."""
    return json.dumps(value, sort_keys=True, default=repr)


def _tested(
    env: Mapping[Ref, _Held], variable: str
) -> list[tuple[tuple[str, ...], Validator[Any]]]:
    """Return each field of ``variable`` a test narrowed, with the set it narrowed it to.

    A test's branch holds the tested reference in a set of its own, met with what it tests; a
    call's result and a task input hold each slot in the set the slot compiles to, which the
    value's own set already implies.
    """
    return [
        (ref.path, held.values)
        for ref, held in env.items()
        if ref.variable == variable and ref.path and held.values is not held.origin.values
    ]


def _cells(values: Validator[Any], guards: Sequence[Validator[Any]]) -> list[Validator[Any]]:
    """Return ``values`` split by each guard and its complement, each part valgebra leaves open.

    A part valgebra proves empty holds no result, so no case is walked for it.
    """
    cells = [values]
    for guard in guards:
        cells = [
            part
            for cell in cells
            for part in (intersection(cell, guard), intersection(cell, complement(guard)))
            if part.relation_to(nothing) != "subset"
        ]
    return cells


def _join(where: str, branch: str) -> str:
    return f"{where}.{branch}" if where else branch


#: The states of every policy a path may be in, one set per policy.
_States = tuple[frozenset[str], ...]


_Acted = frozenset[tuple[str, frozenset[tuple[str, Ref | Const]]]]


def _acts(block: Block, contracts: Mapping[str, Contract]) -> bool:
    """Return whether a call ``block`` may make, at any depth, is one that may act.

    A call may act unless its tool's annotations say it is read-only; a tool no contract names
    may act.
    """
    for call in block.calls:
        contract = contracts.get(call.tool)
        if contract is None or "readOnlyHint" not in contract.hints:
            return True
        if call.on_error is not None and _acts(call.on_error, contracts):
            return True
    end = block.end
    if isinstance(end, Branch):
        return _acts(end.then, contracts) or _acts(end.otherwise, contracts)
    if isinstance(end, For):
        return _acts(end.body, contracts) or _acts(end.after, contracts)
    return False


def _unread(acted: _Acted, result: str) -> _Acted:
    """Return the calls that may have acted, less those given a value ``result`` now rebinds."""
    return frozenset(
        (tool, given)
        for tool, given in acted
        if all(not isinstance(r, Ref) or r.variable != result for _, r in given)
    )


@dataclass(frozen=True, slots=True)
class _Path:
    """What a path carries beside its values: policy states, and the sources it tested."""

    states: _States
    tested: frozenset[str] = frozenset()
    #: The calls that answered with an error on the way here and may have acted: each tool,
    #: and the values it was given.
    acted: _Acted = frozenset()
    #: The values held before the last call on the way here that may act, by variable.
    stale: frozenset[str] = frozenset()


#: Whether a path may reach a bad state, read as a verdict on the policy holding.
_OBEYED: dict[str, Verdict] = {"never": "proven", "surely": "refuted", "possibly": "undecided"}

#: valgebra's three answers, read as verdicts on an inclusion.
_VERDICTS: dict[str, Verdict] = {
    "subset": "proven",
    "not_subset": "refuted",
    "undecided": "undecided",
}


def _relation(subject: Validator[Any], target: Validator[Any]) -> Verdict:
    return _VERDICTS[subject.relation_to(target)]


def verify(
    program: Block,
    contracts: Mapping[str, Contract],
    task: Task,
    *,
    examples: Mapping[str, Sequence[Any]] | None = None,
    seed: int = 0,
    errors: bool = False,
    policies: Sequence[Policy] = (),
    flows: Sequence[Flow] = (),
) -> Verification:
    """Ask every link of ``program`` of valgebra, for ``task`` over ``contracts``.

    ``examples`` maps a reference, written as the program writes it, to values it may hold,
    tried first in search of a counterexample. With ``errors``, any tool may answer with an
    error, and a call neither retried nor given an error block leaves that path uncovered.
    A retry is accepted only for a tool that is read-only or idempotent, since calling twice
    must act as calling once; it ends under fairness, the strong cyclic reading (Cimatti et al.
    2003, §2.3). Each policy
    adds a link at the end of every path: proven where no bad state can be reached there,
    refuted where one surely is, undecided where one possibly is. Each flow rule adds a link
    for every value from one of its sources bound to its sink, and, where implicit flows are
    read, for every call to the sink under a test on such a value. Raises `ValueError` for a
    task naming an input as a tool, whose source no name could tell apart.
    """
    unclashed(task, contracts)
    walk = _Walk(
        contracts, task, examples or {}, random.Random(seed), errors, tuple(policies), tuple(flows)
    )
    walk.seed = seed
    env = {
        Ref(var, slot.path): _Held(slot.values, slot)
        for var, slots in task.held.items()
        for slot in slots
    }
    walk.block(program, env, "", _Path(tuple(frozenset({p.initial}) for p in policies)))
    return Verification(tuple(walk.links), tuple(walk.gaps))
