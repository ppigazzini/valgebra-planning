"""Every link of a program, asked of valgebra alone.

A link is an inclusion a program rests on: a reference inside the argument it fills, a call's
whole argument record inside the tool's input set where the arguments are related, and the
returned reference inside the goal. Each is asked of valgebra with the set the reference lies in
at that point of the program, narrowed by every test on the way there, and nothing else, not the
cells, not the search: the belief execution along a path (Bertoli et al. 2006, Def. 3.4), each
reference's set read apart from the others'. A program whose every link is proven and on which
no path stops is a strong solution, applicable at each call and inside the goal at the end of
each path (Bertoli et al. 2006, Thm. 3.5). The theorem is proven of finite conditional plans,
which hold no loop (Bertoli et al. 2006, Def. 2.8); a loop over an array, which holds finitely
many items, unrolls each run to one such plan, and the walk reads its body once for every item
(`_Walk.loop`), a belief holding each unrolling's, so the reading holds of each: a step of this
package's own. A refuted link carries a value outside the inclusion
where one is found among the examples given and the values drawn from the reference's own
schema. A value found that way refutes an undecided link too, since a concrete member is a
proof.
"""

from __future__ import annotations

import copy
import itertools
import json
import math
import random
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal, NamedTuple

from valgebra_planning._valgebra import (
    ARRAY,
    Sampler,
    Unsupported,
    Validator,
    anything,
    compile_schema,
    complement,
    intersection,
    nothing,
    union,
)
from valgebra_planning.contracts import (
    Contract,
    Postcondition,
    Slot,
    Task,
    apart,
    field_slot,
    having,
    inside,
    item_slots,
    listed,
    placed,
    record_of,
    selecting,
    sides,
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
#: No value is found.
_NONE: Any = object()
#: How many references a walk reads values under are kept (`_ref`).
_REFS = 1024


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
    #: Where each return some execution reaches stands: one under a test valgebra proves no
    #: value meets is left out.
    returns: tuple[str, ...] = ()

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

        Every link is proven, some execution returns, and each stop is on a value the task
        tolerates there: a weak solution, one reaching the goal on some execution (Cimatti et al.
        2003, Def. 2.10), whose every other end the task accepts. A return no execution reaches
        is proven of no value and counts for nothing (`returns`).
        """
        return self.verified and bool(self.returns) and all(gap.tolerated for gap in self.gaps)

    def count(self, verdict: Verdict) -> int:
        """Return how many links read ``verdict``."""
        return sum(link.verdict == verdict for link in self.links)


class _Held(NamedTuple):
    """The set a reference lies in at a point of a program, and the slot it came from."""

    values: Validator[Any]
    origin: Slot
    #: Where the value is a loop's item: the array it is read from.
    loop: _Loop | None = None
    #: Whether the value is a call's result narrowed, to one case of a split on a policy's
    #: result guard or by a postcondition, so a branch none of its values reaches is no path.
    case: bool = False
    #: The postconditions a call's result was narrowed by, each link over it naming them.
    assumed: tuple[str, ...] = ()
    #: The value's version: each call's result, each loop's item and each task input is one, so
    #: two references read one value where their roots hold one version, on the path walked.
    version: int = 0
    #: The version of the call that returned the value, a loop's item's being its array's, an
    #: input's its own: what a flow rule reading when and for what a value was returned reads.
    returned: int = 0


@lru_cache(maxsize=_REFS)
def _ref(variable: str, path: tuple[str, ...] = ()) -> Ref:
    """Return the reference a walk reads a value under, one kept for each name and path."""
    return Ref(variable, path)


@dataclass(frozen=True, slots=True, eq=False)
class _Attempt:
    """A call on the path that answered with an error and may have acted.

    ``keys`` is what each argument read, as a key compares it (`_Walk.keyed`), and ``record``
    the set each held: a later call to ``tool`` may be made on the same values, a retry of it,
    unless at some argument both take the two sets are disjoint.
    """

    tool: str
    keys: tuple[tuple[str, _Key], ...]
    record: tuple[tuple[str, Validator[Any]], ...]


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
    #: What every other witness is drawn from: the state the first draw leaves the seed's stream
    #: in, and the stream, made at the second draw. Most programs need neither.
    state: Any = None
    rng: random.Random | None = None
    seed: int = 0
    postconditions: tuple[Postcondition, ...] = ()
    #: The arguments each call was given, by its result's version, each a literal or the
    #: version and path of the value it read, which a flow rule's ``key`` reads of a source call.
    given: dict[int, tuple[tuple[str, _Key], ...]] = field(default_factory=dict)
    #: How many versions were given out: a version names one value on every path.
    versions: int = 0
    #: Each task input's version, to the input's name, and each loop item's version, to the
    #: version of the call that returned its array.
    inputs: dict[int, str] = field(default_factory=dict)
    items: dict[int, int] = field(default_factory=dict)
    #: Where each return some execution reaches stands (`Verification.returns`).
    returns: list[str] = field(default_factory=list)
    #: Each state that lapses read by a call so far, in order: what a loop's body added to it.
    awaits: list[_Awaited] = field(default_factory=list)

    @property
    def lapsing(self) -> tuple[Policy, ...]:
        """Return the policies with lapses, whose states whatever time passed are walked too."""
        return tuple(policy for policy in self.policies if policy.lapses)

    def initial(self) -> _States:
        """Return the states a program starts in."""
        return tuple(frozenset({p.initial}) for p in (*self.policies, *self.lapsing))

    def stepped(
        self,
        states: _States,
        contract: Contract,
        record: Validator[Any],
        result: Validator[Any] | None = None,
    ) -> _States:
        """Return the states a call leaves, each policy's and each lapsing one's whatever time."""
        every = len(self.policies)
        return tuple(
            policy.step(held, contract.name, record, contract.hints, result, late=at >= every)
            for at, (policy, held) in enumerate(
                zip((*self.policies, *self.lapsing), states, strict=True)
            )
        )

    def awaiting(
        self, states: _States, contract: Contract, record: Validator[Any]
    ) -> frozenset[_Awaited]:
        """Return each state that lapses held where a call its policy reads, or its result, is."""
        found: list[_Awaited] = []
        for policy, held in zip(self.policies, states, strict=False):
            if not policy.lapses:
                continue
            reading = [t for t in policy.transitions if t.reads(contract.name, contract.hints)]
            sent = policy.sent(held, contract.name, record, contract.hints)
            found += [
                (policy.name, what, state)
                for what, reached, returned in (
                    (contract.name, held, False),
                    (f"the result of {contract.name}", sent, True),
                )
                for state in sorted(reached)
                if policy.lapse(state) is not None
                and any((t.result is not None) == returned for t in reading)
            ]
        self.awaits += found
        return frozenset(found)

    def version(self) -> int:
        """Return a version no value has yet.

        A rule reading when and for what a value was returned reads facts of the value, as CaMeL
        tags each value with its provenance (Debenedetti et al. 2025, §5.3), and a name may hold
        another value on another path. So each call's result, loop item and task input is a
        version of its own, held beside the value on the path that bound it, the belief that
        path walks (Bertoli et al. 2006, Def. 3.4): a failed call binds none, and a branch's or a
        loop's body's bindings stay on their path.
        """
        self.versions += 1
        return self.versions

    def lineage(self, returned: int) -> set[str]:
        """Return the task inputs the value a version names was read from.

        An input's version is its own; a call's result was read from every input its arguments
        were, through the versions they read (`_Walk.given`), and a loop's item from what its
        array was.
        """
        seen, inputs, pending = set(), set(), [returned]
        while pending:
            version = pending.pop()
            if version in seen:
                continue
            seen.add(version)
            if version in self.inputs:
                inputs.add(self.inputs[version])
            if version in self.items:
                pending.append(self.items[version])
            for _, key in self.given.get(version, ()):
                if isinstance(key, tuple):
                    pending.append(key[0])
        return inputs

    def keyed(self, env: Mapping[Ref, _Held], ref: Ref | Const | None) -> _Key:
        """Return what a binding reads, as a key compares it (`_Key`).

        One version read at one path is one value, and so is one literal: the slice of the trace
        a key's value names (Roşu and Chen 2012, Def. 11).
        """
        if ref is None or isinstance(ref, Const):
            return ref
        root = env.get(_ref(ref.variable))
        return None if root is None else (root.version, ref.path)

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
            self.returning(end, env, where, path)
        elif isinstance(end, Stop):
            # A stop hands back the value it names, which the run reads as it reads a return's.
            self.held(env, end.ref, where)
            self.gaps.append(Gap(where, str(end.ref), self.tolerated(env, end.ref)))
            self.obeyed(where, path)
            self.ended(path)
        elif isinstance(end, Next):
            if self.ends is not None:
                self.ends.append(path.states)
        elif isinstance(end, For):
            self.loop(end, env, where, path)
        else:
            self.branch(end, env, where, path)

    def returning(self, end: Return, env: dict[Ref, _Held], where: str, path: _Path) -> None:
        """Add a return's links: its value held and inside the goal; keep it where it is reached."""
        held = self.held(env, end.ref, where)
        if held is not None:
            claim = f"return {end.ref} <= goal"
            self.inclusion(where, claim, end.ref, held, self.task.goal, env)
            if self.reaches(env):
                self.returns.append(where)
        if self.task.reads:
            root = env.get(_ref(end.ref.variable))
            read = set() if root is None else self.lineage(root.returned)
            for var in sorted(self.task.reads):
                verdict: Verdict = "proven" if var in read else "refuted"
                claim = f"return {end.ref} is read from {var}"
                self.links.append(Link(where, claim, verdict))
        self.obeyed(where, path)
        self.ended(path)

    def tolerated(self, env: dict[Ref, _Held], ref: Ref) -> bool:
        """Return whether the task tolerates a stop on ``ref`` here.

        It does where valgebra proves every whole value the stopped reference's root may hold
        here, met with each field tested on the way, inside the set the task gives for the tool
        or input it came from. A loop's item is read as no whole value of its tool: a stop on it
        is never tolerated.
        """
        root = env.get(_ref(ref.variable))
        if root is None or root.loop is not None:
            return False
        allowed = dict(self.task.stops).get(root.origin.tool)
        if allowed is None:
            return False
        return self.belief(env, ref.variable).relation_to(allowed) == "subset"

    def loop(self, end: For, env: dict[Ref, _Held], where: str, path: _Path) -> None:
        """Walk a loop's body for an arbitrary item, the policies at a fixpoint; then what follows.

        No value crosses iterations, so one walk over the item's set holds for every item. The
        body may run any number of times, so the states a policy may be in when it starts are
        taken to a fixpoint, the states being finite. How many times it runs is the array's, so
        its calls run under a test on the array's source (Debenedetti et al. 2025, §5.4). What
        follows runs only where no iteration ended the run, the returns in the body being terminal
        states of the program (Cimatti et al. 2003, Def. 2.10), so it runs under every test a path
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
        inner = _unbound(env, end.item)
        # The item is a value of its own, returned by the call that returned its array.
        array = env.get(_ref(end.ref.variable))
        inner[_ref(end.item)] = _Held(
            item.values,
            item,
            _Loop(end.ref, end.item, env),
            version=self.version(),
            returned=held.returned if array is None else array.returned,
        )
        self.items[inner[_ref(end.item)].version] = inner[_ref(end.item)].returned
        inside, tested = _join(where, f"for:{end.item}"), path.tested | {held.origin.tool}
        if _acts(end.body, self.contracts):
            stale = path.stale | {held.returned for ref, held in inner.items() if not ref.path}
            path = path._replace(stale=stale, last=None, observed=frozenset())
        else:
            path = _rebound(path, end.item)
        listed = _withdrawn(
            path, self.flows, inner, lambda flow: _makes(end.body, self.contracts, flow.withdraws)
        )
        path = path._replace(withdrawn=listed)
        states, holding, read = path.states, path.states, len(self.awaits)
        if self.policies:
            # The states once the body ran at least once, to a fixpoint from the first walk's.
            holding = tuple(frozenset() for _ in states)
            ran = states
            while True:
                ends, _ = self.iterate(
                    end.body, inner, inside, path._replace(states=ran, tested=tested), silent=True
                )
                grown = tuple(s.union(*(e[i] for e in ends)) for i, s in enumerate(holding))
                if grown == holding:
                    break
                holding = ran = grown
        reach = tuple(before | then for before, then in zip(states, holding, strict=True))
        _, exits = self.iterate(
            end.body, inner, inside, path._replace(states=reach, tested=tested), silent=False
        )
        after = path.tested.union(*exits)
        path = path._replace(awaited=path.awaited.union(self.awaits[read:]))
        sides = self.sides(held, states, holding)
        for label, at in sides:
            here = where if len(sides) == 1 else _join(where, f"{label}:{end.item}")
            self.block(end.after, env, here, path._replace(states=at, tested=after))

    def sides(self, held: _Held, empty: _States, holding: _States) -> list[tuple[str, _States]]:
        """Return the sides of a loop what follows it is walked on, each with its states.

        A loop is a test on whether its array holds an item (Bertoli et al. 2006, Def. 2.8): the
        empty array's run leaves the states before the loop, and any other the states once its
        body ran. A side valgebra proves the array never takes is no execution, nor one no
        iteration ends; where the two sides hold the same states, one walk reads both.
        """
        if holding == empty:
            return [("empty", empty)]
        found = []
        if intersection(held.values, _EMPTY).relation_to(nothing) != "subset":
            found.append(("empty", empty))
        if any(holding) and intersection(held.values, _HOLDING).relation_to(nothing) != "subset":
            found.append(("items", holding))
        return found or [("empty", empty)]

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

    def obeyed(self, where: str, path: _Path) -> None:
        """Add a link per policy where a path ends: whether it may end in a bad or pending state.

        The walk reads no time, so a link proven with every lapse left out rests on the run's
        timing where it is not with each taken possibly, the reading of a finite word whose
        continuations' times decide the property (Bauer et al. 2011, Def. 4.14): it is proven
        under the assumption it names, that the run reaches each call the policy reads before the
        state it holds there lapses.
        """
        for at, policy in enumerate(self.policies):
            held = path.states[at]
            verdict = _OBEYED[policy.reach(held, ended=True)]
            hinted = policy.hints() and not all(c.trusted for c in self.contracts.values())
            assumed = [f"the annotation hints policy {policy.name} reads"] if hinted else []
            if policy.lapses and verdict == "proven":
                late = path.states[len(self.policies) + self.lapsing.index(policy)]
                if policy.reach(policy.due(late), ended=True) != "never":
                    assumed += _timing(policy, held, path.awaited)
            link = Link(where, f"policy {policy.name} holds", verdict, (), tuple(assumed))
            self.links.append(link)

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
        kept, left = (
            _Held(
                values,
                held.origin,
                held.loop,
                version=held.version,
                returned=held.returned,
            )
            for values in sides(held.values, tested)
        )
        # Which branch runs carries the tested value's influence: an implicit flow.
        tested = path._replace(tested=path.tested | {held.origin.tool})
        for side, narrowed, name in (
            (test.then, kept, "then"),
            (test.otherwise, left, "else"),
        ):
            branched = {**env, test.ref: narrowed}
            if not self.reached(branched, test.ref.variable):
                continue
            self.block(side, branched, _join(where, name), tested)

    def reaches(self, env: dict[Ref, _Held]) -> bool:
        """Return whether a path here is an execution: no value a test narrowed is proven none.

        A test narrows the value it reads (Bertoli et al. 2006, Def. 3.4), and a side valgebra
        proves no value of that belief meets is reached on no execution, so an end there is no
        terminal state a weak solution counts (Cimatti et al. 2003, Def. 2.10).
        """
        narrowed = {
            ref.variable for ref, held in env.items() if held.values is not held.origin.values
        }
        for variable in narrowed:
            root = env.get(_ref(variable))
            if root is None or self.holds(env, variable):
                continue
            if self.belief(env, variable).relation_to(nothing) == "subset":
                return False
        return True

    def holds(
        self,
        env: Mapping[Ref, _Held],
        variable: str,
        given: Sequence[tuple[tuple[str, ...], Any]] = (),
    ) -> bool:
        """Return whether a value found lies in ``variable``'s belief, each one given at its path.

        Each value the contract shows (`Contract.shown`) is tried with those given, and with a
        known member at each field a test narrowed, and lies in the belief where valgebra holds it
        in the value's own set and each such field in the set the tests left it (Bertoli et al.
        2006, Def. 3.4).
        """
        root = env[_ref(variable)]
        contract = self.contracts.get(root.origin.tool)
        if contract is None or root.origin.path:
            return False
        fields = [
            (ref.path, held)
            for ref, held in env.items()
            if ref.variable == variable and ref.path and held.values is not held.origin.values
        ]
        for value in contract.shown():
            found = value
            for path, one in given:
                found = placed(found, path, one)
            for path, held in fields:
                found = _meeting(found, path, held)
                if found is _NONE:
                    break
            if found is _NONE or any(_at(found, path) is not one for path, one in given):
                continue
            if root.values.is_valid(found):
                return True
        return False

    def reached(self, env: dict[Ref, _Held], variable: str) -> bool:
        """Return whether a branch is a path: not where a case's value is proven to miss it.

        A call's result narrowed to one case of a split on a policy's result guard reaches a
        branch only where some value of the case meets every test on the way; a case and a
        branch valgebra proves disjoint are no execution (Bertoli et al. 2006, Def. 3.4).
        """
        root = env.get(_ref(variable))
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
        cell; a cell valgebra proves empty is dropped. A result set valgebra proves empty has no
        cell, and is walked as one case, as a call no guard reads is.
        """
        contract = self.contracts.get(call.tool)
        if contract is None:
            self.links.append(Link(where, f"{call.tool} is a tool", "refuted"))
            return [(env, path)]
        self.flowing(call, env, where, path)
        version = self.version()
        if self.flows or self.task.reads:
            # A flow rule and a task's required inputs alone read what a call was given.
            keys = ((name, self.keyed(env, ref)) for name, ref in call.bindings)
            self.given[version] = tuple(keys)
        acted = path.acted
        failing = (
            self.repeated(call, contract, where, env, path) if acted or call.on_error else acted
        )
        states = path.states
        bound = self.bind(call, contract, env, where)
        self.fixed(call, env, where)
        self.read_fresh(call, env, where, path)
        if not contract.plain and len(bound) == len(call.bindings):
            self.record(call, contract, bound, where, env)
        record, after, awaited = self.sent(contract, bound, path)
        # The result is a new version, so no value held under its name before is made fresh, as
        # the error's block, which reads that value, reads it.
        stale = path.stale
        acts = "readOnlyHint" not in contract.hints
        if acts:
            # A call that may act may change what every value held so far read.
            stale |= {held.returned for ref, held in env.items() if not ref.path}
        # A call that may act and answered with an error left an effect no postcondition reads.
        unknown = None if acts else path.last
        observed = frozenset() if acts else _unnamed(path.observed, call.result)
        withdrawn = _withdrawn(
            path, self.flows, env, lambda flow: flow.withdraws(call.tool, contract.hints)
        )
        erred = path
        if call.on_error is not None:
            # A tool that answered with an error may still have acted, so its block reads both.
            either = tuple(before | then for before, then in zip(states, after, strict=True))
            erred = _Path(
                either, path.tested, failing, stale, unknown, observed, withdrawn, awaited
            )
        self.failure(call, contract, env, where, erred)
        observed = self.observing(call, env, path, observed, acts=acts)
        shown = self.shown(call, env, path)
        defined = _unbound(env, call.result)
        for slot in contract.results:
            defined[_ref(call.result, slot.path)] = _Held(
                slot.values, slot, version=version, returned=version
            )
        # An attempt is held by the values it was given, so a name bound again leaves it.
        left = acted
        last = _left(path.last, call, acts=acts)
        whole = (
            self.narrowed(defined, call, contract, shown)
            if shown
            else defined.get(_ref(call.result))
        )
        guards = [
            transition.result
            for policy in self.policies
            for transition in policy.transitions
            if transition.result is not None and transition.reads(call.tool, contract.hints)
        ]
        cells = [] if whole is None or not guards else _cells(whole.values, guards)
        made = _Path(after, path.tested, left, stale, last, observed, withdrawn, awaited)
        if whole is None or not cells:
            return [(defined, made)]
        cases = []
        for cell in cells:
            held = _Held(
                cell,
                whole.origin,
                case=True,
                assumed=whole.assumed,
                version=whole.version,
                returned=whole.returned,
            )
            narrowed = {**defined, _ref(call.result): held}
            stepped = self.stepped(states, contract, record, cell)
            cases.append((narrowed, made._replace(states=stepped)))
        return cases

    def sent(
        self, contract: Contract, bound: Mapping[str, _Held], path: _Path
    ) -> tuple[Validator[Any], _States, frozenset[_Awaited]]:
        """Return a call's record, the policy states it leaves, and the states awaited once sent.

        A policy alone reads the record, so with none it is not built.
        """
        if not self.policies:
            return nothing, path.states, path.awaited
        record = record_of({name: held.values for name, held in bound.items()}, closed=True)
        after = self.stepped(path.states, contract, record)
        return record, after, path.awaited | self.awaiting(path.states, contract, record)

    def repeated(
        self, call: Call, contract: Contract, where: str, env: dict[Ref, _Held], path: _Path
    ) -> _Acted:
        """Check a call against those that may have acted on this path; return what its block holds.

        A call to a tool that may have acted, on values that may be the ones it was given, is a
        retry written out, and holds as one: only for a read-only or idempotent tool, on its
        annotations unless they are trusted. The values are read as sets, not as the names
        holding them: a call is no retry only where at some argument valgebra proves the set it
        holds disjoint from the one the earlier call's held, so another name for the same value,
        or a value read again after the error, may repeat the call. It is no retry where a read
        a postcondition of that call's acting covers shows it did not act: valgebra proves every
        value the read may hold here, the tests on the way met, outside what the declaration
        says any effect leaves, so the belief holds no state where it acted (Bertoli et al.
        2006, §3), and the call is made once more, verified before it is retried (Mansoor et al.
        2026, §4.1). Only an error block holds a call that may have acted, so a path outside one
        asks nothing.
        """
        before = [done for done in path.acted if done.tool == call.tool]
        if not before and call.on_error is None:
            return path.acted
        attempt = self.attempt(call, env)
        unread = False
        for done in before:
            if _disjoint(done.record, attempt.record):
                continue
            post = self.unacted(done, env, path)
            if post is None:
                unread = True
                continue
            claim = f"{call.tool} is made again where {post.read} shows it did not act"
            assumed = (post.name, f"{post.read} reads an effect of {post.after} at once")
            self.links.append(Link(where, claim, "proven", (), assumed))
        if unread:
            verdict: Verdict = "proven" if contract.idempotent else "refuted"
            assumed = annotated(contract) if contract.idempotent else ()
            self.links.append(Link(where, f"{call.tool} may be retried", verdict, (), assumed))
        return path.acted | {attempt} if call.on_error is not None else path.acted

    def attempt(self, call: Call, env: dict[Ref, _Held]) -> _Attempt:
        """Return the call as a later one may repeat it: what each argument read, and its set.

        An argument whose set the walk does not read is left out, so it shows the two calls
        apart nowhere.
        """
        record: list[tuple[str, Validator[Any]]] = []
        for name, ref in call.bindings:
            if isinstance(ref, Const):
                record.append((name, listed([ref.value])))
                continue
            held = self.resolve(env, ref).held
            if held is not None:
                record.append((name, held.values))
        keys = tuple((name, self.keyed(env, ref)) for name, ref in call.bindings)
        return _Attempt(call.tool, keys, tuple(record))

    def unacted(
        self, made: _Attempt, env: Mapping[Ref, _Held], path: _Path
    ) -> Postcondition | None:
        """Return the postcondition whose read shows ``made`` did not act here, or None."""
        for done, variable, index in path.observed:
            post = self.postconditions[index]
            if done is not made or _ref(variable) not in env:
                continue
            shown = intersection(self.belief(env, variable), post.shows)
            if shown.relation_to(nothing) == "subset":
                return post
        return None

    def observing(
        self,
        call: Call,
        env: Mapping[Ref, _Held],
        path: _Path,
        kept: frozenset[_Observed],
        *,
        acts: bool,
    ) -> frozenset[_Observed]:
        """Return the reads that may show a call did not act, once ``call`` returned.

        A call that may act changes what every read before it reported, so it leaves none. A
        read of a postcondition's ``read`` after an error of its ``after``, on the values that
        call was given at each pair, one version read at one path or one literal, is one, while
        its name holds it.
        """
        if acts:
            return frozenset()
        if not path.acted or not self.postconditions:
            return kept
        reading = {name: self.keyed(env, ref) for name, ref in call.bindings}
        found = set(kept)
        for made in path.acted:
            given = dict(made.keys)
            for index, post in enumerate(self.postconditions):
                if not post.acted or post.after != made.tool or post.read != call.tool:
                    continue
                if all(
                    reading.get(mine) is not None and reading.get(mine) == given.get(theirs)
                    for mine, theirs in post.given
                ):
                    found.add((made, call.result, index))
        return frozenset(found)

    def shown(self, call: Call, env: Mapping[Ref, _Held], path: _Path) -> list[Postcondition]:
        """Return each postcondition the call's result is read under.

        One is where the call is its ``read``, made on the values its ``after`` was given at
        each pair, the last call on the path that may act is that ``after``, and valgebra
        proves every result it may have returned here, met with each test on the way, inside
        its ``when``. The declaration says the read shows its ``shows`` then, so the read's
        belief is met with it, as a belief is with an observation (Bertoli et al. 2006, Def.
        3.4); it is the library's claim (Mansoor et al. 2026, §4.3), named on each link over
        the result.
        """
        if path.last is None or not self.postconditions:
            return []
        tool, bindings, result = path.last
        if _ref(result) not in env:
            return []
        given, reading = dict(bindings), dict(call.bindings)
        found = []
        for post in self.postconditions:
            if post.acted or post.read != call.tool or post.after != tool:
                continue
            if any(
                mine not in reading or reading[mine] != given.get(theirs)
                for mine, theirs in post.given
            ):
                continue
            if self.belief(env, result).relation_to(post.when) == "subset":
                found.append(post)
        return found

    def narrowed(
        self,
        defined: dict[Ref, _Held],
        call: Call,
        contract: Contract,
        shown: Sequence[Postcondition],
    ) -> _Held | None:
        """Meet the call's result with what ``shown`` says it shows; return the whole result.

        Each field slot is met with the set ``shown`` gives that field, so a record built of
        the result's fields is asked inside it as the whole is.
        """
        whole = defined.get(_ref(call.result))
        if not shown or whole is None:
            return whole
        whole = _Held(
            intersection(whole.values, *(post.shows for post in shown)),
            whole.origin,
            case=True,
            assumed=tuple(post.name for post in shown),
            version=whole.version,
            returned=whole.returned,
        )
        defined[_ref(call.result)] = whole
        for slot in contract.results:
            if slot.path:
                fields = (post.field(slot.path) for post in shown)
                defined[_ref(call.result, slot.path)] = _Held(
                    intersection(slot.values, *fields),
                    slot,
                    version=whole.version,
                    returned=whole.returned,
                )
        return whole

    def resting(self, env: Mapping[Ref, _Held], ref: Ref | Const) -> tuple[str, ...]:
        """Return the postconditions the value ``ref`` reads was narrowed by."""
        if not self.postconditions or not isinstance(ref, Ref):
            return ()
        root = env.get(_ref(ref.variable))
        return () if root is None else root.assumed

    def bind(
        self, call: Call, contract: Contract, env: dict[Ref, _Held], where: str
    ) -> dict[str, _Held]:
        """Add the link of each binding inside its argument; return the set each argument holds.

        A required argument no binding fills is refuted.
        """
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
        given = dict(call.bindings)
        for slot in contract.arguments:
            if slot.required and slot.path[0] not in given:
                self.links.append(Link(where, f"{slot.name} is bound", "refuted"))
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
                if name == flow.argument:
                    self.flowed(flow, call, ref, env, where, path)

    def flowed(
        self,
        flow: Flow,
        call: Call,
        ref: Ref | Const,
        env: dict[Ref, _Held],
        where: str,
        path: _Path,
    ) -> None:
        """Add the links of one value reaching a rule's sink."""
        if isinstance(ref, Const):
            if flow.holds(None):
                self.literal(where, flow, ref)
            return
        held = self.resolve(env, ref).held
        if held is None:
            return
        if (flow.fresh or flow.key) and held.origin.tool in flow.sources:
            self.current(flow, call, ref, held, env, where, path)
            return
        if not flow.holds(held.origin.tool):
            return
        claim = f"flow {flow.name}: {ref} from {held.origin.tool} reaches {flow.sink}"
        if flow.endorsed is None:
            self.links.append(Link(where, claim, "refuted"))
        else:
            self.inclusion(where, claim, ref, held, flow.endorsed, env)

    def current(
        self,
        flow: Flow,
        call: Call,
        ref: Ref,
        held: _Held,
        env: dict[Ref, _Held],
        where: str,
        path: _Path,
    ) -> None:
        """Add the links of a source's value reaching a rule that reads when it was returned.

        With ``fresh``, the call that returned it came after every call on the path that may
        act, an action changing what a reading before it reported (Mansoor et al. 2026, §4.1);
        which calls may act is read off their annotations, so the link lists that assumption
        unless every server is trusted. With ``key``, that call took the value the sink's call
        takes at the key, one version read at one path or one literal: a value of one slice of
        the trace reaches no call of another (Roşu and Chen 2012, Def. 11). A value read through
        a loop's item was returned by the call returning the loop's array, and both read the
        version the path holds under a name, not the name (`_Walk.version`). A value failing
        either reaches the sink only inside the endorsed set, as a value from elsewhere does.
        """
        root = env.get(_ref(ref.variable))
        returner = held.returned if root is None else root.returned
        tool = held.origin.tool
        said = f"flow {flow.name}: {ref} from {tool} was returned"
        read: list[tuple[str, bool, tuple[str, ...]]] = []
        if flow.fresh:
            read.append(self.fresh_read(flow, call, said, returner, path))
        if flow.key:
            # The same version read at the same path, or the same literal: one value.
            source = dict(self.given.get(returner, ())).get(flow.key)
            sink = self.keyed(env, dict(call.bindings).get(flow.key))
            same = source is not None and source == sink
            read.append((f"{said} for the {flow.key} {call.tool} takes", same, ()))
        if all(holds for _, holds, _ in read) or flow.endorsed is None:
            for claim, holds, assumed in read:
                verdict = "proven" if holds else "refuted"
                self.links.append(Link(where, claim, verdict, (), assumed if holds else ()))
            return
        claim = f"flow {flow.name}: {ref} from {tool} reaches {flow.sink}"
        self.inclusion(where, claim, ref, held, flow.endorsed, env)

    def fresh_read(
        self, flow: Flow, call: Call, said: str, returner: int, path: _Path
    ) -> tuple[str, bool, tuple[str, ...]]:
        """Return a fresh rule's claim on a source's value, whether it holds, and its assumption.

        The value was returned after the last call before ``call`` that may act, which calls do
        read off their annotations; or, where the rule names the calls that withdraw it, after
        the last of those, the author's claim that no other changes what the sources read
        (Mansoor et al. 2026, §4.1), which the link names.
        """
        if flow.withdrawn_by:
            named = ", ".join(sorted(flow.withdrawn_by))
            stale = dict(path.withdrawn).get(flow.name, frozenset())
            claim = f"{said} after every call to {named} before {call.tool}"
            sources = ", ".join(sorted(flow.sources))
            assumed = (f"flow {flow.name}: only {named} change what {sources} read",)
            return claim, returner not in stale, assumed
        hinted = not all(c.trusted for c in self.contracts.values())
        assumed = ("the read-only annotations of the calls before it",) if hinted else ()
        claim = f"{said} after every call before {call.tool} that may act"
        return claim, returner not in path.stale, assumed

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
            choices = [self.candidates(_ref(name), held)[:3] for name, held in bound.items()]
            for values in itertools.islice(itertools.product(*choices), 64):
                value = dict(zip(bound, values, strict=True))
                if not contract.accepts.is_valid(value) and self.jointly(env, refs, value):
                    found = (value,)
                    break
            if not found and verdict == "refuted" and not _apart(env, refs.values()):
                verdict = "undecided"
        named = [a for held in bound.values() for a in self.assumption(held)]
        if self.postconditions:
            named += [a for _, ref in call.bindings for a in self.resting(env, ref)]
        assumed = tuple(dict.fromkeys(named))
        verdict = "refuted" if found else verdict
        self.links.append(Link(where, claim, verdict, _own(found), assumed))

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
        answer = inside(held.values, target)
        found: tuple[Any, ...] = ()
        if answer != "subset":
            # Realize a witness first: a wrong program is refuted on one question of valgebra,
            # and the root is asked only where none is, the case where it may prove the link.
            outside = [v for v in self.candidates(ref, held) if not target.is_valid(v)][:8]
            found = self.realized(env, ref, outside)
            if found:
                found = (self.shrunk(env, ref, target, found[0]),)
            if not found:
                whole = None if ref.path else answer
                answer = self.asked(env, ref.variable, within(ref.path, target), whole)
        verdict = "refuted" if found else _VERDICTS[answer]
        found = _own(found)
        assumed = self.assumption(held)
        if rested := self.resting(env, ref):
            assumed = tuple(dict.fromkeys((*assumed, *rested)))
        self.links.append(Link(where, claim, verdict, found, assumed))

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
        held = env.get(ref)
        if held is not None:
            return _Read(held)
        for depth in range(len(ref.path) - 1, -1, -1):
            held = env.get(_ref(ref.variable, ref.path[:depth]))
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
        held = env.get(_ref(variable))
        if held is None:
            return "undecided"
        if answer is None:
            answer = inside(held.values, question)
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
        meet = env[_ref(variable)].values
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
        root = env.get(_ref(ref.variable))
        building = root is not None and root.loop is not None
        seen: list[str] = []
        for value in self.candidates(_ref(ref.variable, path), read.record):
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

    def shrunk(self, env: dict[Ref, _Held], ref: Ref, target: Validator[Any], value: Any) -> Any:
        """Return ``value`` shrunk while it stays a value ``ref`` may hold and outside ``target``.

        A drawn witness is as long as the draw, so it is made smaller, length first, a step at a
        time while the smaller one still refutes: each step is one membership valgebra decides.
        """
        for _ in range(_SHRINKS):
            smaller = next(
                (
                    candidate
                    for candidate in _smaller(value)
                    if not target.is_valid(candidate)
                    and self.member(env, ref.variable, [(ref.path, candidate)])
                ),
                _UNSHRUNK,
            )
            if smaller is _UNSHRUNK:
                break
            value = smaller
        return value

    def member(
        self,
        env: Mapping[Ref, _Held],
        variable: str,
        given: Sequence[tuple[tuple[str, ...], Any]],
        now: Mapping[Ref, _Held] | None = None,
    ) -> bool:
        """Return whether valgebra proves a value of ``variable``'s belief holds each one given.

        Each value is given at its path, the empty path being the whole value; the value
        also carries every field a test on the way narrowed inside its narrowed set. A loop's
        item's belief may admit a value no item of the array takes, so for an item the value
        is built, `_Walk.built`. A belief holds every value possible there (Bertoli et al. 2006,
        §3), so a field's own set refuses a value the belief refuses, a value found holding each
        (`_Walk.holds`) shows one, and the belief itself is asked of the rest.
        """
        held = env.get(_ref(variable))
        if held is None:
            return False
        if held.loop is not None or now is not None:
            return self.built(env, variable, given, env if now is None else now)
        for path, value in given:
            field = env.get(_ref(variable, path))
            if field is not None and not field.values.is_valid(value):
                return False
        if self.holds(env, variable, given):
            return True
        belief = self.belief(env, variable)
        if [path for path, _ in given] == [()]:
            return belief.is_valid(given[0][1])
        for path, value in given:
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
        held = env[_ref(variable)]
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
        return given + [v for v in self.draw(held.origin) if held.values.is_valid(v)]

    def draw(self, slot: Slot) -> Sequence[Any]:
        """Return the values drawn for ``slot`` next in the walk's stream, its first ones kept."""
        if self.state is None:
            drawn, self.state = slot.stream(self.seed, DRAWS)
            return drawn
        if self.rng is None:
            self.rng = random.Random()
            self.rng.setstate(self.state)
        try:
            return Sampler(slot.root, self.rng).draw(slot.schema, DRAWS)
        except (Unsupported, RecursionError):
            return ()

    def read_fresh(self, call: Call, env: Mapping[Ref, _Held], where: str, path: _Path) -> None:
        """Add a link for each argument the task reads fresh: no call that may act came between.

        An action changes the world a reading reports, and whether its effect holds is read in
        the world again, not off what was read before it (Mansoor et al. 2026, §4.1). So a value
        read before a call that may act, one whose annotations do not say it is read-only, is no
        reading of the world after it, and a constant is no reading at all. Which calls may act
        is read off their annotations, a hint their server gives, so a link proven over them
        lists that assumption unless every server is trusted.
        """
        for tool, argument in self.task.fresh:
            given = dict(call.bindings)
            if tool != call.tool or argument not in given:
                continue
            ref = given[argument]
            claim = f"{tool}.{argument} is read after every call before it that may act"
            root = env.get(_ref(ref.variable)) if isinstance(ref, Ref) else None
            fresh = root is not None and root.returned not in path.stale
            hinted = fresh and not all(c.trusted for c in self.contracts.values())
            assumed = ("the read-only annotations of the calls before it",) if hinted else ()
            self.links.append(Link(where, claim, "proven" if fresh else "refuted", (), assumed))

    def fixed(self, call: Call, env: dict[Ref, _Held], where: str) -> None:
        """Add a link for each argument the task fixes: the call passes the task's constant.

        A task's intent fixes the value, as the action a confirmation asks about, so a program
        passing another, a literal or a held value that may differ, is refuted there, and so is
        one leaving the argument out: the tool then reads its own default, which no task fixed.
        """
        for tool, argument, value in self.task.constants:
            if tool != call.tool:
                continue
            claim = f"{call.tool}.{argument} is the task's {Const.of(value)}"
            ref = dict(call.bindings).get(argument)
            if ref is None:
                self.links.append(Link(where, claim, "refuted"))
                continue
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
        held = env.get(ref)
        if held is not None:
            return held
        read = self.resolve(env, ref)
        if read.held is not None:
            return read.held
        claim = f"{ref} is a value the program holds"
        lacking = self.lacking(env, ref, read)
        refuted = bool(lacking) or read.answer in ("unbound", "not_subset")
        verdict: Verdict = "refuted" if refuted else "undecided"
        self.links.append(Link(where, claim, verdict, _own(lacking)))
        return None

    def argument(self, tool: str, name: str, where: str) -> Slot | None:
        """Return the slot of ``tool.name``, or None with the link it fails added.

        A tool the library lacks is refuted as a call to it is, and an argument the tool lacks
        as one: a test against either reads no set, so neither branch it guards is walked.
        """
        contract = self.contracts.get(tool)
        if contract is None:
            self.links.append(Link(where, f"{tool} is a tool", "refuted"))
            return None
        try:
            return contract.argument(name)
        except KeyError:
            self.links.append(Link(where, f"{tool}.{name} is an argument", "refuted"))
            return None


