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

import functools
import itertools
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from valgebra_planning._valgebra import (
    Sampler,
    Unsupported,
    Validator,
    anything,
    complement,
    intersection,
    nothing,
)
from valgebra_planning.contracts import (
    Contract,
    Slot,
    Task,
    field_slot,
    item_slots,
    listed,
    record_of,
    unclashed,
)
from valgebra_planning.flows import Flow
from valgebra_planning.policy import Policy
from valgebra_planning.program import Block, Branch, Call, Const, For, Next, Ref, Return, Stop
from valgebra_planning.run import annotated, retry_assumptions

__all__ = ["Gap", "Link", "Verdict", "Verification", "verify"]

Verdict = Literal["proven", "refuted", "undecided"]

#: How many values are drawn from a reference's schema in search of a counterexample.
DRAWS = 40


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
    #: What the verdict rests on beside the contracts: an output schema inferred from observed
    #: calls, never a proof. The verdict is valgebra's over the contracts and these.
    assumed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Gap:
    """A path the program stops on, and the reference it leaves uncovered there."""

    where: str
    ref: str


@dataclass(frozen=True, slots=True)
class Verification:
    """The links of a program and the gaps it leaves."""

    links: tuple[Link, ...]
    gaps: tuple[Gap, ...]

    @property
    def proven(self) -> bool:
        """Return whether every link is proven and no path stops."""
        return not self.gaps and all(link.verdict == "proven" for link in self.links)

    def count(self, verdict: Verdict) -> int:
        """Return how many links read ``verdict``."""
        return sum(link.verdict == verdict for link in self.links)


