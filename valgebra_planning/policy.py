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
disjoint, and possibly otherwise. A state's transitions on one call are read in file order, each
on what the earlier ones leave, as the monitor takes the first whose guards hold: the walk reads
the deterministic automaton the list is, whose guards from one state meet in nothing (D'Antoni
and Veanes 2017, §2). A path ending in a bad or a pending state surely refutes the
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
``only`` (`valgebra_planning.flows`).

A policy naming an argument in ``per`` is a parametric property, one instance of the automaton
per value that argument takes, each checked on its own slice of the trace (Roşu and Chen 2012,
Def. 12): a call carrying the argument is an event of the instance its value names alone, and a
call not carrying it an event of every instance (Roşu and Chen 2012, Def. 11), so "each object
is confirmed before it is grasped" holds of each object, as Agent-C binds a file read to the
file opened before it (Kamath et al. 2025, §5.1). The monitor holds the instances at run time;
the walk holds no value's identity, so it takes every transition of such a policy possibly at
most, and proves none of its links.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from valgebra_planning._valgebra import (
    Validator,
    compile_schema,
    complement,
    intersection,
    json_key,
    nothing,
    union,
)
from valgebra_planning.contracts import keys_read

__all__ = ["Monitor", "Policy", "Reach", "Snapshot", "Transition", "read_policy"]

Reach = Literal["never", "possibly", "surely"]

#: A monitor's states, as `Monitor.snapshot` returns them: the state, the others the run may
#: be in, those before the call observed last, the value it is the instance of, and each
#: instance's, by its value.
Snapshot: TypeAlias = "tuple[str, frozenset[str], frozenset[str], str, dict[object, Snapshot]]"

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
    #: The JSON Schema ``result`` compiles from, which the planner reads a test off.
    result_schema: object = None

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
    #: The argument whose every value has an instance of its own, or empty for one instance.
    per: str = ""

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
        result: Validator[Any] | None = None,
    ) -> frozenset[str]:
        """Return the states the automaton may be in after a call whose record lies in ``record``.

        A transition taken surely leaves its source; one taken possibly adds its target and
        keeps the source, as does a call no guard of the state surely matches. A bad state is
        never left. A state's transitions are read in file order, each on what the earlier ones
        leave: the record outside each earlier guard on the call, and the result outside each
        earlier guard on the result whose guard on the call surely holds, so the walk takes no
        transition the monitor's first match would not (D'Antoni and Veanes 2017, §2). A
        transition reading the result is stepped after those reading the call, from the states
        they leave: where ``result`` holds the set the result lies in, as in one case of a split
        on it, its guard is read against that set; otherwise it is taken possibly at most, no
        result being known.
        """
        made = self._stepped(states, tool, record, hints, returned=False)
        return self._stepped(made, tool, record, hints, returned=True, result=result)

    def _stepped(
        self,
        states: frozenset[str],
        tool: str,
        record: Validator[Any],
        hints: frozenset[str],
        *,
        returned: bool,
        result: Validator[Any] | None = None,
    ) -> frozenset[str]:
        after: set[str] = set()
        for state in states:
            if state in self.bad:
                after.add(state)
                continue
            reached, stays = self._from(
                state, tool, record, hints, returned=returned, result=result
            )
            after |= reached
            if stays:
                after.add(state)
        return frozenset(after)

    def _from(
        self,
        state: str,
        tool: str,
        record: Validator[Any],
        hints: frozenset[str],
        *,
        returned: bool,
        result: Validator[Any] | None,
    ) -> tuple[set[str], bool]:
        """Return the states the transitions from ``state`` take on a call, and whether it stays.

        The monitor takes the first transition in file order whose guards hold, so a later one is
        taken only by what the earlier ones leave: the record outside each earlier guard on the
        call, and, where an earlier transition's guard on the call surely holds, the result outside
        its guard on the result. Where that guard only possibly holds, nothing is taken away,
        which keeps every state the monitor may reach.
        """
        reached: set[str] = set()
        stays = True
        on_call: list[Validator[Any]] = []
        on_result: list[Validator[Any]] = []
        for transition in self.transitions:
            if (
                transition.source != state
                or not transition.reads(tool, hints)
                or (transition.result is not None) != returned
            ):
                continue
            taken = _taken_after(record, on_call, transition.guard)
            if returned and transition.result is not None:
                met = (
                    "possibly"
                    if result is None
                    else _taken_after(result, on_result, transition.result)
                )
                if taken == "surely" and result is not None:
                    on_result.append(transition.result)
                taken = _both(taken, met)
            else:
                on_call.append(transition.guard)
            if self.per and taken == "surely":
                # A call may be another value's event: no value's identity is held here.
                taken = "possibly"
            if taken != "never":
                reached.add(transition.target)
            if taken == "surely":
                stays = False
        return reached, stays

    def reach(self, states: frozenset[str], *, ended: bool = False) -> Reach:
        """Return whether a bad state is held, or a pending one where a path ends: surely if all.

        A path that has not ended may pass through a pending state.
        """
        bad = states & (self.bad | self.pending if ended else self.bad)
        if not bad:
            return "never"
        return "surely" if bad == states else "possibly"


