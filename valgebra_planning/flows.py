"""Where a value came from, and which arguments it may reach.

A program's control flow is fixed before any tool runs, so the origin of every value it
passes on is known from the program alone: a task input, or the result of a named tool. A
flow rule names untrusted sources and a sink argument. A value from a source reaches the sink
only where valgebra proves it inside the rule's endorsed set, when the rule names one, the
narrowing a test or a sanitizing tool performs; otherwise it does not reach it at all. This
is the integrity half of information-flow control for agents, a value's provenance as its
capability (CaMeL, Debenedetti et al. 2025, §5.3) and its integrity as its label (FIDES, Costa
et al. 2025, §4.1), and a privilege rule over arguments (Progent, Shi et al. 2025, §4.1),
decided over sets rather than checked per value.

With ``implicit``, a sink call made under a test on a value from a source is refused too:
which branch runs carries that value's influence, the dependency on a condition that CaMeL's
STRICT mode adds (Debenedetti et al. 2025, §5.4).

With ``only``, the rule holds every origin but its sources: a value from a tool it does not
name, from a task input it does not name, or written into the program as a literal reaches the
sink only inside the endorsed set. That is integrity as provenance: an identifier must come
from the tool that retrieved it, on every path, which refutes the identifier a model makes up to
pass an authentication step, the integrity leak (Sah et al. 2026, §4.3).

An ``only`` rule may also read when and for what a value was returned. With ``fresh``, a value its
sources returned before a call that may act reaches the sink no more: an action changes the world
a reading reports (Mansoor et al. 2026, §4.1). With ``key``, an argument's name, a value reaches
the sink only from a source call that took the same value at that argument as the sink's call,
a slice of the trace per value of the key (Roşu and Chen 2012, Def. 11), as a distance measured
for one object and passed for another is refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from valgebra_planning._valgebra import Validator, compile_schema
from valgebra_planning.contracts import Contract, flag, keys_read, scope_of

__all__ = ["Flow", "read_flow"]


@dataclass(frozen=True, slots=True)
class Flow:
    """Values from the origins the rule holds reach ``tool.argument`` only inside ``endorsed``.

    It holds ``sources``, or, with ``only``, every origin but them.
    """

    name: str
    sources: frozenset[str]
    tool: str
    argument: str
    endorsed: Validator[Any] | None
    implicit: bool
    only: bool = False
    #: Whether a value its sources returned before a call that may act no longer reaches the sink.
    fresh: bool = False
    #: The argument a source call and the sink's call must take the same value at, or empty.
    key: str = ""
    #: Whom a proxy holds what the sources returned for: every client together, or each
    #: principal a token names apart; a program runs as one principal.
    scope: str = "shared"

    def holds(self, origin: str | None) -> bool:
        """Return whether a value from ``origin`` is held to the rule; None is a literal."""
        return (origin in self.sources) != self.only

    @property
    def sink(self) -> str:
        """Return the sink as a program writes it."""
        return f"{self.tool}.{self.argument}"


#: The keys a flow rule reads.
_FLOW_KEYS = frozenset(
    {"name", "sources", "sink", "endorsed", "implicit", "only", "fresh", "key", "scope"}
)


def read_flow(spec: Mapping[str, Any], *, assert_format: bool = False) -> Flow:
    """Read a flow rule from its JSON form.

    It holds ``name``, ``sources``, ``sink`` as ``tool.argument``, and optionally the
    ``endorsed`` JSON Schema, ``implicit`` and ``only``, and with ``only``, ``fresh`` and
    ``key``. A source is a tool's name or a task input's. A key it does not read is a
    `ValueError` naming it, and so are a sink that names no argument and ``fresh`` or ``key``
    without ``only``, which would admit a value a rule refusing its sources' values refuses; a
    flag that is no boolean, and a ``key`` that is no argument's name, is a `TypeError`.
    ``scope``, ``"shared"`` or ``"principal"``, names whom a proxy holds what the sources
    returned for, every client together or each principal apart. With
    ``assert_format``, ``format`` in the endorsed set is read as an assertion, as the library's
    contracts read it.
    """
    where = f"flow {spec.get('name')!r}"
    keys_read(spec, _FLOW_KEYS, where)
    # The last dot, as MCP allows one in a tool's name: `g1.grasp.distance` is `g1.grasp`'s.
    tool, _, argument = spec["sink"].rpartition(".")
    if not tool or not argument:
        message = f"{where}: the sink {spec['sink']!r} is no tool.argument"
        raise ValueError(message)
    implicit, only = flag(spec, "implicit", where), flag(spec, "only", where)
    fresh, key = flag(spec, "fresh", where), spec.get("key", "")
    if not isinstance(key, str):
        message = f"{where}: 'key' is an argument's name, not {key!r}"
        raise TypeError(message)
    if (fresh or key) and not only:
        message = f"{where}: 'fresh' and 'key' read a value's return, so they need 'only'"
        raise ValueError(message)
    endorsed = spec.get("endorsed")
    return Flow(
        name=spec["name"],
        sources=frozenset(spec["sources"]),
        tool=tool,
        argument=argument,
        endorsed=None
        if endorsed is None
        else compile_schema(endorsed, assert_format=assert_format),
        implicit=implicit,
        only=only,
        fresh=fresh,
        key=key,
        scope=scope_of(spec, where),
    )


def check_flow(
    flow: Flow, contracts: Mapping[str, Contract], inputs: frozenset[str] = frozenset()
) -> None:
    """Refuse a flow rule naming a sink, an argument or a source the listing lacks.

    A sink no tool listed is, an argument its tool does not take, or a source that is neither a
    listed tool nor one of ``inputs``, the task inputs a library names, makes the rule read
    nothing, so a value reaches the sink it guards unchecked: ``grasp.dist`` for
    ``grasp.distance`` held no grasp. A rule here is refused naming it, a `ValueError`.
    """
    where = f"flow {flow.name!r}"
    contract = contracts.get(flow.tool)
    if contract is None:
        message = f"{where}: the sink {flow.tool}.{flow.argument} names no tool listed"
        raise ValueError(message)
    if flow.argument not in {slot.path[0] for slot in contract.arguments}:
        message = f"{where}: {flow.tool} takes no argument {flow.argument!r}"
        raise ValueError(message)
    for source in sorted(flow.sources - contracts.keys() - inputs):
        named = "no tool listed and no task's input" if inputs else "no tool listed"
        message = f"{where}: the source {source!r} names {named}"
        raise ValueError(message)