class _Read(NamedTuple):
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
        held = env.get(_ref(ref.variable))
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
    if now.get(_ref(variable)) is loop.env.get(_ref(variable)):
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

    A part valgebra proves empty holds no result, so no case is walked for it; a part is also
    dropped where valgebra proves the inclusion its emptiness is, the cell inside the guard or
    its complement, which it can decide where it leaves the emptiness open.
    """
    cells = [values]
    for guard in guards:
        split = []
        for cell in cells:
            # valgebra may decide a part's emptiness only as the inclusion it is the same as.
            inside = cell.relation_to(guard)
            outside = cell.relation_to(complement(guard))
            for part, empty in (
                (intersection(cell, guard), outside == "subset"),
                (intersection(cell, complement(guard)), inside == "subset"),
            ):
                if not empty and part.relation_to(nothing) != "subset":
                    split.append(part)
        cells = split
    return cells


def _join(where: str, branch: str) -> str:
    return f"{where}.{branch}" if where else branch


#: The states of every policy a path may be in, one set per policy, and then one per policy with
#: lapses, the states it may be in whatever time passed (`_Walk.lapsing`).
_States = tuple[frozenset[str], ...]

#: A policy's state held where a call it reads is reached, or its result: the policy's name, what
#: is reached and the state.
_Awaited = tuple[str, str, str]


_Acted = frozenset["_Attempt"]

#: What a binding reads, as a key compares it: a literal, or the version of the value whose
#: field it reads and the field's path, or None where nothing holds it.
_Key = Const | tuple[int, tuple[str, ...]] | None


#: A call as a postcondition reads it: its tool, its bindings and its result's name.
_Made = tuple[str, tuple[tuple[str, Ref | Const], ...], str]


#: A read a postcondition of a call's acting covers: the call, the read's name, its index.
_Observed = tuple["_Attempt", str, int]


def _disjoint(
    one: tuple[tuple[str, Validator[Any]], ...], other: tuple[tuple[str, Validator[Any]], ...]
) -> bool:
    """Return whether valgebra proves two argument records disjoint at some argument."""
    sets = dict(other)
    return any(name in sets and apart(held, sets[name]) for name, held in one)


def _own(found: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return a witness the link alone holds: a slot keeps what it draws, and a caller may write."""
    return copy.deepcopy(found) if found else ()


