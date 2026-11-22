"""Policies over the calls a program makes: automata whose guards are valgebra sets.

A policy is a symbolic automaton over call events (D'Antoni and Veanes 2017, Def. 1): a transition
fires on a call to its tool whose argument record lies in its guard, a JSON Schema the
companion reads into a set. Some states are bad: reaching one is a violation, and a bad state
is never left. Some are pending: a path may pass through one and must not end in one, an
obligation still open, as a notice owed after a booking. A call no guard of the current state
matches keeps that state. It states what a domain policy says
in prose (τ-bench, Yao et al. 2024, §3), and what a trace rule checks one prefix at a time
(AgentLTL, Elkoussy and Perez 2026, §3.4), as a set a whole program is decided against before it
runs.

Statically, a program's every path steps the automaton with the argument records it may pass: a
guard is taken surely where valgebra proves the record inside it, never where it proves them
disjoint, and possibly otherwise. A path ending in a bad or a pending state surely refutes the
program, possibly leaves it undecided, and no path ending in one proves it; a bad state never
left makes ending in one the same as reaching one. Every path of a program ends, a loop's after
any number of items, so an obligation is decided over the whole program before it runs, where a
monitor reading a run's prefix cannot decide it (Bauer et al. 2011, Def. 3.4). At run time the
same automaton is a monitor stepping on concrete records, and reading the pending states when
the run ends.

A policy reads one program's calls in order. It carries no value from one call to another,
so a rule about the same parcel across two calls is read as a rule about any two calls: a
program approving one parcel and booking another passes a rule that a booking follows its own
parcel's approval. A rule that a call takes a value another returned is a flow rule with
``only`` (`valgebra_planning.flows`). A policy instance per value, a parametric property
(Roşu and Chen 2012, Def. 12), is not read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from valgebra_planning._valgebra import Validator, compile_schema, intersection, nothing
from valgebra_planning.contracts import keys_read

__all__ = ["Monitor", "Policy", "Reach", "Transition", "read_policy"]

Reach = Literal["never", "possibly", "surely"]

#: What `Monitor.stepped` is given in place of a result for a call made and not yet returned.
_UNRETURNED: Any = object()


@dataclass(frozen=True, slots=True)
class Transition:
    """From ``source`` to ``target`` on a call to ``tool`` whose arguments lie in ``guard``.

    With ``hint`` set and ``tool`` empty, the call is to any tool whose annotations give that hint,
    ``destructiveHint`` or another, MCP's default read where a tool sets none, so a tool setting
    no annotation is destructive: a hint its server gives, untrusted unless the server is.
    """

    source: str
    tool: str
    guard: Validator[Any]
    target: str
    hint: str = ""
    #: The set the call's result must lie in, for a transition taken when the result arrives;
    #: None for one taken when the call is made.
    result: Validator[Any] | None = None

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
        never left. A transition reading the result is stepped after those reading the call,
        from the states they leave, and is taken possibly at most: no result is known here.
        """
        made = self._stepped(states, tool, record, hints, returned=False)
        return self._stepped(made, tool, record, hints, returned=True)

    def _stepped(
        self,
        states: frozenset[str],
        tool: str,
        record: Validator[Any],
        hints: frozenset[str],
        *,
        returned: bool,
    ) -> frozenset[str]:
        after: set[str] = set()
        for state in states:
            if state in self.bad:
                after.add(state)
                continue
            stays = True
            for transition in self.transitions:
                if (
                    transition.source != state
                    or not transition.reads(tool, hints)
                    or (transition.result is not None) != returned
                ):
                    continue
                taken = _taken(record, transition.guard)
                if returned and taken == "surely":
                    taken = "possibly"
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


#: The keys a policy and each of its transitions read.
_POLICY_KEYS = frozenset({"name", "initial", "bad", "transitions", "pending"})
_TRANSITION_KEYS = frozenset({"from", "to", "tool", "hint", "arguments", "result"})


