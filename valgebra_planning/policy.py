"""Policies over the calls a program makes: automata whose guards are valgebra sets.

A policy is a symbolic automaton over call events (D'Antoni and Veanes 2017): a transition
fires on a call to its tool whose argument record lies in its guard, a JSON Schema the
companion reads into a set. Some states are bad: reaching one is a violation, and a bad state
is never left. Some are pending: a path may pass through one and must not end in one, an
obligation still open, as a notice owed after a booking. A call no guard of the current state
matches keeps that state. It states what a domain policy says
in prose (τ-bench, Yao et al. 2024), and what a trace rule checks one prefix at a time
(AgentLTL, Elkoussy and Perez 2026), as a set a whole program is decided against before it
runs.

Statically, a program's every path steps the automaton with the argument records it may
pass: a guard is taken surely where valgebra proves the record inside it, never where it
proves them disjoint, and possibly otherwise. A path ending in a bad or a pending state
surely refutes the program, possibly leaves it undecided, and no path ending in one proves
it; a bad state never left makes ending in one the same as reaching one. Every path of a
program ends, a loop's after any number of items, so an obligation is decided over the whole
program before it runs, where a monitor reading a run's prefix cannot decide it (Bauer et al.
2011). At run time the same automaton is a monitor stepping on concrete records, and reading
the pending states when the run ends.

A policy reads one program's calls in order. It carries no value from one call to another,
so a rule about the same parcel across two calls is read as a rule about any two calls: a
program approving one parcel and booking another passes a rule that a booking follows its own
parcel's approval. A rule that a call takes a value another returned is a flow rule with
``only`` (`valgebra_planning.flows`). A policy instance per value, parametric monitoring
(Roşu and Chen 2012), is not read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from valgebra_planning._valgebra import Validator, compile_schema, intersection, nothing

Reach = Literal["never", "possibly", "surely"]


@dataclass(frozen=True, slots=True)
class Transition:
    """From ``source`` to ``target`` on a call to ``tool`` whose arguments lie in ``guard``.

    With ``hint`` set and ``tool`` empty, the call is to any tool whose annotations set that hint,
    ``destructiveHint`` or another: a hint its server gives, untrusted unless the server is.
    """

    source: str
    tool: str
    guard: Validator[Any]
    target: str
    hint: str = ""

    def reads(self, tool: str, hints: frozenset[str]) -> bool:
        """Return whether a call to ``tool``, whose annotations set ``hints``, is one it reads."""
        return self.hint in hints if self.hint else self.tool == tool


@dataclass(frozen=True, slots=True)
class Policy:
    """An automaton over call events: where it starts, its bad states and its pending ones."""

    name: str
    initial: str
    bad: frozenset[str]
    transitions: tuple[Transition, ...]
    #: The states a path may pass through and must not end in: an obligation still open.
    pending: frozenset[str] = frozenset()

    def tools(self) -> frozenset[str]:
        """Return every tool a transition names."""
        return frozenset(transition.tool for transition in self.transitions if transition.tool)

    def hints(self) -> frozenset[str]:
        """Return every annotation hint a transition reads."""
        return frozenset(transition.hint for transition in self.transitions if transition.hint)

    def step(
        self,
        states: frozenset[str],
        tool: str,
        record: Validator[Any],
        hints: frozenset[str] = frozenset(),
    ) -> frozenset[str]:
        """Return the states the automaton may be in after a call whose record lies in ``record``.

        A transition taken surely leaves its source; one taken possibly adds its target and
        keeps the source, as does a call no guard of the state surely matches. A bad state is
        never left.
        """
        after: set[str] = set()
        for state in states:
            if state in self.bad:
                after.add(state)
                continue
            stays = True
            for transition in self.transitions:
                if transition.source != state or not transition.reads(tool, hints):
                    continue
                taken = _taken(record, transition.guard)
                if taken != "never":
                    after.add(transition.target)
                if taken == "surely":
                    stays = False
            if stays:
                after.add(state)
        return frozenset(after)

    def reach(self, states: frozenset[str], *, ended: bool = False) -> Reach:
        """Return whether a bad state is held, or a pending one where a path ends: surely if all.

        A path that has not ended may pass through a pending state.
        """
        bad = states & (self.bad | self.pending if ended else self.bad)
        if not bad:
            return "never"
        return "surely" if bad == states else "possibly"


def _taken(record: Validator[Any], guard: Validator[Any]) -> Reach:
    if record.relation_to(guard) == "subset":
        return "surely"
    if intersection(record, guard).relation_to(nothing) == "subset":
        return "never"
    return "possibly"


def read_policy(spec: Mapping[str, Any]) -> Policy:
    """Read a policy: ``name``, ``initial``, ``bad`` states, ``transitions`` and ``pending``.

    Each transition names its ``from`` and ``to`` states and its ``tool``, or the annotation
    ``hint`` a tool must set, and optionally the JSON Schema its ``arguments`` record must meet,
    every record where it names none.
    """
    transitions = tuple(
        Transition(
            source=t["from"],
            tool=t.get("tool", ""),
            guard=compile_schema(t.get("arguments", True)),
            target=t["to"],
            hint=t.get("hint", ""),
        )
        for t in spec["transitions"]
    )
    return Policy(
        spec["name"],
        spec["initial"],
        frozenset(spec["bad"]),
        transitions,
        frozenset(spec.get("pending", ())),
    )


@dataclass(slots=True)
class Monitor:
    """A policy stepped on concrete calls, as a run makes them."""

    policy: Policy
    state: str = ""
    violations: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.state = self.state or self.policy.initial

    def observe(
        self, tool: str, arguments: Mapping[str, Any], hints: frozenset[str] = frozenset()
    ) -> bool:
        """Step on one call; return whether it reached a bad state, which it never leaves."""
        if self.state in self.policy.bad:
            return False
        for transition in self.policy.transitions:
            if (
                transition.source == self.state
                and transition.reads(tool, hints)
                and transition.guard.is_valid(dict(arguments))
            ):
                self.state = transition.target
                break
        if self.state in self.policy.bad:
            self.violations.append(f"{self.policy.name}: {tool} reached {self.state}")
            return True
        return False

    def ended(self) -> bool:
        """Return whether the run ends in a pending state: an obligation it leaves open."""
        if self.state not in self.policy.pending:
            return False
        self.violations.append(f"{self.policy.name}: the run ended in {self.state}")
        return True