def _at(value: Any, path: tuple[str, ...]) -> Any:
    """Return what ``value`` holds at ``path``, or `_NONE`."""
    for key in path:
        value = value.get(key, _NONE) if isinstance(value, dict) else _NONE
    return value


def _meeting(value: Any, path: tuple[str, ...], held: _Held) -> Any:
    """Return ``value`` with the field at ``path`` in the set ``held``, or `_NONE`."""
    at = _at(value, path)
    if at is not _NONE and held.values.is_valid(at):
        return value
    member = next(filter(held.values.is_valid, held.origin.members()), _NONE)
    return _NONE if member is _NONE else placed(value, path, member)


def _unbound(env: dict[Ref, _Held], name: str) -> dict[Ref, _Held]:
    """Return what the program holds under any name but ``name``."""
    if not any(ref.variable == name for ref in env):
        return env.copy()
    return {ref: held for ref, held in env.items() if ref.variable != name}


def _unnamed(observed: frozenset[_Observed], name: str) -> frozenset[_Observed]:
    """Return the reads ``observed`` holds under any name but ``name``."""
    return frozenset(read for read in observed if read[1] != name) if observed else observed


def _rebound(path: _Path, name: str) -> _Path:
    """Return ``path`` once ``name`` holds another value: nothing read under it covers."""
    last = None if path.last is not None and name in _named(path.last) else path.last
    return path._replace(last=last, observed=_unnamed(path.observed, name))


