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
file opened before it (Kamath et al. 2025, §5.1). The monitor holds the instances at run time,
and the walk and the planner hold them by the names holding each value
(`valgebra_planning._instances`).

A state may lapse (`Lapse`): it is left for a named state once it was held its seconds, with no
call. The monitor reads it by a clock at each event. The walk reads no time and takes no lapse, and
a link that rests on one not coming due names the timing (`valgebra_planning.verify`).
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple, TypeAlias

from valgebra_planning._valgebra import (
    ANNOTATIONS,
    Validator,
    compile_schema,
    complement,
    intersection,
    json_key,
    nothing,
    union,
)
from valgebra_planning.contracts import MCP_HINTS, Contract, keys_read, scope_of

__all__ = [
    "Lapse",
    "Monitor",
    "Policy",
    "Reach",
    "Snapshot",
    "Span",
    "Transition",
    "listing",
    "read_policy",
]

Reach = Literal["never", "possibly", "surely"]

#: A time by a monitor's clock, in seconds, as the earliest and the latest it may be: one time
#: where it was read, and two where an order of calls leaves when a state was entered open, the
#: latest an infinity while the call that enters it may still act.
Span: TypeAlias = "tuple[float, float]"


class Snapshot(NamedTuple):
    """A monitor's states, as `Monitor.snapshot` returns them and `Monitor.restore` takes them."""

    state: str
    #: The others the run may be in, and those held before the call observed last.
    also: frozenset[str]
    before: frozenset[str]
    #: The value it is the instance of, and each instance's states, by its value.
    value: str
    instances: dict[object, Snapshot]
    #: When the states held, and those before that call, were entered, and when it was made.
    entered: dict[str, frozenset[Span]]
    earlier: dict[str, frozenset[Span]]
    called: Span


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
class Lapse:
    """From ``source`` to ``target`` once ``source`` has been held ``seconds``, with no call.

    The time is an event-recording clock's, the time since the event that entered the state
    occurred last (Bauer et al. 2011, Def. 4.2), so a monitor decides the lapse at each event
    from the times of events already seen; a walk of a program, whose events have no times, does
    not read it.
    """

    source: str
    seconds: float
    target: str


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
    #: Whom a proxy holds the policy's states for: ``"shared"``, every client together, or
    #: ``"principal"``, each principal a token names apart (`valgebra_planning.proxy.Gate`).
    scope: str = "shared"
    #: The states that lapse, each once and to one state, no chain of them leading back.
    lapses: tuple[Lapse, ...] = ()

    def lapse(self, state: str) -> Lapse | None:
        """Return the lapse out of ``state``, or None where it is held until a call leaves it."""
        return next((lapse for lapse in self.lapses if lapse.source == state), None)

    def due(self, states: frozenset[str]) -> frozenset[str]:
        """Return ``states`` and every one their lapses may move them to, whatever time passed."""
        reached = set(states)
        for state in states:
            lapse = self.lapse(state)
            while lapse is not None and lapse.target not in reached:
                reached.add(lapse.target)
                lapse = self.lapse(lapse.target)
        return frozenset(reached)

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
        *,
        late: bool = False,
        instance: bool = False,
    ) -> frozenset[str]:
        """Return the states the automaton may be in after a call whose record lies in ``record``.

        A transition taken surely leaves its source; one taken possibly adds its target and
        keeps the source, as does a call no guard of the state surely matches. A bad state is
        never left. A state's transitions are read in file order, each on what the earlier ones
        leave: the record outside each earlier guard on the call, and the result outside each
        earlier guard on the result whose guard on the call surely holds, so a state's guards meet
        in nothing, a deterministic automaton (D'Antoni and Veanes 2017, §2), and the walk takes
        no transition the monitor's first match would not. A
        transition reading the result is stepped after those reading the call, from the states
        they leave: where ``result`` holds the set the result lies in, as in one case of a split
        on it, its guard is read against that set; otherwise it is taken possibly at most, no
        result being known.

        A lapse is not taken, the events of a walk having no times; with ``late``, each may have
        come due before the call, and before its result. A policy with ``per`` takes each
        transition possibly at most, a call being maybe another value's event, unless
        ``instance`` says ``states`` are one instance's and the call that instance's event.
        """
        waited = self.due if late else frozenset
        made = self._stepped(waited(states), tool, record, hints, returned=False, instance=instance)
        return self._stepped(
            waited(made), tool, record, hints, returned=True, result=result, instance=instance
        )

    def sent(
        self,
        states: frozenset[str],
        tool: str,
        record: Validator[Any],
        hints: frozenset[str] = frozenset(),
    ) -> frozenset[str]:
        """Return the states the automaton may be in once a call is made and not yet answered."""
        return self._stepped(states, tool, record, hints, returned=False)

    def answered(
        self,
        states: frozenset[str],
        tool: str,
        record: Validator[Any],
        hints: frozenset[str] = frozenset(),
    ) -> frozenset[str]:
        """Return the states the automaton may be in once a call sent in ``states`` is answered.

        No result is known, so a transition reading one is taken possibly at most.
        """
        return self._stepped(states, tool, record, hints, returned=True)

    def _stepped(
        self,
        states: frozenset[str],
        tool: str,
        record: Validator[Any],
        hints: frozenset[str],
        *,
        returned: bool,
        result: Validator[Any] | None = None,
        instance: bool = False,
    ) -> frozenset[str]:
        after: set[str] = set()
        for state in states:
            if state in self.bad:
                after.add(state)
                continue
            reached, stays = self._from(
                state, tool, record, hints, returned=returned, result=result, instance=instance
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
        instance: bool = False,
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
            if self.per and not instance and taken == "surely":
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


#: The keys a policy, each of its transitions and each of its lapses read.
_POLICY_KEYS = frozenset({"name", "initial", "bad", "transitions", "pending", "per", "scope"})
_TRANSITION_KEYS = frozenset({"from", "to", "tool", "hint", "arguments", "result"})
_LAPSE_KEYS = frozenset({"from", "to", "after_s"})


def read_policy(spec: Mapping[str, Any], *, assert_format: bool = False) -> Policy:
    """Read a policy: ``name``, ``initial``, ``bad`` states, ``transitions`` and ``pending``.

    Each transition names its ``from`` and ``to`` states and its ``tool``, or the annotation
    ``hint`` a tool must set, and optionally the JSON Schema its ``arguments`` record must meet,
    every record where it names none. With ``result``, the JSON Schema the call's result must
    meet, the transition is taken when the result arrives rather than when the call is made.
    With ``per``, an argument's name, the policy has an instance for each value it takes.
    With ``scope`` ``"principal"`` a proxy holds an instance for each principal a token names,
    and with ``"shared"``, the default, one for every client; a program runs as one principal,
    so the critic and the runner read either alike. A transition of ``from``, ``after_s`` and
    ``to`` alone is a lapse, taken where the state has been held that many seconds with no call
    leaving it (`Lapse`). A key neither reads is refused, naming it, and so is a transition
    naming both a tool and a hint, or neither, and a ``scope`` that is neither. With
    ``assert_format``, ``format`` in a guard is read as an assertion, as the library's contracts
    read it.
    """
    keys_read(spec, _POLICY_KEYS, f"policy {spec.get('name')!r}")
    lapses = _lapses(spec)
    calls = [t for t in spec["transitions"] if "after_s" not in t]
    for t in calls:
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
        for t in calls
    )
    return Policy(
        spec["name"],
        spec["initial"],
        frozenset(spec["bad"]),
        transitions,
        frozenset(spec.get("pending", ())),
        _per(spec),
        scope_of(spec, f"policy {spec.get('name')!r}"),
        lapses,
    )