def _both(one: Reach, other: Reach) -> Reach:
    """Return whether two guards are both met, from whether each is."""
    if "never" in (one, other):
        return "never"
    return "surely" if one == other == "surely" else "possibly"


def _taken_after(
    values: Validator[Any], earlier: list[Validator[Any]], guard: Validator[Any]
) -> Reach:
    """Return whether ``guard`` is met by what the ``earlier`` guards leave of ``values``.

    Where the earlier guards take every value, valgebra is asked the inclusion, which it decides
    where it may leave the emptiness of the difference open, as of a fixpoint's complement.
    """
    if earlier:
        taken = union(*earlier)
        if values.relation_to(taken) == "subset":
            return "never"
        values = intersection(values, complement(taken))
    return _taken(values, guard)


def _taken(record: Validator[Any], guard: Validator[Any]) -> Reach:
    if record.relation_to(nothing) == "subset":
        return "never"
    if record.relation_to(guard) == "subset":
        return "surely"
    if intersection(record, guard).relation_to(nothing) == "subset":
        return "never"
    return "possibly"


#: The keys a policy and each of its transitions read.
_POLICY_KEYS = frozenset({"name", "initial", "bad", "transitions", "pending", "per"})
_TRANSITION_KEYS = frozenset({"from", "to", "tool", "hint", "arguments", "result"})