def _named(made: _Made) -> set[str]:
    """Return every name a made call's bindings and its result are read under."""
    _, bindings, result = made
    return {ref.variable for _, ref in bindings if isinstance(ref, Ref)} | {result}


def _left(last: _Made | None, call: Call, *, acts: bool) -> _Made | None:
    """Return the last call that may act once ``call`` returned.

    It is ``call`` where it may act. A read-only call leaves the one before, unless it binds a
    name that one reads: the claim reads a value under that name, which now holds another.
    """
    if acts:
        return (call.tool, call.bindings, call.result)
    if last is not None and call.result in _named(last):
        return None
    return last


def _acts(block: Block, contracts: Mapping[str, Contract]) -> bool:
    """Return whether a call ``block`` may make, at any depth, is one that may act.

    A call may act unless its tool's annotations say it is read-only; a tool no contract names
    may act.
    """
    return _makes(block, contracts, lambda _, hints: "readOnlyHint" not in hints)


def _makes(
    block: Block, contracts: Mapping[str, Contract], reads: Callable[[str, frozenset[str]], bool]
) -> bool:
    """Return whether a call ``block`` may make, at any depth, is one ``reads`` holds of.

    ``reads`` is given the tool and its hints, none for a tool no contract names.
    """
    for call in block.calls:
        contract = contracts.get(call.tool)
        if reads(call.tool, frozenset() if contract is None else contract.hints):
            return True
        if call.on_error is not None and _makes(call.on_error, contracts, reads):
            return True
    end = block.end
    if isinstance(end, Branch):
        return _makes(end.then, contracts, reads) or _makes(end.otherwise, contracts, reads)
    if isinstance(end, For):
        return _makes(end.body, contracts, reads) or _makes(end.after, contracts, reads)
    return False


