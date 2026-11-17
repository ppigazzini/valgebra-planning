"""Where a value came from, and which arguments it may reach.

A program's control flow is fixed before any tool runs, so the origin of every value it
passes on is known from the program alone: a task input, or the result of a named tool. A
flow rule names untrusted sources and a sink argument. A value from a source reaches the sink
only where valgebra proves it inside the rule's endorsed set, when the rule names one, the
narrowing a test or a sanitizing tool performs; otherwise it does not reach it at all. This
is the integrity half of information-flow control for agents (CaMeL, Debenedetti et al.
2025; FIDES, Costa et al. 2025), and a privilege rule over arguments (Progent, Shi et al.
2025), decided over sets rather than checked per value.

With ``implicit``, a sink call made under a test on a value from a source is refused too:
which branch runs carries that value's influence, the implicit flow CaMeL names.

With ``only``, the rule holds every origin but its sources: a value from a tool it does not
name, from a task input it does not name, or written into the program as a literal reaches the
sink only inside the endorsed set. That is integrity as provenance: an identifier must come
from the tool that retrieved it, on every path, which refutes the identifier a model makes up to
pass an authentication step, the integrity leak Sah et al. 2026 measure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from valgebra_planning._valgebra import Validator, compile_schema

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

    def holds(self, origin: str | None) -> bool:
        """Return whether a value from ``origin`` is held to the rule; None is a literal."""
        return (origin in self.sources) != self.only

    @property
    def sink(self) -> str:
        """Return the sink as a program writes it."""
        return f"{self.tool}.{self.argument}"


def read_flow(spec: Mapping[str, Any]) -> Flow:
    """Read a flow rule from its JSON form.

    It holds ``name``, ``sources``, ``sink`` as ``tool.argument``, and optionally the
    ``endorsed`` JSON Schema, ``implicit`` and ``only``. A source is a tool's name or a task
    input's.
    """
    tool, _, argument = spec["sink"].partition(".")
    endorsed = spec.get("endorsed")
    return Flow(
        name=spec["name"],
        sources=frozenset(spec["sources"]),
        tool=tool,
        argument=argument,
        endorsed=None if endorsed is None else compile_schema(endorsed),
        implicit=bool(spec.get("implicit", False)),
        only=bool(spec.get("only", False)),
    )
