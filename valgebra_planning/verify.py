"""Every link of a program, asked of valgebra alone.

A link is an inclusion a program rests on: a reference inside the argument it fills, a
call's whole argument record inside the tool's input set where the arguments are related,
and the returned reference inside the goal. Each is asked of valgebra with the set the
reference lies in at that point of the program, narrowed by every test on the way there, and
nothing else: not the cells, not the search. A refuted link carries a value outside the
inclusion where one is found among the examples given and the values drawn from the
reference's own schema. A value found that way refutes an undecided link too, since a
concrete member is a proof.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from valgebra_planning._valgebra import (
    Sampler,
    Unsupported,
    Validator,
    compile_schema,
    complement,
    intersection,
)
from valgebra_planning.contracts import Contract, Slot, Task, item_slots, listed
from valgebra_planning.flows import Flow
from valgebra_planning.policy import Policy
from valgebra_planning.program import Block, Branch, Call, Const, For, Next, Ref, Return, Stop
from valgebra_planning.run import retry_assumptions

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
                self.inclusion(where, f"return {end.ref} <= goal", end.ref, held, self.task.goal)
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
            ends = self.iterate(end.body, inner, inside, _Path(states, tested), silent=True)
            grown = tuple(s.union(*(e[i] for e in ends)) for i, s in enumerate(states))
            if grown == states:
                break
            states = grown
        self.iterate(end.body, inner, inside, _Path(states, tested), silent=False)
        self.block(end.after, env, where, _Path(states, path.tested))

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
        """Add a link per policy: whether this path may reach one of its bad states."""
        for policy, held in zip(self.policies, states, strict=True):
            verdict = _OBEYED[policy.reach(held)]
            hinted = policy.hints() and not all(c.trusted for c in self.contracts.values())
            assumed = (f"the annotation hints policy {policy.name} reads",) if hinted else ()
            self.links.append(Link(where, f"policy {policy.name} holds", verdict, (), assumed))

    def branch(self, test: Branch, env: dict[Ref, _Held], where: str, path: _Path) -> None:
        held = self.held(env, test.ref, where)
        opened: dict[Ref, _Held] = {}
        if test.key:
            tested = compile_schema({"type": "object", "required": [test.key]})
            if held is not None:
                opened = self.opened(test.ref, held, test.key)
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
        tested = _Path(path.states, path.tested | {held.origin.tool})
        self.block(test.then, {**env, test.ref: inside, **opened}, _join(where, "then"), tested)
        self.block(test.otherwise, {**env, test.ref: outside}, _join(where, "else"), tested)

    def call(
        self, call: Call, env: dict[Ref, _Held], where: str, path: _Path
    ) -> tuple[dict[Ref, _Held], _Path]:
        contract = self.contracts.get(call.tool)
        if contract is None:
            self.links.append(Link(where, f"{call.tool} is a tool", "refuted"))
            return env, path
        self.flowing(call, env, where, path)
        states = path.states
        bound = self.bind(call, env, where)
        for slot in contract.arguments:
            if slot.required and slot.path[0] not in dict(call.bindings):
                self.links.append(Link(where, f"{slot.name} is bound", "refuted"))
        if not contract.plain and len(bound) == len(call.bindings):
            self.record(call, contract, bound, where)
        after = states
        if self.policies:
            record = Validator({name: held.values for name, held in bound.items()}).close()
            after = tuple(
                policy.step(held, call.tool, record, contract.hints)
                for policy, held in zip(self.policies, states, strict=True)
            )
        # A tool that answered with an error may still have acted, so its block reads both.
        either = tuple(before | then for before, then in zip(states, after, strict=True))
        self.failure(call, contract, env, where, _Path(either, path.tested))
        defined = {ref: held for ref, held in env.items() if ref.variable != call.result}
        for slot in contract.results:
            defined[Ref(call.result, slot.path)] = _Held(slot.values, slot)
        return defined, _Path(after, path.tested)

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
            self.inclusion(where, f"{ref} <= {slot.name}", ref, held, slot.values)
        return bound

    def flowing(self, call: Call, env: dict[Ref, _Held], where: str, path: _Path) -> None:
        """Add a link for every flow rule whose sink this call fills."""
        for flow in self.flows:
            if flow.tool != call.tool:
                continue
            tested = sorted(path.tested & flow.sources)
            if flow.implicit and tested:
                claim = f"flow {flow.name}: {call.tool} runs under a test on {', '.join(tested)}"
                self.links.append(Link(where, claim, "refuted"))
            for name, ref in call.bindings:
                if isinstance(ref, Const):
                    continue
                held = env.get(ref)
                if name != flow.argument or held is None or held.origin.tool not in flow.sources:
                    continue
                claim = f"flow {flow.name}: {ref} from {held.origin.tool} reaches {flow.sink}"
                if flow.endorsed is None:
                    self.links.append(Link(where, claim, "refuted"))
                else:
                    self.inclusion(where, claim, ref, held, flow.endorsed)

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
        record = Validator({name: held.values for name, held in bound.items()}).close()
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
        self, where: str, claim: str, ref: Ref, held: _Held, target: Validator[Any]
    ) -> None:
        verdict = _relation(held.values, target)
        found: tuple[Any, ...] = ()
        if verdict != "proven":
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

    def opened(self, ref: Ref, held: _Held, key: str) -> dict[Ref, _Held]:
        """Return the optional fields a test showing ``key`` in ``ref`` makes the program hold."""
        contract = self.contracts.get(held.origin.tool)
        base = (*held.origin.path, key)
        return {
            Ref(ref.variable, (*ref.path, *slot.path[len(held.origin.path) :])): _Held(
                slot.values, slot
            )
            for slot in (() if contract is None else contract.optional)
            if slot.path[: len(base)] == base
        }

    def absent(self, env: dict[Ref, _Held], ref: Ref) -> tuple[Any, ...]:
        """Return a value of the nearest field the program holds above ``ref`` lacking the next."""
        for depth in range(len(ref.path) - 1, -1, -1):
            parent = Ref(ref.variable, ref.path[:depth])
            if parent not in env:
                continue
            for value in self.candidates(parent, env[parent]):
                if isinstance(value, dict):
                    lacking = {k: v for k, v in value.items() if k != ref.path[depth]}
                    if env[parent].values.is_valid(lacking):
                        return (lacking,)
            return ()
        return ()

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
        if ref not in env:
            claim = f"{ref} is a value the program holds"
            self.links.append(Link(where, claim, "refuted", self.absent(env, ref)))
            return None
        return env[ref]

    def argument(self, tool: str, name: str, where: str) -> Slot | None:
        contract = self.contracts.get(tool)
        try:
            return None if contract is None else contract.argument(name)
        except KeyError:
            self.links.append(Link(where, f"{tool}.{name} is an argument", "refuted"))
            return None


def _join(where: str, branch: str) -> str:
    return f"{where}.{branch}" if where else branch


#: The states of every policy a path may be in, one set per policy.
_States = tuple[frozenset[str], ...]


@dataclass(frozen=True, slots=True)
class _Path:
    """What a path carries beside its values: policy states, and the sources it tested."""

    states: _States
    tested: frozenset[str] = frozenset()


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
    must act as calling once; it ends under fairness, the strong cyclic reading. Each policy
    adds a link at the end of every path: proven where no bad state can be reached there,
    refuted where one surely is, undecided where one possibly is. Each flow rule adds a link
    for every value from one of its sources bound to its sink, and, where implicit flows are
    read, for every call to the sink under a test on such a value.
    """
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