def _withdrawn(
    path: _Path,
    flows: Sequence[Flow],
    env: Mapping[Ref, _Held],
    withdraws: Callable[[Flow], bool],
) -> tuple[tuple[str, frozenset[int]], ...]:
    """Return, for each fresh rule naming the calls that withdraw it, what they made stale.

    It is the versions returned before the last such call: every value held where one is made.
    """
    if not any(flow.withdrawn_by for flow in flows):
        return ()
    held = dict(path.withdrawn)
    returned = frozenset(value.returned for ref, value in env.items() if not ref.path)
    return tuple(
        (
            flow.name,
            held.get(flow.name, frozenset()) | (returned if withdraws(flow) else frozenset()),
        )
        for flow in flows
        if flow.withdrawn_by
    )


class _Path(NamedTuple):
    """What a path carries beside its values: policy states, and the sources it tested."""

    states: _States
    tested: frozenset[str] = frozenset()
    #: The calls that answered with an error on the way here and may have acted, each with
    #: the values it was given (`_Attempt`).
    acted: _Acted = frozenset()
    #: The versions of the calls that returned a value before the last call on the way here
    #: that may act, the task inputs' among them.
    stale: frozenset[int] = frozenset()
    #: The last call on the way here that may act, where it returned and no name it reads
    #: has been bound again since: its tool, its bindings and its result's name.
    last: _Made | None = None
    #: The reads that may show a call that answered with an error did not act: the call, the
    #: name the read is held under, and the postcondition's index.
    observed: frozenset[_Observed] = frozenset()
    #: For each fresh rule naming the calls that withdraw it, the versions returned before the
    #: last of those on the way here, by the rule's name (`_withdrawn`).
    withdrawn: tuple[tuple[str, frozenset[int]], ...] = ()
    #: Each state that lapses held on the way here where a call its policy reads was reached.
    awaited: frozenset[_Awaited] = frozenset()