@dataclass(frozen=True, slots=True)
class _Held:
    """The set a reference lies in at a point of a program, and the slot it came from."""

    values: Validator[Any]
    origin: Slot


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
    gaps: list[Gap] = field(default_factory=list)

    def block(self, block: Block, env: dict[Ref, _Held], where: str, path: _Path) -> None:
        for call in block.calls:
            env, path = self.call(call, env, where, path)
        end = block.end
        if isinstance(end, Return):
            held = self.held(env, end.ref, where)
            if held is not None:
                claim = f"return {end.ref} <= goal"
                self.inclusion(where, claim, end.ref, held, self.task.goal, env)
            self.obeyed(where, path.states)
        elif isinstance(end, Stop):
            self.gaps.append(Gap(where, str(end.ref)))
            self.obeyed(where, path.states)
        elif isinstance(end, Next):
            if self.ends is not None:
                self.ends.append(path.states)
        elif isinstance(end, For):
            self.loop(end, env, where, path)
        else:
            self.branch(end, env, where, path)

    def loop(self, end: For, env: dict[Ref, _Held], where: str, path: _Path) -> None:
        """Walk a loop's body for an arbitrary item, the policies at a fixpoint; then what follows.

        Nothing crosses iterations, so one walk over the item's set holds for every item. The
        body may run any number of times, so the states a policy may be in when it starts are
        taken to a fixpoint, the states being finite. How many times it runs is the array's, so
        its calls run under a test on the array's source.
        """
        held = self.held(env, end.ref, where)
        items = () if held is None else item_slots(held.origin)
        if held is None or not items:
            if held is not None:
                claim = f"{end.ref} is an array of one item schema"
                self.links.append(Link(where, claim, "refuted"))
            return
        base = len(held.origin.path) + 1
        inner = env | {Ref(end.item, s.path[base:]): _Held(s.values, s) for s in items}
        inside, tested = _join(where, f"for:{end.item}"), path.tested | {held.origin.tool}
        states = path.states
        while self.policies:
            ends = self.iterate(
                end.body, inner, inside, _Path(states, tested, path.acted), silent=True
            )
            grown = tuple(s.union(*(e[i] for e in ends)) for i, s in enumerate(states))
            if grown == states:
                break
            states = grown
        self.iterate(end.body, inner, inside, _Path(states, tested, path.acted), silent=False)
        self.block(end.after, env, where, _Path(states, path.tested, path.acted))

    def iterate(
        self, body: Block, env: dict[Ref, _Held], where: str, path: _Path, *, silent: bool
    ) -> list[_States]:
        """Walk a loop's body; return the policy states its iterations end in."""
        links, gaps, ends = self.links, self.gaps, self.ends
        self.ends = []
        if silent:
            self.links, self.gaps = [], []
        self.block(body, env, where, path)
        found = self.ends
        self.links, self.gaps, self.ends = links, gaps, ends
        return found

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
            tested = _having(test.key)
        elif test.tool:
            slot = self.argument(test.tool, test.argument, where)
            tested = None if slot is None else slot.values
        else:
            tested = listed(test.values)
        if held is None or tested is None:
            return
        inside = _Held(intersection(held.values, tested), held.origin)
        outside = _Held(intersection(held.values, complement(tested)), held.origin)
        # Which branch runs carries the tested value's influence: an implicit flow.
        tested = _Path(path.states, path.tested | {held.origin.tool}, path.acted)
        self.block(test.then, {**env, test.ref: inside}, _join(where, "then"), tested)
        self.block(test.otherwise, {**env, test.ref: outside}, _join(where, "else"), tested)

    def call(
        self, call: Call, env: dict[Ref, _Held], where: str, path: _Path
    ) -> tuple[dict[Ref, _Held], _Path]:
        contract = self.contracts.get(call.tool)
        if contract is None:
            self.links.append(Link(where, f"{call.tool} is a tool", "refuted"))
            return env, path
        self.flowing(call, env, where, path)
        acted = path.acted
        failing = self.repeated(call, contract, where, acted) if acted or call.on_error else acted
        states = path.states
        bound = self.bind(call, env, where)
        for slot in contract.arguments:
            if slot.required and slot.path[0] not in dict(call.bindings):
                self.links.append(Link(where, f"{slot.name} is bound", "refuted"))
        if not contract.plain and len(bound) == len(call.bindings):
            self.record(call, contract, bound, where)
        after = states
        if self.policies:
            record = record_of({name: held.values for name, held in bound.items()}, closed=True)
            after = tuple(
                policy.step(held, call.tool, record, contract.hints)
                for policy, held in zip(self.policies, states, strict=True)
            )
        # A tool that answered with an error may still have acted, so its block reads both.
        either = tuple(before | then for before, then in zip(states, after, strict=True))
        self.failure(call, contract, env, where, _Path(either, path.tested, failing))
        defined = {ref: held for ref, held in env.items() if ref.variable != call.result}
        for slot in contract.results:
            defined[Ref(call.result, slot.path)] = _Held(slot.values, slot)
        return defined, _Path(after, path.tested, _unread(acted, call.result) if acted else acted)

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

    def record(self, call: Call, contract: Contract, bound: dict[str, _Held], where: str) -> None:
        record = record_of({name: held.values for name, held in bound.items()}, closed=True)
        claim = f"the arguments of {call.tool} <= its input schema"
        verdict = _relation(record, contract.accepts)
        found: tuple[Any, ...] = ()
        if verdict != "proven":
            choices = [self.candidates(Ref(name), held)[:3] for name, held in bound.items()]
            for values in itertools.islice(itertools.product(*choices), 64):
                value = dict(zip(bound, values, strict=True))
                if not contract.accepts.is_valid(value):
                    found = (value,)
                    break
        self.links.append(Link(where, claim, "refuted" if found else verdict, found))

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
        (Castagna 2023, §4.2), and the program's own tests prove it carried on this path
        (Bertoli et al. 2006, Def. 3.4). A refutation rests on the root's answer only where no
        test narrowed a field on the way, and on a value drawn for the field only where
        valgebra proves a member of the root carries it there.
        """
        verdict = _relation(held.values, target)
        found: tuple[Any, ...] = ()
        root = env.get(Ref(ref.variable)) if ref.path else None
        if verdict != "proven" and root is not None:
            # Realize a witness first: a wrong program is refuted on one question of valgebra,
            # and the root is asked only where none is, the case where it may prove the link.
            outside = [v for v in self.candidates(ref, held) if not target.is_valid(v)][:8]
            found = self.realized(env, ref, outside)
            if not found:
                verdict = _relation(root.values, _within(ref.path, target))
                if verdict == "refuted" and _narrowed(env, ref.variable, ref.path):
                    verdict = "undecided"
        elif verdict != "proven":
            outside = [v for v in self.candidates(ref, held) if not target.is_valid(v)]
            found = tuple(outside[:1])
        verdict = "refuted" if found else verdict
        self.links.append(Link(where, claim, verdict, found, self.assumption(held)))

    def assumption(self, held: _Held) -> tuple[str, ...]:
        """Return the assumption a held value rests on: its tool's output schema, if inferred."""
        contract = self.contracts.get(held.origin.tool)
        if contract is None or not contract.inferred:
            return ()
        return (f"the output of {contract.name} as observed",)

    def resolve(self, env: dict[Ref, _Held], ref: Ref) -> _Read:
        """Read ``ref`` from the nearest value the program holds above it, a field at a time.

        Selection requires the selected field to be defined (Castagna 2023, §3.2.2): a field is
        read where valgebra proves every value held for its record an object holding the key,
        as a test showing the key narrows the record to (Bertoli et al. 2006, Def. 3.4), and
        otherwise where it proves every value of the root carries the path. The field lies in
        what its record's schema declares of it, `valgebra_planning.contracts.field_slot`.
        """
        if ref in env:
            return _Read(env[ref])
        for depth in range(len(ref.path) - 1, -1, -1):
            held = env.get(Ref(ref.variable, ref.path[:depth]))
            if held is None:
                continue
            for at in range(depth, len(ref.path)):
                key = ref.path[at]
                answer = held.values.relation_to(_having(key))
                if answer != "subset" and at:
                    answer = self.present(env, ref.variable, ref.path[: at + 1])
                if answer != "subset":
                    return _Read(None, answer, held, at)
                slot = field_slot(held.origin, key)
                held = _Held(slot.values, slot)
            return _Read(held)
        return _Read(None, "unbound")

    def present(self, env: dict[Ref, _Held], variable: str, path: tuple[str, ...]) -> str:
        """Return valgebra's answer on every value of the root carrying ``path``.

        A record's declared set may admit a value lacking the key that another keyword of the
        root requires, so presence the record's set leaves open is asked of the root, whose set
        is the belief the program holds of every value there (Bertoli et al. 2006, Def. 3.4).
        Where a test narrowed a field on the way, a root value lacking the key may be one the
        test excluded, so a refutation there is undecided.
        """
        root = env.get(Ref(variable))
        if root is None:
            return "undecided"
        answer = root.values.relation_to(_selecting(path, anything))
        if answer == "not_subset" and _narrowed(env, variable, path[:-1]):
            return "undecided"
        return answer

    def lacking(self, env: dict[Ref, _Held], ref: Ref, read: _Read) -> tuple[Any, ...]:
        """Return a value of the record ``read`` stopped at, lacking the next key of ``ref``.

        A value drawn for the record is a witness only where valgebra proves a member of the
        root carries it there: the record's declared set is a belief holding every value the
        record takes (Bertoli et al. 2006, Def. 3.4), and may admit one no member carries.
        """
        if read.record is None:
            return ()
        key, path = ref.path[read.at], ref.path[: read.at]
        for value in self.candidates(Ref(ref.variable, path), read.record):
            if not isinstance(value, dict):
                continue
            bare = {name: item for name, item in value.items() if name != key}
            if read.record.values.is_valid(bare) and self.carried(env, ref.variable, path, bare):
                return (bare,)
        return ()

    def realized(self, env: dict[Ref, _Held], ref: Ref, values: Sequence[Any]) -> tuple[Any, ...]:
        """Return one of ``values`` valgebra proves a member of the root carries at ``ref``."""
        for value in values:
            if self.carried(env, ref.variable, ref.path, value):
                return (value,)
        return ()

    def carried(
        self, env: dict[Ref, _Held], variable: str, path: tuple[str, ...], value: Any
    ) -> bool:
        """Return whether valgebra proves a member of the root carries ``value`` at ``path``.

        The member also lies inside every set the program holds on the way.
        """
        root = env.get(Ref(variable))
        if root is None:
            return False
        if not path:
            return root.values.is_valid(value)
        meet = intersection(root.values, _selecting(path, listed([value])))
        for depth in range(1, len(path)):
            held = env.get(Ref(variable, path[:depth]))
            if held is not None:
                meet = intersection(meet, _selecting(path[:depth], held.values))
        return meet.relation_to(nothing) == "not_subset"

    def candidates(self, ref: Ref, held: _Held) -> list[Any]:
        """Return values the reference may hold there: the examples given, then draws."""
        given = [v for v in self.examples.get(str(ref), ()) if held.values.is_valid(v)]
        try:
            drawn = Sampler(held.origin.root, self.rng).draw(held.origin.schema, DRAWS)
        except (Unsupported, RecursionError):
            drawn = []
        return given + [v for v in drawn if held.values.is_valid(v)]

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