def _lapses(spec: Mapping[str, Any]) -> tuple[Lapse, ...]:
    """Return the lapses a policy's transitions hold, refusing one no monitor could hold.

    A bad state is never left, so no lapse leaves one; a state lapses once, to one state; no
    lapse is out of a state nothing enters, as a misspelled one is, which would hold a yes for
    ever; and no chain of lapses leads back where it began, which would move a policy in a circle
    for as long as no call is made.
    """
    lapses: dict[str, Lapse] = {}
    entered = {spec["initial"], *(t["to"] for t in spec["transitions"])}
    for t in spec["transitions"]:
        if "after_s" not in t:
            continue
        source, seconds = t.get("from"), t["after_s"]
        where = f"policy {spec.get('name')!r}, a lapse from {source!r}"
        keys_read(t, _LAPSE_KEYS, where)
        known = isinstance(seconds, int | float) and not isinstance(seconds, bool)
        if not known or not math.isfinite(seconds) or seconds <= 0:
            message = f"{where}: 'after_s' is a positive number of seconds"
            raise ValueError(message)
        if source in spec["bad"]:
            message = f"{where}: a bad state is never left"
            raise ValueError(message)
        if source not in entered:
            message = f"{where}: no transition enters it"
            raise ValueError(message)
        if source in lapses:
            message = f"{where}: a state lapses once, and it lapses to {lapses[source].target!r}"
            raise ValueError(message)
        lapses[source] = Lapse(source, float(seconds), t["to"])
    for source in lapses:
        chain = [source]
        while chain[-1] in lapses and len(chain) <= len(lapses):
            chain.append(lapses[chain[-1]].target)
        if chain[-1] in chain[:-1]:
            message = f"policy {spec.get('name')!r}: the lapses lead back, {' to '.join(chain)}"
            raise ValueError(message)
    return tuple(lapses.values())