#: How many smaller witnesses a refutation's is shrunk through at most, and the mark of none.
_SHRINKS = 64
_UNSHRUNK: Any = object()


def _smaller(value: Any) -> Iterator[Any]:
    """Yield values smaller than ``value``, the smallest first: shorter, then nearer zero.

    A text or an array is cut, from empty up to half its length and one shorter; a record loses
    a key; a number goes to zero, then halves toward it, an infinity to zero alone; each item and
    field is shrunk in place.
    """
    if isinstance(value, str | list):
        yield from _cut(value)
    elif isinstance(value, dict):
        yield from _fewer(value)
    elif isinstance(value, int) and not isinstance(value, bool) and value:
        yield 0
        # Halve in integers: one past the float range has no float to divide.
        if half := abs(value) // 2:
            yield half if value > 0 else -half
    elif isinstance(value, float) and value:
        yield 0.0
        if math.isfinite(value) and (half := int(value / 2)):
            yield float(half)


def _cut(value: str | list[Any]) -> Iterator[Any]:
    """Yield a text or an array cut shorter, then an array with one item shrunk.

    The lengths tried close in on a bound by halves: empty, one, then a half, a quarter and on
    to one item fewer, so a witness reaches the shortest that still refutes in few steps.
    """
    lengths, cut = [0, 1], len(value) // 2
    while cut:
        lengths.append(len(value) - cut)
        cut //= 2
    for length in dict.fromkeys(lengths):
        if length < len(value):
            yield value[:length]
    if isinstance(value, list):
        for at, item in enumerate(value):
            for smaller in _smaller(item):
                yield [*value[:at], smaller, *value[at + 1 :]]