def read_policy(spec: Mapping[str, Any], *, assert_format: bool = False) -> Policy:
    """Read a policy: ``name``, ``initial``, ``bad`` states, ``transitions`` and ``pending``.

    Each transition names its ``from`` and ``to`` states and its ``tool``, or the annotation
    ``hint`` a tool must set, and optionally the JSON Schema its ``arguments`` record must meet,
    every record where it names none. With ``result``, the JSON Schema the call's result must
    meet, the transition is taken when the result arrives rather than when the call is made.
    A key neither reads is refused, naming it, and so is a
    transition naming both a tool and a hint, or neither. With ``assert_format``, ``format``
    in a guard is read as an assertion, as the library's contracts read it.
    """
    keys_read(spec, _POLICY_KEYS, f"policy {spec.get('name')!r}")
    for t in spec["transitions"]:
        where = f"policy {spec.get('name')!r}, a transition from {t.get('from')!r}"
        keys_read(t, _TRANSITION_KEYS, where)
        if ("tool" in t) == ("hint" in t):
            message = f"{where}: a transition names a tool or a hint, one of the two"
            raise ValueError(message)
    transitions = tuple(
        Transition(
            source=t["from"],
            tool=t.get("tool", ""),
            guard=compile_schema(t.get("arguments", True), assert_format=assert_format),
            result=None
            if "result" not in t
            else compile_schema(t["result"], assert_format=assert_format),
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
    """A policy stepped on concrete calls, as a run makes them.

    ``state`` is the state every call observed takes the policy to. A call that answered with
    an error may not have acted, so after one the run may also be in a state ``also`` holds,
    as the verifier reads the error's block from either set of states: a call is refused where
    any state the run may be in reaches a bad one.
    """

    policy: Policy
    state: str = ""
    violations: list[str] = field(default_factory=list)
    #: The other states the run may be in, the states before a call that answered with an error.
    also: frozenset[str] = frozenset()
    #: The states held before the call observed last.
    before: frozenset[str] = field(default=frozenset(), init=False, repr=False)

    def __post_init__(self) -> None:
        self.state = self.state or self.policy.initial

    def held(self) -> frozenset[str]:
        """Return every state the run may be in."""
        return self.also | {self.state}

    def stepped(
        self,
        state: str,
        tool: str,
        arguments: Mapping[str, Any],
        hints: frozenset[str],
        result: Any = _UNRETURNED,
    ) -> str:
        """Return the state one call takes ``state`` to; a bad state is never left.

        Without ``result`` the transitions read are those taken when the call is made; with it,
        those reading the result.
        """
        if state in self.policy.bad:
            return state
        returned = result is not _UNRETURNED
        for transition in self.policy.transitions:
            if (
                transition.source == state
                and transition.reads(tool, hints)
                and (transition.result is not None) == returned
                and transition.guard.is_valid(dict(arguments))
                and (transition.result is None or transition.result.is_valid(result))
            ):
                return transition.target
        return state

    def observe(
        self, tool: str, arguments: Mapping[str, Any], hints: frozenset[str] = frozenset()
    ) -> bool:
        """Step on one call; return whether it may reach a bad state, which is never left."""
        held = self.held()
        self.before = held
        self.state = self.stepped(self.state, tool, arguments, hints)
        self.also = frozenset(self.stepped(s, tool, arguments, hints) for s in self.also)
        self.also -= {self.state}
        reached = sorted((self.held() & self.policy.bad) - held)
        if reached:
            self.violations.append(f"{self.policy.name}: {tool} reached {reached[0]}")
            return True
        return False

    def returned(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        result: Any,
        hints: frozenset[str] = frozenset(),
    ) -> bool:
        """Step on a call's result; return whether it may reach a bad state, never left."""
        held = self.held()
        self.state = self.stepped(self.state, tool, arguments, hints, result)
        self.also = frozenset(self.stepped(s, tool, arguments, hints, result) for s in self.also)
        self.also -= {self.state}
        reached = sorted((self.held() & self.policy.bad) - held)
        if reached:
            self.violations.append(f"{self.policy.name}: {tool} returned into {reached[0]}")
            return True
        return False

    def errored(self) -> None:
        """Hold the states before the call observed last too: it answered with an error."""
        self.also = (self.also | self.before) - {self.state}

    def ended(self) -> bool:
        """Return whether the run may end in a pending state: an obligation it leaves open."""
        pending = sorted(self.held() & self.policy.pending)
        if not pending:
            return False
        self.violations.append(f"{self.policy.name}: the run ended in {pending[0]}")
        return True
