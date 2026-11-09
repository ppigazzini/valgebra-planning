"""A program of tool calls and type tests, and its text.

A program is a block: calls, each binding arguments to references and naming its result,
then one ending. A return hands a reference back, a stop marks a value the program does not
cover, and a test branches on whether a reference lies in a tool's argument, each branch a
block of its own. A reference is a variable and a path of fields below it, ``c1.order.status``.

The text reads as Python, so a program written elsewhere in that form is read back by
`valgebra_planning.critic`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Ref:
    """A variable, and the fields below it: ``c1.order.status`` is ``c1`` at ``order.status``."""

    variable: str
    path: tuple[str, ...] = ()

    def __str__(self) -> str:
        return ".".join((self.variable, *self.path))


@dataclass(frozen=True, slots=True)
class Call:
    """Call ``tool`` with each argument bound to a reference, the result named ``result``."""

    tool: str
    bindings: tuple[tuple[str, Ref], ...]
    result: str


@dataclass(frozen=True, slots=True)
class Return:
    """Hand back the value of a reference: the task's goal must hold it."""

    ref: Ref


@dataclass(frozen=True, slots=True)
class Stop:
    """End a path the program does not cover; ``ref`` is the value left uncovered there."""

    ref: Ref


@dataclass(frozen=True, slots=True)
class Branch:
    """Branch on whether ``ref`` lies in a set: a tool's argument, or JSON values listed.

    With ``tool`` empty the set is ``values``, read as a JSON Schema ``enum``: ``1`` and
    ``1.0`` are one value there. Otherwise it is the argument ``argument`` of ``tool``.
    """

    ref: Ref
    tool: str
    argument: str
    then: Block
    otherwise: Block
    values: tuple[Any, ...] = ()

    @property
    def against(self) -> str:
        """Return the set as a program writes it."""
        if self.tool:
            return f"{self.tool}.{self.argument}"
        return "(" + "".join(f"{json.dumps(v)}, " for v in self.values).rstrip(" ") + ")"


@dataclass(frozen=True, slots=True)
class Block:
    """Calls in order, then one ending."""

    calls: tuple[Call, ...]
    end: Return | Stop | Branch


def render(block: Block, indent: str = "") -> str:
    """Return the program as Python-like text, one step a line."""
    lines = [
        f"{indent}{call.result} = {call.tool}("
        + ", ".join(f"{name}={ref}" for name, ref in call.bindings)
        + ")"
        for call in block.calls
    ]
    end = block.end
    if isinstance(end, Return):
        lines.append(f"{indent}return {end.ref}")
    elif isinstance(end, Stop):
        lines.append(f"{indent}stop({end.ref})")
    else:
        lines.append(f"{indent}if {end.ref} in {end.against}:")
        lines.append(render(end.then, indent + "    "))
        lines.append(f"{indent}else:")
        lines.append(render(end.otherwise, indent + "    "))
    return "\n".join(lines)


def steps(block: Block) -> int:
    """Return the calls and tests on the longest path of a program."""
    end = block.end
    below = 1 + max(steps(end.then), steps(end.otherwise)) if isinstance(end, Branch) else 0
    return len(block.calls) + below


def renumber(block: Block, first: int = 1) -> Block:
    """Return the program with its results named ``c1``, ``c2`` ... in order along each path.

    Two branches that differ only in the names of their results read the same once both are
    numbered from where they start.
    """
    return _renumber(block, {}, first)


def _renamed(ref: Ref, names: dict[str, str]) -> Ref:
    return Ref(names.get(ref.variable, ref.variable), ref.path)


def _renumber(block: Block, names: dict[str, str], first: int) -> Block:
    names = dict(names)
    calls = []
    for at, call in enumerate(block.calls, start=first):
        bindings = tuple((name, _renamed(ref, names)) for name, ref in call.bindings)
        names[call.result] = f"c{at}"
        calls.append(Call(call.tool, bindings, names[call.result]))
    after = first + len(calls)
    end = block.end
    if isinstance(end, Return):
        ending: Return | Stop | Branch = Return(_renamed(end.ref, names))
    elif isinstance(end, Stop):
        ending = end if end.ref.variable not in names else Stop(_renamed(end.ref, names))
    else:
        ending = Branch(
            _renamed(end.ref, names),
            end.tool,
            end.argument,
            _renumber(end.then, names, after),
            _renumber(end.otherwise, names, after),
            end.values,
        )
    return Block(tuple(calls), ending)