def _fewer(value: dict[str, Any]) -> Iterator[Any]:
    """Yield a record with one key fewer, then one with one field shrunk."""
    for key in value:
        yield {k: v for k, v in value.items() if k != key}
    for key, item in value.items():
        for smaller in _smaller(item):
            yield {**value, key: smaller}


#: The empty array, and the arrays holding an item: the two sides of a loop's test.
_EMPTY = compile_schema({"type": "array", "maxItems": 0})
_HOLDING = compile_schema({"type": "array", "minItems": 1})


def _timing(policy: Policy, held: frozenset[str], awaited: frozenset[_Awaited]) -> list[str]:
    """Return the timing a policy's link proven with no lapse taken rests on, as it names it.

    ``held`` is the states it ends in: one whose lapse would leave an obligation open, or reach a
    bad state, is left before it comes due.
    """
    found = [
        f"the run reaches {what} within {lapse.seconds:g} s of entering {state}"
        for name, what, state in sorted(awaited)
        if name == policy.name and (lapse := policy.lapse(state)) is not None
    ]
    return found + [
        f"the run ends within {lapse.seconds:g} s of entering {state}"
        for state in sorted(held)
        if (lapse := policy.lapse(state)) is not None
        and policy.due(frozenset({state})) & (policy.bad | policy.pending)
    ]


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
    postconditions: Sequence[Postcondition] = (),
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
    read, for every call to the sink under a test on such a value. A read a postcondition
    covers is read in the set it declares the read shows, and each link over it names it
    (`_Walk.shown`). Raises `ValueError` for a task naming an input as a tool, whose source no
    name could tell apart.
    """
    unclashed(task, contracts)
    walk = _Walk(contracts, task, examples or {}, errors, tuple(policies), tuple(flows))
    walk.seed, walk.postconditions = seed, tuple(postconditions)
    # Each input is a value of its own, returned by no call: its version is its own.
    versions = {var: walk.version() for var in task.held}
    walk.inputs = {version: var for var, version in versions.items()}
    env = {
        _ref(var, slot.path): _Held(
            slot.values, slot, version=versions[var], returned=versions[var]
        )
        for var, slots in task.held.items()
        for slot in slots
    }
    walk.block(program, env, "", _Path(walk.initial()))
    return Verification(tuple(walk.links), tuple(walk.gaps), tuple(walk.returns))
