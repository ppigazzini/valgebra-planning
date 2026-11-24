"""Typed tool stubs for a model to write a program against, and the loop that repairs it.

In code mode a model writes a program against typed tool APIs rather than calling tools one by
one. `stubs` gives those APIs as Python: one function a tool, every argument by name, its type
hint the kind its schema names or the `Literal` of the values it lists, and its docstring the
tool's own input and output JSON Schemas, since a pattern or a bound has no Python type and the
critic holds the program to the schemas.
`repair` is the loop of a model that proposes and a sound critic that checks, the critique
handed back as the next prompt (Kambhampati et al. 2024, §3.2): a program is critiqued, and its
critique handed back for the next proposal, until it is accepted or the rounds run out. The
model is any callable from the last critique to a program, so no model is part of this package.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from valgebra_planning._valgebra import finite
from valgebra_planning.contracts import Contract, Slot, Task
from valgebra_planning.critic import Critique, ProgramError, critique
from valgebra_planning.program import alias

__all__ = ["Repair", "feedback", "repair", "stub", "stubs"]

#: The Python a JSON Schema type is written as in a stub.
_HINTS = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list[Any]",
    "object": "dict[str, Any]",
    "null": "None",
}
#: What a stub's reader is told before the tools: the language the critic reads.
PREAMBLE = '''"""Tools to write a program against.

A program binds each call's result to a name and passes every argument by name: a value it
holds, a field of one, or a JSON constant. It tests a value with `if value in tool.argument:`,
`==`, `in (...)` or `"key" in value`, and loops with `for item in value:`. It wraps a call to a
read-only or idempotent tool in `retry(...)`, any call in `try:`/`except ToolError:`, and ends
each path in `return value` or `stop(value)`. A program for another task reads:

    c1 = find_order(order_id=order_id)
    if c1.status in send_notice.status:
        c2 = send_notice(order_id=c1.order_id, status=c1.status)
        return c2.notice_id
    stop(c1.status)
"""

from typing import Any, Literal


class ToolError(Exception): ...


def retry(result: Any) -> Any: ...


def stop(value: Any) -> None: ...
'''


def _hint(schema: object) -> str:
    kind = schema.get("type") if isinstance(schema, dict) else None
    return _HINTS.get(kind, "Any") if isinstance(kind, str) else "Any"


def _literal(slot: Slot) -> str | None:
    """Return the `Literal` of the values an argument's schema lists, or None for no such list.

    `finite` lists the JSON values a schema with `enum` or `const` admits, the rest of the
    schema applied (JSON Schema 2020-12 Validation, §6.1.2, §6.1.3), an integral number in both
    of its spellings. A `Literal` holds a `str`, an `int`, a `bool` or `None` (PEP 586), so the
    float spelling of a listed integer is left out and a list holding a float that is no integer
    has none. `True` is equal to `1` in Python and not in JSON, so a value is kept by its type as
    well. A list naming both booleans, or `null` alone, is the set a kind's hint names, `bool`
    or `None`, and is written so.
    """
    listed = finite(slot.schema, slot.root)
    if not listed:
        return None
    kept: list[object] = []
    for value in listed:
        if isinstance(value, float):
            if value.is_integer():
                continue
            return None
        if not any(type(value) is type(held) and value == held for held in kept):
            kept.append(value)
    named = {(type(value), value) for value in kept}
    if named == {(bool, True), (bool, False)}:
        return "bool"
    if named == {(type(None), None)}:
        return "None"
    return f"Literal[{', '.join(map(repr, kept))}]"


def stub(contract: Contract) -> str:
    """Return a tool's stub: its signature, and its input and output schemas as its docstring."""
    arguments = ", ".join(
        f"{slot.path[0]}: {_literal(slot) or _hint(slot.schema)}"
        + ("" if slot.required else " = ...")
        for slot in sorted(contract.arguments, key=lambda slot: not slot.required)
    )
    inputs = contract.arguments[0].root if contract.arguments else {"type": "object"}
    returns = contract.result().schema if contract.results else None
    doc = [f"Arguments, as JSON Schema: {json.dumps(inputs, sort_keys=True)}"]
    doc.append(
        "Returns no value a program can read."
        if returns is None
        else f"Returns, as JSON Schema: {json.dumps(returns, sort_keys=True)}"
    )
    called = alias(contract.name)
    if called != contract.name:
        doc.insert(0, f"The tool {contract.name!r}, called by this name.")
    body = "\n\n".join(f"    {line}" for line in doc)
    signature = f"def {called}(*, {arguments})" if arguments else f"def {called}()"
    return f'{signature} -> {_hint(returns)}:\n    """{body.strip()}\n    """\n    ...\n'


def stubs(contracts: Mapping[str, Contract]) -> str:
    """Return the stubs of every tool, after the preamble stating the language a program uses."""
    return "\n\n".join([PREAMBLE, *(stub(contracts[name]) for name in sorted(contracts))])


@dataclass(frozen=True, slots=True)
class Repair:
    """The rounds a program took: each source proposed and its critique, or why it was not read.

    ``accept`` is the mode the rounds ran in, `repair`'s.
    """

    rounds: tuple[tuple[str, Critique | str], ...]
    accept: Literal["strong", "weak"] = "strong"

    @property
    def accepted(self) -> bool:
        """Return whether the last program proposed was accepted: proven, stopping nowhere."""
        last = self.rounds[-1][1] if self.rounds else None
        return isinstance(last, Critique) and last.accepted

    @property
    def met(self) -> bool:
        """Return whether the last program proposed met the mode the rounds ran in.

        Under ``accept="weak"`` a program weak under its task's stops meets it, which ends the
        rounds though `accepted` reads false.
        """
        last = self.rounds[-1][1] if self.rounds else None
        if not isinstance(last, Critique):
            return False
        return last.accepted or (self.accept == "weak" and last.weak)


def feedback(checked: Critique | str) -> str:
    """Return a critique as the text handed back to the model: each note, a line."""
    if isinstance(checked, str):
        return f"the program is not read: {checked}"
    return "\n".join(checked.notes) if checked.notes else "accepted"


def repair(
    propose: Callable[[str | None], str],
    contracts: Mapping[str, Contract],
    task: Task,
    *,
    rounds: int = 3,
    accept: Literal["strong", "weak"] = "strong",
    **rules: Any,
) -> Repair:
    """Propose, critique and hand the critique back, until a program is accepted or ``rounds`` end.

    ``propose`` takes the last critique as text, None the first time, and returns a program's
    source. The ``rules`` are those `valgebra_planning.critic.critique` holds a program to.
    With ``accept="weak"``, a program some path of which returns and whose every stop the
    task tolerates ends the rounds too: a task no program covers on every path, as an object
    a detector may not find, is met by one stopping only where the task says it may.
    """
    done: list[tuple[str, Critique | str]] = []
    told: str | None = None
    for _ in range(rounds):
        source = propose(told)
        try:
            checked: Critique | str = critique(source, contracts, task, **rules)
        except ProgramError as error:
            checked = str(error)
        done.append((source, checked))
        if isinstance(checked, Critique) and (
            checked.accepted or (accept == "weak" and checked.weak)
        ):
            break
        told = feedback(checked)
    return Repair(tuple(done), accept)