def _per(spec: Mapping[str, Any]) -> str:
    """Return the argument a policy has an instance per value of, empty where it names none."""
    per = spec.get("per", "")
    if not isinstance(per, str):
        message = f"policy {spec.get('name')!r}: 'per' is an argument's name"
        raise TypeError(message)
    return per


def _answered(schema: object) -> str:
    """Return how a way out names the result a transition reads: the test a program writes."""
    read = listing(schema)
    if read is None or not read[0]:
        return ", by its result,"
    field, values = ".".join(read[0]), read[1]
    said = repr(values[0]) if len(values) == 1 else f"in {values!r}"
    return f" with {field} {said}"


def listing(schema: object) -> tuple[tuple[str, ...], tuple[Any, ...]] | None:
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

    Where the policy has lapses, ``entered`` holds when each state held that lapses may have been
    entered, by ``clock``, and a state held for its lapse's seconds moves to the lapse's target
    before each step, before `ended` and before `held`. A policy with none reads no clock.
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
    #: The clock lapses are read by, in seconds.
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    #: When each state held that lapses may have been entered, and each held before the call
    #: observed last, and when that call was made.
    entered: dict[str, frozenset[Span]] = field(default_factory=dict, repr=False)
    earlier: dict[str, frozenset[Span]] = field(default_factory=dict, init=False, repr=False)
    called: Span = field(default=(0.0, 0.0), init=False, repr=False)

    def __post_init__(self) -> None:
        self.state = self.state or self.policy.initial
        unread = [
            state
            for state in self.also | {self.state}
            if state not in self.entered and self.policy.lapse(state) is not None
        ]
        if unread:
            self.entered = self.entered | dict.fromkeys(unread, frozenset({self.now()}))

    def now(self) -> Span:
        """Return the time, as a lapse reads it; a policy with no lapse reads no clock."""
        if not self.policy.lapses:
            return (0.0, 0.0)
        read = self.clock()
        return (read, read)

    def held(self) -> frozenset[str]:
        """Return every state the run may be in, in any instance, as of now."""
        if self.policy.lapses:
            self.elapse(self.now())
        return self.holding()

    def holding(self) -> frozenset[str]:
        """Return every state held, in any instance, as of the last time read."""
        held = self.also | {self.state}
        return held.union(*(instance.holding() for instance in self.instances.values()))

    def elapse(self, at: Span) -> bool:
        """Move each state held past its lapse, as of ``at``; return whether one reaches a bad.

        A state entered at ``e`` lapses at ``e + seconds``, where its target is entered, so a
        chain of lapses that came due between two events is read in the order they came. Of a
        time known to lie between two, each state it may leave held is held.
        """
        if not self.policy.lapses:
            return False
        for instance in [self, *self.instances.values()]:
            before = instance.also | {instance.state}
            moved = instance.lapsed(at)
            if bad := sorted((moved - before) & self.policy.bad):
                self.violations.append(f"{self.named(instance)}: a state lapsed into {bad[0]}")
                return True
        return False

    def lapsed(self, at: Span) -> frozenset[str]:
        """Move this instance's own states past their lapses, as of ``at``; return those held."""
        soonest, latest = at
        kept: dict[str, set[Span]] = {
            state: set() for state in self.also | {self.state} if self.policy.lapse(state) is None
        }
        waiting = [(state, span) for state, spans in self.entered.items() for span in spans]
        first = self.state
        while waiting:
            state, (low, high) = waiting.pop()
            lapse = self.policy.lapse(state)
            if lapse is None:
                kept.setdefault(state, set())
                continue
            if low + lapse.seconds <= latest:
                due = min(high + lapse.seconds, latest)
                waiting.append((lapse.target, (low + lapse.seconds, due)))
            if high + lapse.seconds > soonest:
                kept.setdefault(state, set()).add((max(low, soonest - lapse.seconds), high))
        while first not in kept and (lapse := self.policy.lapse(first)) is not None:
            first = lapse.target
        self.hold(first, kept)
        return frozenset(kept)

    def settle(self, at: Span) -> None:
        """Read every state held as entered by ``at``: no call that may enter one is in flight."""
        for instance in [self, *self.instances.values()]:
            instance.entered = {
                state: frozenset((low, min(high, at[1])) for low, high in spans)
                for state, spans in instance.entered.items()
            }

    def hold(self, state: str, held: Mapping[str, Iterable[Span]]) -> None:
        """Hold ``state`` and beside it every other of ``held``, entered when it says."""
        self.state, self.also = state, frozenset(held) - {state}
        self.entered = {
            name: frozenset(spans)
            for name, spans in held.items()
            if self.policy.lapse(name) is not None
        }

    def beliefs(self) -> dict[str, frozenset[Span]]:
        """Return each state this instance holds, and when it may have been entered."""
        return {state: self.entered.get(state, frozenset()) for state in self.also | {self.state}}

    def add(self, states: Iterable[str], entered: Mapping[str, Iterable[Span]]) -> None:
        """Hold ``states`` beside those held, each entered when ``entered`` says."""
        held: dict[str, set[Span]] = {name: set(spans) for name, spans in self.beliefs().items()}
        for state in states:
            held.setdefault(state, set()).update(entered.get(state, ()))
        self.hold(self.state, held)

    def snapshot(self) -> Snapshot:
        """Return what a refused call restores: every instance's states and their times."""
        return Snapshot(
            self.state,
            self.also,
            self.before,
            self.value,
            {key: instance.snapshot() for key, instance in self.instances.items()},
            # Neither map is changed in place: each step holds a new one.
            self.entered,
            self.earlier,
            self.called,
        )

    @classmethod
    def at(
        cls, policy: Policy, snapshot: Snapshot, clock: Callable[[], float] = time.monotonic
    ) -> Monitor:
        """Return a monitor of ``policy`` holding the states ``snapshot`` holds."""
        monitor = cls(policy, snapshot.state, clock=clock, entered=snapshot.entered)
        monitor.restore(snapshot)
        return monitor

    def holds(self, snapshot: Snapshot) -> bool:
        """Return whether these are the states ``snapshot`` held, as time has moved them since."""
        at = self.now()
        other = Monitor.at(self.policy, snapshot, self.clock)
        other.elapse(at)
        self.elapse(at)
        return self.snapshot() == other.snapshot()

    def join(self, snapshot: Snapshot) -> None:
        """Hold beside these states every one ``snapshot`` holds, every instance's among them.

        Two orders the same calls may have taken are two executions the run may be on, so the
        belief holds the states each leaves (Bertoli et al. 2006, §3).
        """
        self.add(snapshot.also | {snapshot.state}, snapshot.entered)
        for key, held in snapshot.instances.items():
            instance = self.instances.get(key)
            if instance is None:
                self.instances[key] = Monitor.at(self.policy, held, self.clock)
            else:
                instance.join(held)

    def restore(self, snapshot: Snapshot) -> None:
        """Return to the states `snapshot` holds, every instance's among them."""
        self.state, self.also, self.before = snapshot.state, snapshot.also, snapshot.before
        self.entered, self.earlier = snapshot.entered, snapshot.earlier
        self.value, self.called = snapshot.value, snapshot.called
        self.instances = {
            key: Monitor.at(self.policy, held, self.clock)
            for key, held in snapshot.instances.items()
        }

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
        taken = self.taken(state, tool, arguments, hints, result)
        return state if taken is None else taken.target

    def taken(
        self,
        state: str,
        tool: str,
        arguments: Mapping[str, Any],
        hints: frozenset[str],
        result: Any = _UNRETURNED,
    ) -> Transition | None:
        """Return the transition one call takes from ``state``, or None where it stays."""
        if state in self.policy.bad:
            return None
        returned = result is not _UNRETURNED
        for transition in self.policy.transitions:
            if (
                transition.source == state
                and transition.reads(tool, hints)
                and (transition.result is not None) == returned
                and transition.guard.is_valid(dict(arguments))
                and (transition.result is None or transition.result.is_valid(result))
            ):
                return transition
        return None

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
            instance = Monitor(
                self.policy,
                self.state,
                also=self.also,
                value=value,
                clock=self.clock,
                entered=self.entered,
            )
            self.instances[key] = instance
        return [instance]

    def moved(
        self, tool: str, arguments: Mapping[str, Any], hints: frozenset[str], result: Any, at: Span
    ) -> list[str]:
        """Step this instance alone on one call or its result; return the bad states it reaches.

        A state a transition enters is entered at ``at``, one it was already in among them, as an
        event's clock is reset each time the event occurs (Bauer et al. 2011, Def. 4.2).
        """
        if not self.policy.lapses:
            return self.moved_at_once(tool, arguments, hints, result)
        held = self.beliefs()
        if result is _UNRETURNED:
            self.before, self.earlier, self.called = frozenset(held), self.entered, at
        moved: dict[str, set[Span]] = {}
        first = self.state
        for state, spans in held.items():
            taken = self.taken(state, tool, arguments, hints, result)
            target = state if taken is None else taken.target
            moved.setdefault(target, set()).update(spans if taken is None else {at})
            if state == self.state:
                first = target
        self.hold(first, moved)
        return sorted((self.also | {self.state}) & self.policy.bad - held.keys())

    def moved_at_once(
        self, tool: str, arguments: Mapping[str, Any], hints: frozenset[str], result: Any
    ) -> list[str]:
        """Step an instance whose states hold no time, as `moved` steps one; return the bad ones."""
        held = self.also | {self.state}
        if result is _UNRETURNED:
            self.before = held
        self.state = self.stepped(self.state, tool, arguments, hints, result)
        self.also = frozenset(self.stepped(s, tool, arguments, hints, result) for s in self.also)
        self.also -= {self.state}
        return sorted(((self.also | {self.state}) & self.policy.bad) - held)

    def step(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        hints: frozenset[str],
        result: Any,
        at: Span | None = None,
        entered: Span | None = None,
    ) -> bool:
        """Step every instance a call reaches; record the first bad state one reaches.

        The states past their lapses are moved first, as of ``at``, the time now where it is
        None; a state the step enters is entered at ``entered``, at ``at`` where it is None.
        """
        at = self.now() if at is None else at
        if self.policy.lapses and self.elapse(at):
            return True
        stepped = self.stepping(arguments)
        if result is _UNRETURNED:
            self.touched = stepped
        for instance in stepped:
            reached = instance.moved(tool, arguments, hints, result, entered or at)
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
        self,
        tool: str,
        arguments: Mapping[str, Any],
        hints: frozenset[str] = frozenset(),
        at: Span | None = None,
        entered: Span | None = None,
    ) -> bool:
        """Step on one call; return whether it may reach a bad state, which is never left."""
        return self.step(tool, arguments, hints, _UNRETURNED, at, entered)

    def returned(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        result: Any,
        hints: frozenset[str] = frozenset(),
        at: Span | None = None,
    ) -> bool:
        """Step on a call's result; return whether it may reach a bad state, never left."""
        return self.step(tool, arguments, hints, result, at)

    def ways_out(
        self,
        tool: str,
        hints: frozenset[str] = frozenset(),
        arguments: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Return each call that moves the run on from where a refused call to ``tool`` leaves it.

        Each is a transition from a state the run may be in to one that is not bad, on a call
        other than ``tool``'s, as `from start, approve_freight leads to approved`: the calls the
        policy waits for. Those into a state that admits the refused call are named alone where
        there are any, a no that leads back left out. A transition reading a result is named by
        the test a program writes where its guard reads one field's members, `confirm with status
        'yes'`, and otherwise as reading its result; one reading a hint by the hint; each is named
        once, in the order the policy lists them. A call carrying the ``per`` argument in
        ``arguments`` is an event of its value's instance alone (Roşu and Chen 2012, Def. 11), so
        the states read are that instance's, or, for a value no call has carried, the states a
        new instance starts in; no instance is made.
        """
        held = self.instance_held(arguments) - self.policy.bad
        named: list[str] = []
        moves: list[str] = []
        for lapse in self.policy.lapses:
            if lapse.source in held and lapse.target not in self.policy.bad:
                way = f"from {lapse.source}, {lapse.seconds:g} s lead to {lapse.target}"
                (named if self.admits(lapse.target, tool, hints) else moves).append(way)
        for transition in self.policy.transitions:
            if (
                transition.source not in held
                or transition.target in self.policy.bad
                or transition.reads(tool, hints)
            ):
                continue
            call = transition.tool or f"a call setting {transition.hint}"
            if transition.result is not None:
                call += _answered(transition.result_schema)
            way = f"from {transition.source}, {call} leads to {transition.target}"
            if way not in named and self.admits(transition.target, tool, hints):
                named.append(way)
            elif way not in moves:
                moves.append(way)
        # A way that leads the refused call out comes first; where none does in one call, each
        # call that moves the policy on is named, the first of a longer way among them.
        return named or moves

    def instance_held(self, arguments: Mapping[str, Any] | None = None) -> frozenset[str]:
        """Return the states a call carrying ``arguments`` steps from, every instance's for none.

        A call carrying the ``per`` argument steps its value's instance, which starts where the
        calls carrying none left the policy (`stepping`); this reads it and makes none.
        """
        every = self.held()
        per = self.policy.per
        if not per or arguments is None or per not in arguments:
            return every
        instance = self.instances.get(json_key(arguments[per]))
        return instance.holding() if instance is not None else self.also | {self.state}

    def admits(self, state: str, tool: str, hints: frozenset[str]) -> bool:
        """Return whether no transition reading a call to ``tool`` takes ``state`` to a bad one."""
        return not any(
            transition.source == state
            and transition.target in self.policy.bad
            and transition.reads(tool, hints)
            for transition in self.policy.transitions
        )

    def errored(self) -> None:
        """Hold the states before the call observed last too: it answered with an error."""
        for instance in self.touched or [self]:
            instance.add(instance.before, instance.earlier)

    def unread(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        hints: frozenset[str],
        at: Span | None = None,
    ) -> bool:
        """Hold every state the call observed last may have reached, its outcome unread.

        A call whose result is refused, which is canceled, or which no result answers may have
        acted or not, as a call answering with an error may, and may have returned any result. A
        result no guard reads is no observation, so the run may be anywhere in the call's image
        (Bertoli et al. 2006, Def. 3.4): beside the states before the call, every state a
        transition reading its result reaches from the states the call left. Return whether a
        bad state may now be held, recording it.
        """
        at = self.now() if at is None else at
        lapsed = self.elapse(at)
        left = [
            (instance, instance.also | {instance.state}) for instance in self.stepping(arguments)
        ]
        self.errored()
        lapsed = self.elapse(at) or lapsed
        return self.reach(tool, arguments, hints, left, at) or lapsed

    def imaged(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        hints: frozenset[str],
        within: Span | None = None,
    ) -> bool:
        """Hold every state a transition reading the call's result reaches beside those held.

        The call was made and stepped, and its result is not read: this is `unread` with the
        states before the call left out, for a call known to have been made. ``within`` is when
        the result may have arrived, between the call and now where it is None. Return whether a
        bad state may now be held, recording it.
        """
        at = self.now() if within is None else within
        lapsed = self.elapse(at)
        left = [
            (instance, instance.also | {instance.state}) for instance in self.stepping(arguments)
        ]
        return self.reach(tool, arguments, hints, left, at) or lapsed

    def reach(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        hints: frozenset[str],
        left: list[tuple[Monitor, frozenset[str]]],
        at: Span,
    ) -> bool:
        """Hold each state a transition reading a result reaches from ``left``; flag a bad one.

        A result not read arrived at no time known: a state it entered was entered between the
        call and ``at``.
        """
        for instance, held in left:
            span = (min(instance.called[0], at[0]), at[1])
            reached = frozenset(
                transition.target
                for state in held - self.policy.bad
                for transition in self.policy.transitions
                if transition.source == state
                and transition.result is not None
                and transition.reads(tool, hints)
                and transition.guard.is_valid(dict(arguments))
            )
            instance.add(reached, dict.fromkeys(reached, (span,)))
            if bad := sorted(reached & self.policy.bad):
                self.violations.append(
                    f"{self.named(instance)}: {tool} may have returned into {bad[0]}"
                )
                return True
        return False

    def ended(self) -> bool:
        """Return whether the run may end in a pending state: an obligation it leaves open."""
        if self.elapse(self.now()):
            return True
        for instance in [self, *self.instances.values()]:
            pending = sorted((instance.also | {instance.state}) & self.policy.pending)
            if pending:
                self.violations.append(f"{self.named(instance)}: the run ended in {pending[0]}")
                return True
        return False


def check_policy(policy: Policy, contracts: Mapping[str, Contract]) -> None:
    """Refuse a policy naming a tool the listing lacks, or a hint no tool can set.

    A transition on a tool no server lists, or on a hint that is none of MCP's and that no
    listed tool sets, reads no call, so the policy holds nothing it seems to: a misspelled
    ``walk`` for ``walk_to`` or ``destructivehint`` for ``destructiveHint`` would leave the
    motion it names unguarded. Progent warns on a tool or an argument its policy names and no
    server offers (``policy_type_check.py``); a rule here is refused naming it, a `ValueError`.
    """
    hints = MCP_HINTS.union(*(contract.hints for contract in contracts.values()))
    for transition in policy.transitions:
        if transition.hint and transition.hint not in hints:
            message = (
                f"policy {policy.name!r}: the hint {transition.hint!r} is none MCP names and "
                "no tool listed sets"
            )
            raise ValueError(message)
        if not transition.hint and transition.tool not in contracts:
            message = (
                f"policy {policy.name!r}: a transition reads {transition.tool!r}, and no tool "
                "listed is it"
            )
            raise ValueError(message)