def read_policy(spec: Mapping[str, Any], *, assert_format: bool = False) -> Policy:
    """Read a policy: ``name``, ``initial``, ``bad`` states, ``transitions`` and ``pending``.

    Each transition names its ``from`` and ``to`` states and its ``tool``, or the annotation
    ``hint`` a tool must set, and optionally the JSON Schema its ``arguments`` record must meet,
    every record where it names none. With ``result``, the JSON Schema the call's result must
    meet, the transition is taken when the result arrives rather than when the call is made.
    With ``per``, an argument's name, the policy has an instance for each value it takes.
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
            result_schema=t.get("result"),
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
        _per(spec),
    )


def _per(spec: Mapping[str, Any]) -> str:
    """Return the argument a policy has an instance per value of, empty where it names none."""
    per = spec.get("per", "")
    if not isinstance(per, str):
        message = f"policy {spec.get('name')!r}: 'per' is an argument's name"
        raise TypeError(message)
    return per


@dataclass(slots=True)
class Monitor:
    """A policy stepped on concrete calls, as a run makes them.

    ``state`` is the state every call observed takes the policy to. A call that answered with
    an error may not have acted, so after one the run may also be in a state ``also`` holds,
    as the verifier reads the error's block from either set of states: a call is refused where
    any state the run may be in reaches a bad one.

    A policy with ``per`` holds an instance for each value its argument takes, in ``instances``
    by the value's JSON text, each started where the calls carrying no value left the policy;
    ``state`` and ``also`` are then the instance those calls step, which every other is too.
    ``instances`` keys each by the value under JSON equality, and ``value`` names it.
    """

    policy: Policy
    state: str = ""
    violations: list[str] = field(default_factory=list)
    #: The other states the run may be in, the states before a call that answered with an error.
    also: frozenset[str] = frozenset()
    #: The states held before the call observed last.
    before: frozenset[str] = field(default=frozenset(), init=False, repr=False)
    #: The instance of each value the ``per`` argument took, by the value under JSON equality.
    instances: dict[object, Monitor] = field(default_factory=dict, repr=False)
    #: The value an instance holds the policy for, as JSON text; empty for the policy's own.
    value: str = ""
    #: The instances the call observed last stepped.
    touched: list[Monitor] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self.state = self.state or self.policy.initial

    def held(self) -> frozenset[str]:
        """Return every state the run may be in, in any instance."""
        held = self.also | {self.state}
        return held.union(*(instance.held() for instance in self.instances.values()))

    def snapshot(self) -> Snapshot:
        """Return what a refused call restores: every instance's states."""
        return (
            self.state,
            self.also,
            self.before,
            self.value,
            {key: instance.snapshot() for key, instance in self.instances.items()},
        )

    def restore(self, snapshot: Snapshot) -> None:
        """Return to the states `snapshot` holds, every instance's among them."""
        state, also, before, value, instances = snapshot
        self.state, self.also, self.before, self.value = state, also, before, value
        self.instances = {}
        for key, held in instances.items():
            instance = Monitor(self.policy)
            instance.restore(held)
            self.instances[key] = instance

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

    def stepping(self, arguments: Mapping[str, Any]) -> list[Monitor]:
        """Return the instances a call steps.

        A call carrying the ``per`` argument is an event of its value's instance alone, started
        where the calls carrying none left the policy; any other call is an event of every
        instance (Roşu and Chen 2012, Def. 11).
        """
        per = self.policy.per
        if not per or per not in arguments:
            return [self, *self.instances.values()]
        key = json_key(arguments[per])
        instance = self.instances.get(key)
        if instance is None:
            value = json.dumps(arguments[per], sort_keys=True)
            instance = Monitor(self.policy, self.state, also=self.also, value=value)
            self.instances[key] = instance
        return [instance]

    def moved(
        self, tool: str, arguments: Mapping[str, Any], hints: frozenset[str], result: Any
    ) -> list[str]:
        """Step this instance alone on one call or its result; return the bad states it reaches."""
        held = self.also | {self.state}
        if result is _UNRETURNED:
            self.before = held
        self.state = self.stepped(self.state, tool, arguments, hints, result)
        self.also = frozenset(self.stepped(s, tool, arguments, hints, result) for s in self.also)
        self.also -= {self.state}
        return sorted(((self.also | {self.state}) & self.policy.bad) - held)

    def step(
        self, tool: str, arguments: Mapping[str, Any], hints: frozenset[str], result: Any
    ) -> bool:
        """Step every instance a call reaches; record the first bad state one reaches."""
        stepped = self.stepping(arguments)
        if result is _UNRETURNED:
            self.touched = stepped
        for instance in stepped:
            reached = instance.moved(tool, arguments, hints, result)
            if reached:
                verb = "reached" if result is _UNRETURNED else "returned into"
                self.violations.append(f"{self.named(instance)}: {tool} {verb} {reached[0]}")
                return True
        return False

    def named(self, instance: Monitor) -> str:
        """Return how a violation names the policy: with the value, for a value's instance."""
        which = f" for {self.policy.per} {instance.value}" if instance.value else ""
        return f"{self.policy.name}{which}"

    def observe(
        self, tool: str, arguments: Mapping[str, Any], hints: frozenset[str] = frozenset()
    ) -> bool:
        """Step on one call; return whether it may reach a bad state, which is never left."""
        return self.step(tool, arguments, hints, _UNRETURNED)

    def returned(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        result: Any,
        hints: frozenset[str] = frozenset(),
    ) -> bool:
        """Step on a call's result; return whether it may reach a bad state, never left."""
        return self.step(tool, arguments, hints, result)

    def ways_out(self, tool: str, hints: frozenset[str] = frozenset()) -> list[str]:
        """Return each call that moves the run on from where a refused call to ``tool`` leaves it.

        Each is a transition from a state the run may be in to one that is not bad, on a call
        other than ``tool``'s, as `from start, approve_freight leads to approved`: the calls the
        policy waits for. A transition reading a result is named by its tool and its result, and
        one reading a hint by the hint; each is named once, in the order the policy lists them.
        """
        held = self.held() - self.policy.bad
        named: list[str] = []
        for transition in self.policy.transitions:
            if (
                transition.source not in held
                or transition.target in self.policy.bad
                or transition.reads(tool, hints)
            ):
                continue
            call = transition.tool or f"a call setting {transition.hint}"
            if transition.result is not None:
                call += ", by its result,"
            way = f"from {transition.source}, {call} leads to {transition.target}"
            if way not in named:
                named.append(way)
        return named

    def errored(self) -> None:
        """Hold the states before the call observed last too: it answered with an error."""
        for instance in self.touched or [self]:
            instance.also = (instance.also | instance.before) - {instance.state}

    def unread(self, tool: str, arguments: Mapping[str, Any], hints: frozenset[str]) -> bool:
        """Hold every state the call observed last may have reached, its outcome unread.

        A call whose result is refused, which is canceled, or which no result answers may have
        acted or not, as a call answering with an error may, and may have returned any result. A
        result no guard reads is no observation, so the run may be anywhere in the call's image
        (Bertoli et al. 2006, Def. 3.4): beside the states before the call, every state a
        transition reading its result reaches from the states the call left. Return whether a
        bad state may now be held, recording it.
        """
        left = [
            (instance, instance.also | {instance.state}) for instance in self.stepping(arguments)
        ]
        self.errored()
        for instance, held in left:
            reached = frozenset(
                transition.target
                for state in held - self.policy.bad
                for transition in self.policy.transitions
                if transition.source == state
                and transition.result is not None
                and transition.reads(tool, hints)
                and transition.guard.is_valid(dict(arguments))
            )
            instance.also = (instance.also | reached) - {instance.state}
            if bad := sorted(reached & self.policy.bad):
                self.violations.append(
                    f"{self.named(instance)}: {tool} may have returned into {bad[0]}"
                )
                return True
        return False

    def ended(self) -> bool:
        """Return whether the run may end in a pending state: an obligation it leaves open."""
        for instance in [self, *self.instances.values()]:
            pending = sorted((instance.also | {instance.state}) & self.policy.pending)
            if pending:
                self.violations.append(f"{self.named(instance)}: the run ended in {pending[0]}")
                return True
        return False
