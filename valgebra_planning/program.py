"""A program of tool calls and type tests, and its text.

A program is a block: calls, each binding arguments to references or to constants and naming
its result, then one ending. A return hands a reference back, a stop marks a value the program
does not cover, and a test branches on whether a reference lies in a tool's argument, each
branch a block of its own. A reference is a variable and a path of fields below it,
``c1.order.status``; a constant is a JSON value, ``"delivered"``.

The text reads as Python, so a program written elsewhere in that form is read back by
`valgebra_planning.critic`.
"""

from __future__ import annotations

import json
import keyword
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

__all__ = [
    "Block",
    "Branch",
    "Call",
    "Const",
    "For",
    "Next",
    "Ref",
    "Return",
    "Stop",
    "alias",
    "render",
    "steps",
]


#: How many names keep how a program writes them: a program writes a few names many times.
_NAMES = 1024


@lru_cache(maxsize=_NAMES)
def spelled(name: str) -> bool:
    """Return whether Python spells ``name``: an identifier, no keyword, that it reads unchanged.

    Python reads an identifier in its NFKC form, so a name in another form, as one holding the
    ligature U+FB01, would be read as another name. An ASCII name is in that form.
    """
    return (
        name.isidentifier()
        and not keyword.iskeyword(name)
        and (name.isascii() or unicodedata.normalize("NFKC", name) == name)
    )


#: The first code point past the Basic Multilingual Plane.
_ASTRAL = 0x10000


def quoted(name: str) -> str:
    """Return ``name`` as the Python string literal a program writes it as.

    JSON escapes a character past the Basic Multilingual Plane as two surrogates, which a Python
    literal reads as two characters and not as the one: such a name is written by `repr`.
    """
    return json.dumps(name) if all(ord(char) < _ASTRAL for char in name) else repr(name)


@lru_cache(maxsize=_NAMES)
def _member(name: str) -> str:
    """Return how a program writes the field or the argument ``name`` after what holds it."""
    return f".{name}" if spelled(name) else f"[{quoted(name)}]"


@dataclass(frozen=True, slots=True)
class Ref:
    """A variable, and the fields below it: ``c1.order.status`` is ``c1`` at ``order.status``."""

    variable: str
    path: tuple[str, ...] = ()

    def __str__(self) -> str:
        """Return the reference as a program writes it, a key no attribute names subscripted.

        A key that is no Python identifier, or is a keyword, as `a-b` or `class`, reads back only
        as a subscript, `x["a-b"]`, the form the critic reads it in.
        """
        return "".join((self.variable, *map(_member, self.path))) if self.path else self.variable


@dataclass(frozen=True, slots=True)
class Const:
    """A JSON value written into a call, held as its JSON text so a program stays hashable."""

    text: str

    @staticmethod
    def of(value: Any) -> Const:
        """Return the constant ``value``, a JSON value."""
        return Const(json.dumps(value, sort_keys=True))

    @property
    def value(self) -> Any:
        """Return the value the constant holds."""
        return json.loads(self.text)

    def __str__(self) -> str:
        return repr(self.value)


@dataclass(frozen=True, slots=True)
class Call:
    """Call ``tool``, each argument bound to a reference or a constant, the result ``result``.

    A tool may answer with an error. ``retry`` makes the call again until it succeeds, which
    only a tool that is read-only or idempotent allows; ``on_error`` is the block run instead
    of the rest, where the result is never bound.
    """

    tool: str
    bindings: tuple[tuple[str, Ref | Const], ...]
    result: str
    retry: bool = False
    on_error: Block | None = None


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
    """Branch on whether ``ref`` lies in a set: a tool's argument, JSON values listed, or a key.

    With ``key`` set the set is every object holding that key, and the branch where it holds
    reads the fields the key opens. With ``tool`` empty the set is ``values``, read as a JSON
    Schema ``enum``: ``1`` and ``1.0`` are one value there. Otherwise it is the argument
    ``argument`` of ``tool``.
    """

    ref: Ref
    tool: str
    argument: str
    then: Block
    otherwise: Block
    values: tuple[Any, ...] = ()
    key: str = ""

    @property
    def against(self) -> str:
        """Return the set as a program writes it."""
        if self.tool:
            return slot(self.tool, self.argument)
        # Each value as a Python literal, as a call writes a constant, so the test reads back.
        return "(" + "".join(f"{v!r}, " for v in self.values).rstrip(" ") + ")"


@dataclass(frozen=True, slots=True)
class Next:
    """End one iteration of a loop's body."""


@dataclass(frozen=True, slots=True)
class For:
    """Run ``body`` once for each item of the array ``ref`` holds, named ``item``; then ``after``.

    What the body calls is scoped to its iteration: ``after`` reads nothing of it.
    """

    ref: Ref
    item: str
    body: Block
    after: Block


@dataclass(frozen=True, slots=True)
class Block:
    """Calls in order, then one ending; a loop's body ends in `Next`."""

    calls: tuple[Call, ...]
    end: Return | Stop | Branch | For | Next


def slot(tool: str, argument: str) -> str:
    """Return how a program's test names the argument ``argument`` of ``tool``.

    The tool is written by its alias, and an argument Python does not spell subscripted, as a
    field is: ``send["from"]``.
    """
    return alias(tool) + _member(argument)


@lru_cache(maxsize=_NAMES)
def alias(name: str) -> str:
    """Return the identifier a program calls a tool by: its name, where Python spells one.

    MCP allows a tool's name dots and hyphens, and a proxy may prefix one with its server's
    (MCP 2026-07-28, server/tools), so ``g1.grasp`` and ``get-run`` name tools no Python call
    spells. Such a name is called by an alias: each character outside ``[A-Za-z0-9_]`` written
    ``_``, a leading digit taking a leading ``_`` and a keyword a trailing ``_``, as
    ``g1_grasp``, ``get_run`` and ``class_``.
    """
    if spelled(name):
        return name
    written = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not written or written[0].isdigit():
        written = "_" + written
    return written + "_" if keyword.iskeyword(written) else written


