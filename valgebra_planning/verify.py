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
    complement,
    intersection,
)
from valgebra_planning.contracts import Contract, Slot, Task
from valgebra_planning.program import Block, Branch, Call, Ref, Return, Stop

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
    links: list[Link] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)

    def block(self, block: Block, env: dict[Ref, _Held], where: str) -> None:
        for call in block.calls:
            env = self.call(call, env, where)
        end = block.end
        if isinstance(end, Return):
            held = self.held(env, end.ref, where)
            if held is not None:
                self.inclusion(where, f"return {end.ref} <= goal", end.ref, held, self.task.goal)
        elif isinstance(end, Stop):
            self.gaps.append(Gap(where, str(end.ref)))
        else:
            self.branch(end, env, where)

    def branch(self, test: Branch, env: dict[Ref, _Held], where: str) -> None:
        held = self.held(env, test.ref, where)
        slot = self.argument(test.tool, test.argument, where)
        if held is None or slot is None:
            return
        inside = _Held(intersection(held.values, slot.values), held.origin)
        outside = _Held(intersection(held.values, complement(slot.values)), held.origin)
        self.block(test.then, {**env, test.ref: inside}, _join(where, "then"))
        self.block(test.otherwise, {**env, test.ref: outside}, _join(where, "else"))

    def call(self, call: Call, env: dict[Ref, _Held], where: str) -> dict[Ref, _Held]:
        contract = self.contracts.get(call.tool)
        if contract is None:
            self.links.append(Link(where, f"{call.tool} is a tool", "refuted"))
            return env
        bound: dict[str, _Held] = {}
        for name, ref in call.bindings:
            slot = self.argument(call.tool, name, where)
            held = self.held(env, ref, where)
            if slot is None or held is None:
                continue
            bound[name] = held
            self.inclusion(where, f"{ref} <= {slot.name}", ref, held, slot.values)
        for slot in contract.arguments:
            if slot.required and slot.path[0] not in dict(call.bindings):
                self.links.append(Link(where, f"{slot.name} is bound", "refuted"))
        if not contract.plain and len(bound) == len(call.bindings):
            self.record(call, contract, bound, where)
        defined = {ref: held for ref, held in env.items() if ref.variable != call.result}
        for slot in contract.results:
            defined[Ref(call.result, slot.path)] = _Held(slot.values, slot)
        return defined

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
        self.links.append(Link(where, claim, "refuted" if found else verdict, found))

    def candidates(self, ref: Ref, held: _Held) -> list[Any]:
        """Return values the reference may hold there: the examples given, then draws."""
        given = [v for v in self.examples.get(str(ref), ()) if held.values.is_valid(v)]
        try:
            drawn = Sampler(held.origin.root, self.rng).draw(held.origin.schema, DRAWS)
        except (Unsupported, RecursionError):
            drawn = []
        return given + [v for v in drawn if held.values.is_valid(v)]

    def held(self, env: dict[Ref, _Held], ref: Ref, where: str) -> _Held | None:
        if ref not in env:
            self.links.append(Link(where, f"{ref} is a value the program holds", "refuted"))
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
) -> Verification:
    """Ask every link of ``program`` of valgebra, for ``task`` over ``contracts``.

    ``examples`` maps a reference, written as the program writes it, to values it may hold,
    tried first in search of a counterexample.
    """
    walk = _Walk(contracts, task, examples or {}, random.Random(seed))
    env = {
        Ref(var, slot.path): _Held(slot.values, slot)
        for var, slots in task.held.items()
        for slot in slots
    }
    walk.block(program, env, "")
    return Verification(tuple(walk.links), tuple(walk.gaps))
