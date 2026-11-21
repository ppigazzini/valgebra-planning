"""Every way a program holds a value, against every way it reads one.

A record ``R`` guarantees ``k`` and an array ``xs`` of tags, and may hold ``o``. Each way of
holding it, a result, a field, an item, a task input and their tested forms, meets each way of
reading it: each cell is proven and a run of it breaks no check, except a read of ``o`` with no
test showing it, which is refuted on a record lacking ``o`` that ``R`` admits, and whose run on
such a record ends naming the read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.flows import read_flow
from valgebra_planning.run import run
from valgebra_planning.verify import verify

TAG = {"enum": ["a", "b"]}
R = {
    "type": "object",
    "properties": {"k": TAG, "o": TAG, "xs": {"type": "array", "items": TAG}},
    "required": ["k", "xs"],
}
#: A member of `R` holding `o`, and one lacking it.
FULL = {"k": "a", "o": "b", "xs": ["a", "b"]}
BARE = {"k": "b", "xs": ["a"]}
USE = {
    "name": "use",
    "inputSchema": {"type": "object", "properties": {"tag": TAG}, "required": ["tag"]},
    "outputSchema": {"type": "object"},
}


@dataclass(frozen=True)
class Holder:
    """A way to hold `R`: the statements reaching it, the reference, and how it is given."""

    name: str
    #: The output schema of `get`, or None where the record is a task input `p`.
    output: dict[str, Any] | None
    #: The task input `p`'s schema, where the record is one.
    given: dict[str, Any] | None
    #: The statements before the read, each with its depth, and the depth the read sits at.
    lines: tuple[tuple[int, str], ...]
    depth: int
    ref: str
    #: Whether the read sits in a loop's body, where no path returns.
    looping: bool
    #: The value `get` returns, or `p` is given, around a member of `R`.
    wrap: Any
    #: What a path ends in after the read.
    end: str


def _object(name: str, schema: object, *, required: bool) -> dict[str, Any]:
    return {"type": "object", "properties": {name: schema}, "required": [name] if required else []}


def _items(schema: object) -> dict[str, Any]:
    return _object("rs", {"type": "array", "items": schema}, required=True)


HOLDERS = [
    Holder("a result", R, None, ((0, "r = get()"),), 0, "r", False, lambda m: m, "return r"),
    Holder(
        "a guaranteed field",
        _object("f", R, required=True),
        None,
        ((0, "r = get()"),),
        0,
        "r.f",
        False,
        lambda m: {"f": m},
        "return r",
    ),
    Holder(
        "a field under its key test",
        _object("f", R, required=False),
        None,
        ((0, "r = get()"), (0, 'if "f" in r:')),
        1,
        "r.f",
        False,
        lambda m: {"f": m},
        "return r",
    ),
    Holder(
        "a loop item",
        _items(R),
        None,
        ((0, "r = get()"), (0, "for x in r.rs:")),
        1,
        "x",
        True,
        lambda m: {"rs": [m]},
        "return r",
    ),
    Holder(
        "an item's field",
        _items(_object("f", R, required=True)),
        None,
        ((0, "r = get()"), (0, "for x in r.rs:")),
        1,
        "x.f",
        True,
        lambda m: {"rs": [{"f": m}]},
        "return r",
    ),
    Holder(
        "an item's field under its key test",
        _items(_object("f", R, required=False)),
        None,
        ((0, "r = get()"), (0, "for x in r.rs:"), (1, 'if "f" in x:')),
        2,
        "x.f",
        True,
        lambda m: {"rs": [{"f": m}]},
        "return r",
    ),
    Holder("a task input", None, R, (), 0, "p", False, lambda m: m, "return p"),
    Holder(
        "a task input's field",
        None,
        _object("f", R, required=True),
        (),
        0,
        "p.f",
        False,
        lambda m: {"f": m},
        "return p",
    ),
]

#: Each way to read the held record: its statements, given the reference, and whether the read
#: is proven. A path the read opens ends where it must outside a loop.
READERS: dict[str, tuple[tuple[tuple[int, str], ...], bool]] = {
    "a binding": (((0, "u = use(tag={ref}.k)"),), True),
    "a key test and the field under it": (
        ((0, 'if "o" in {ref}:'), (1, "u = use(tag={ref}.o)"), (1, "{end}")),
        True,
    ),
    "a loop over a field": (((0, "for y in {ref}.xs:"), (1, "u = use(tag=y)")), True),
    "a test against listed constants": (
        ((0, 'if {ref}.k in ("a", "b"):'), (1, "u = use(tag={ref}.k)"), (1, "{end}")),
        True,
    ),
    "a flow rule's sink": (((0, "u = use(tag={ref}.k)"),), True),
    "an untested optional field": (((0, "u = use(tag={ref}.o)"),), False),
}


def _source(holder: Holder, reading: tuple[tuple[int, str], ...]) -> str:
    lines = [(depth, text) for depth, text in holder.lines]
    for depth, text in reading:
        if text == "{end}" and holder.looping:
            continue
        lines.append((holder.depth + depth, text.format(ref=holder.ref, end=holder.end)))
    if not holder.looping and holder.depth:
        lines.append((holder.depth, holder.end))
    lines.append((0, holder.end))
    return "\n".join("    " * depth + text for depth, text in lines)


def _case(holder: Holder) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    """Return the contracts, the task and the inputs a run of ``holder`` starts with."""
    tools = [USE]
    if holder.output is not None:
        tools.append(
            {"name": "get", "inputSchema": {"type": "object"}, "outputSchema": holder.output}
        )
    contracts = {contract.name: contract for contract in map(read_contract, tools)}
    inputs = {} if holder.given is None else {"p": holder.given}
    return contracts, read_task(holder.name, inputs, True), inputs


@pytest.mark.parametrize("reader", list(READERS))
@pytest.mark.parametrize("holder", HOLDERS, ids=[holder.name for holder in HOLDERS])
def test_every_held_value_is_read_as_a_result_is(holder: Holder, reader: str) -> None:
    reading, proven = READERS[reader]
    source = _source(holder, reading)
    contracts, task, given = _case(holder)
    source_name = "get" if holder.output is not None else "p"
    flows = []
    if reader == "a flow rule's sink":
        flows = [
            read_flow(
                {"name": "tags", "sources": [source_name], "sink": "use.tag", "endorsed": TAG}
            )
        ]
    checked = verify(read_program(source), contracts, task, flows=flows)
    member = FULL if proven else BARE
    tools = {"get": lambda **_: holder.wrap(member), "use": lambda **_: {}}
    inputs = {"p": holder.wrap(member)} if given else {}
    ran = run(read_program(source), contracts, task, inputs, tools)
    if proven:
        assert checked.proven, [link for link in checked.links if link.verdict != "proven"]
        assert ran.broken == ()
        return
    [refuted] = [link for link in checked.links if link.verdict != "proven"]
    assert (refuted.claim, refuted.verdict) == (
        f"{holder.ref}.o is a value the program holds",
        "refuted",
    )
    assert ran.why is not None
    assert ran.why.check == "read"