def aliases(names: Iterable[str]) -> dict[str, str]:
    """Return each tool's name by the alias a program calls it by; collisions are a `ValueError`.

    Two names one alias spells, or an alias spelling another tool's own name, would make one
    call read as two tools, so the library is refused naming both.
    """
    named: dict[str, str] = {}
    listed = set(names)
    for name in sorted(listed):
        written = alias(name)
        other = named.get(written) or (written if written != name and written in listed else None)
        if other is not None:
            message = f"tools {other!r} and {name!r} are both called {written!r} in a program"
            raise ValueError(message)
        named[written] = name
    return named


def named(block: Block, names: Mapping[str, str]) -> Block:
    """Return ``block`` with each tool an alias calls written by its name (`aliases`)."""
    moved = {written: name for written, name in names.items() if written != name}
    return _named(block, moved) if moved else block


def _named(block: Block, names: Mapping[str, str]) -> Block:
    calls = tuple(
        replace(
            call,
            tool=names.get(call.tool, call.tool),
            on_error=None if call.on_error is None else _named(call.on_error, names),
        )
        for call in block.calls
    )
    end = block.end
    if isinstance(end, Branch):
        end = replace(
            end,
            tool=names.get(end.tool, end.tool),
            then=_named(end.then, names),
            otherwise=_named(end.otherwise, names),
        )
    elif isinstance(end, For):
        end = replace(end, body=_named(end.body, names), after=_named(end.after, names))
    return Block(calls, end)


def _passed(bindings: Iterable[tuple[str, Ref | Const]]) -> str:
    """Return a call's arguments as a program writes them.

    An argument Python does not spell, as ``from`` or ``reply-to``, is no keyword of a call, and
    is passed by its name in a dictionary unpacked into it: ``send(to=to, **{"from": sender})``.
    """
    passed: list[str] = []
    run: list[str] = []
    for name, ref in bindings:
        if not spelled(name):
            run.append(f"{quoted(name)}: {ref}")
            continue
        if run:
            passed.append("**{" + ", ".join(run) + "}")
            run = []
        passed.append(f"{name}={ref}")
    if run:
        passed.append("**{" + ", ".join(run) + "}")
    return ", ".join(passed)


def render(block: Block, indent: str = "") -> str:
    """Return the program as Python-like text, one step a line.

    Raises `TypeError` for anything but a `Block`: a plan with no program holds None.
    """
    if not isinstance(block, Block):
        message = f"render takes a program, a Block, not {type(block).__name__}"
        raise TypeError(message)
    lines = []
    for call in block.calls:
        made = f"{alias(call.tool)}({_passed(call.bindings)})"
        line = f"{call.result} = " + (f"retry({made})" if call.retry else made)
        if call.on_error is None:
            lines.append(f"{indent}{line}")
        else:
            lines += [f"{indent}try:", f"{indent}    {line}", f"{indent}except ToolError:"]
            lines.append(render(call.on_error, indent + "    "))
    end = block.end
    if isinstance(end, Return):
        lines.append(f"{indent}return {end.ref}")
    elif isinstance(end, Stop):
        lines.append(f"{indent}stop({end.ref})")
    elif isinstance(end, Next):
        if not lines:
            lines.append(f"{indent}pass")
    elif isinstance(end, For):
        lines += [f"{indent}for {end.item} in {end.ref}:", render(end.body, indent + "    ")]
        lines.append(render(end.after, indent))
    else:
        tested = f"{quoted(end.key)} in {end.ref}" if end.key else f"{end.ref} in {end.against}"
        lines.append(f"{indent}if {tested}:")
        lines.append(render(end.then, indent + "    "))
        lines.append(f"{indent}else:")
        lines.append(render(end.otherwise, indent + "    "))
    return "\n".join(lines)


def steps(block: Block) -> int:
    """Return the calls and tests on the longest path of a program, error paths included."""
    end = block.end
    below = 1 + max(steps(end.then), steps(end.otherwise)) if isinstance(end, Branch) else 0
    if isinstance(end, For):
        below = 1 + steps(end.body) + steps(end.after)
    longest = len(block.calls) + below
    for at, call in enumerate(block.calls, start=1):
        if call.on_error is not None:
            longest = max(longest, at + steps(call.on_error))
    return longest


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
        bindings = tuple(
            (name, ref if isinstance(ref, Const) else _renamed(ref, names))
            for name, ref in call.bindings
        )
        on_error = None if call.on_error is None else _renumber(call.on_error, names, at)
        names[call.result] = f"c{at}"
        calls.append(Call(call.tool, bindings, names[call.result], call.retry, on_error))
    after = first + len(calls)
    end = block.end
    if isinstance(end, Return):
        ending: Return | Stop | Branch | For | Next = Return(_renamed(end.ref, names))
    elif isinstance(end, Stop):
        ending = end if end.ref.variable not in names else Stop(_renamed(end.ref, names))
    elif isinstance(end, Next):
        ending = end
    elif isinstance(end, For):
        body, rest = _renumber(end.body, names, after), _renumber(end.after, names, after)
        ending = For(_renamed(end.ref, names), end.item, body, rest)
    else:
        ending = Branch(
            _renamed(end.ref, names),
            end.tool,
            end.argument,
            _renumber(end.then, names, after),
            _renumber(end.otherwise, names, after),
            end.values,
            end.key,
        )
    return Block(tuple(calls), ending)
