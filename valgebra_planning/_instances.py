"""The instances of a policy with ``per`` a walk or a search holds, by the keys holding a value.

A policy naming an argument in ``per`` is one automaton for each value the argument takes (Roşu
and Chen 2012, Def. 12). A program's text names values by references, so a walk holds each
instance by the set of keys whose value it is, a key a reference to one value on the path: the
walk's is a version read at one path or a literal, the planner's the reference its search bound.
Both read what two keys hold through ``apart``, which says where no run gives them one value.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from typing import Any, NamedTuple

from valgebra_planning.policy import Policy

#: The keys whose values one policy's instances are told apart by, at most, on a path: past them
#: the policy steps as one set of states again, every transition taken possibly.
KEYS = 6
#: What a call's ``per`` argument holds, where it holds no key: none at all, the call stepping
#: every instance, or one the walk cannot name, the call stepping as one set of states does.
NO_VALUE: Any = object()
UNNAMED: Any = object()

#: Whether two keys hold two values on every run.
Apart = Callable[[Hashable, Hashable], bool]
#: A policy's step on one set of states.
Step = Callable[[frozenset[str]], frozenset[str]]


class Instances(NamedTuple):
    """The instances of one policy with ``per`` a path may hold, by the keys holding each value.

    ``base`` is the states of the instance of a value no call has carried: a new value's
    instance starts there (Roşu and Chen 2012, Fig. 3), and a call carrying no value steps it.
    ``held`` maps a set of keys to the states of an instance whose value exactly those keys hold
    among ``resolved``, each object read by the set of names holding it, which tells may and must
    alias apart at once (Naeem and Lhoták 2008, §3.1); the empty set is every instance no key
    holds, a past item of a loop among them. ``called`` are the keys a call on the path carried.
    ``weak`` holds past `KEYS`: the states are then ``base`` alone, every transition taken
    possibly, which discards what the keys told apart soundly (Fink et al. 2008, §5.4).
    """

    base: frozenset[str]
    held: frozenset[tuple[frozenset[Hashable], frozenset[str]]] = frozenset()
    resolved: frozenset[Hashable] = frozenset()
    called: frozenset[Hashable] = frozenset()
    weak: bool = False

    def every(self) -> frozenset[str]:
        """Return every state some instance may be in."""
        return self.base.union(*(states for _, states in self.held))

    def collapsed(self) -> Instances:
        """Return the instances as one set of states, as a walk read them with no key."""
        return Instances(self.every(), weak=True)

    def dropped(self, gone: Callable[[Hashable], bool]) -> Instances:
        """Return the instances with each key ``gone`` names forgotten: its values a past one's.

        A loop's item is one object within its iteration and one of the past ones after it, as
        the block an allocation made last is one and those made before it a summary
        (Balakrishnan and Reps 2006, §3): the keys an iteration read hold no value past it.
        """
        if self.weak:
            return self
        held = [(frozenset(k for k in keys if not gone(k)), states) for keys, states in self.held]
        return self._replace(
            held=merged(held),
            resolved=frozenset(key for key in self.resolved if not gone(key)),
            called=frozenset(key for key in self.called if not gone(key)),
        )


def merged(
    held: Iterable[tuple[frozenset[Hashable], frozenset[str]]],
) -> frozenset[tuple[frozenset[Hashable], frozenset[str]]]:
    """Return ``held`` with the states of each set of keys joined."""
    joined: dict[frozenset[Hashable], frozenset[str]] = {}
    for keys, states in held:
        joined[keys] = joined.get(keys, frozenset()) | states
    return frozenset(joined.items())


def initial(policies: Iterable[Policy]) -> tuple[Instances | None, ...]:
    """Return the instances a program starts with: each policy with ``per`` its base alone.

    With no such policy there are none, and no step reads them.
    """
    policies = tuple(policies)
    if not any(policy.per for policy in policies):
        return ()
    return tuple(Instances(frozenset({p.initial})) if p.per else None for p in policies)


def resolved(ones: Instances, key: Hashable, apart: Apart) -> Instances:
    """Return the instances with ``key`` told apart in each: held by it or not.

    Each instance every key of which may hold ``key``'s value splits in two, the one ``key``
    holds and the one it does not, as a focus splits a summary by the access path an operation
    reads (Fink et al. 2008, §5.2); and ``key`` may hold a value no call carried, whose instance
    starts from the base (Roşu and Chen 2012, Fig. 3). An instance no key holds may be any
    value's, so ``key`` may hold its value too.
    """
    if ones.weak or key in ones.resolved:
        return ones
    if len(ones.resolved) >= KEYS:
        return ones.collapsed()
    held = [(frozenset({key}), ones.base)]
    for keys, states in ones.held:
        held.append((keys, states))
        if not any(apart(key, other) for other in keys):
            held.append((keys | {key}, states))
    return ones._replace(held=merged(held), resolved=ones.resolved | {key})


def stepped(ones: Instances, key: Any, step: Step, loose: Step, apart: Apart) -> Instances:
    """Return a policy's instances once a call is made, its argument holding ``key``.

    ``step`` steps one instance's states, and ``loose`` a set of states every transition taken
    possibly. A call carrying no value is an event of every instance (Roşu and Chen 2012, Def.
    11). A call carrying one is an event of that value's instance alone: each instance the key
    may hold is told apart first (`resolved`), and only those it holds are stepped, a strong
    update, as one key holds one value (Fink et al. 2008, §2.2.3). A key no reader can name
    steps the policy as one set of states.

    A step is the union of each state's (`Policy.step`), so each state is stepped once for every
    instance holding it: every one asks valgebra what the call's record meets.
    """
    each: dict[str, frozenset[str]] = {}

    def once(states: frozenset[str]) -> frozenset[str]:
        after: set[str] = set()
        for state in states:
            found = each.get(state)
            if found is None:
                found = each[state] = step(frozenset({state}))
            after |= found
        return frozenset(after)

    if key is UNNAMED and not ones.weak:
        ones = ones.collapsed()
    if not ones.weak and key is not NO_VALUE:
        ones = resolved(ones, key, apart)
    if ones.weak:
        return ones._replace(base=loose(ones.base))
    if key is NO_VALUE:
        held = [(keys, once(states)) for keys, states in ones.held]
        return ones._replace(base=once(ones.base), held=merged(held))
    held = [(keys, once(states) if key in keys else states) for keys, states in ones.held]
    return ones._replace(held=merged(held), called=ones.called | {key})


def joined(one: Instances | None, other: Instances | None, apart: Apart) -> Instances | None:
    """Return the instances either of two paths may hold.

    Each side tells apart the keys the other told apart, so one set of keys names one instance on
    both; a key one side never met holds there the value it would start with.
    """
    if one is None or other is None:
        return other if one is None else one
    if one.weak or other.weak:
        return Instances(one.every() | other.every(), weak=True)
    for key in sorted(one.resolved | other.resolved, key=repr):
        one, other = resolved(one, key, apart), resolved(other, key, apart)
    if one.weak or other.weak:
        return Instances(one.every() | other.every(), weak=True)
    return Instances(
        one.base | other.base,
        merged([*one.held, *other.held]),
        one.resolved | other.resolved,
        one.called & other.called,
    )


def surely(policy: Policy, ones: Instances) -> bool:
    """Return whether a path ends with some instance surely in a bad or pending state.

    The base is the policy's own instance, stepped by every call carrying no value, and a key a
    call carried holds one value, whose instance is exactly one of those holding the key: where
    every state of either is bad or pending, the run that took the path ends so.
    """
    if ones.weak:
        return False
    wrong = policy.bad | policy.pending
    return ones.base <= wrong or any(
        all(states <= wrong for keys, states in ones.held if key in keys) for key in ones.called
    )