@functools.cache
def _having(key: str) -> Validator[Any]:
    """Return the objects holding ``key``, whatever else they hold."""
    return record_of({key: anything}, closed=False)


def _selecting(path: tuple[str, ...], values: Validator[Any]) -> Validator[Any]:
    """Return the objects whose field at ``path`` lies in ``values``, whatever else they hold."""
    for key in reversed(path):
        values = record_of({key: values}, closed=False)
    return values


def _within(path: tuple[str, ...], values: Validator[Any]) -> Validator[Any]:
    """Return the values whose field at ``path``, wherever each key on it is, lies in ``values``.

    Each key is spelled optional with a ``?`` appended, which valgebra's display strips once,
    so a key is read as written; a clause admits every other key with any value.
    """
    for key in reversed(path):
        values = Validator({f"{key}?": values, str: anything})
    return values


def _narrowed(env: dict[Ref, _Held], variable: str, path: tuple[str, ...]) -> bool:
    """Return whether a test narrowed a field of ``variable`` on the way down ``path``."""
    return any(
        (held := env.get(Ref(variable, path[:depth]))) is not None
        and held.values is not held.origin.values
        for depth in range(1, len(path) + 1)
    )


def _join(where: str, branch: str) -> str:
    return f"{where}.{branch}" if where else branch


#: The states of every policy a path may be in, one set per policy.
_States = tuple[frozenset[str], ...]


_Acted = frozenset[tuple[str, frozenset[tuple[str, Ref | Const]]]]


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
    2003, Def. 2.10). Each policy
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
    env = {
        Ref(var, slot.path): _Held(slot.values, slot)
        for var, slots in task.held.items()
        for slot in slots
    }
    walk.block(program, env, "", _Path(tuple(frozenset({p.initial}) for p in policies)))
    return Verification(tuple(walk.links), tuple(walk.gaps))
