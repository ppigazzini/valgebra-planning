"""Typed tool stubs for a model to write a program against, and the loop that repairs it.

In code mode a model writes a program against typed tool APIs rather than calling tools one by
one. `stubs` gives those APIs as Python: one function a tool, every argument by name, its type
hint the kind its schema names, and its docstring the tool's own input and output JSON Schemas,
since a pattern or a bound has no Python type and the critic holds the program to the schemas.
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

from valgebra_planning.contracts import Contract, Task
from valgebra_planning.critic import Critique, ProgramError, critique

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

from typing import Any


class ToolError(Exception): ...


def retry(result: Any) -> Any: ...


def stop(value: Any) -> None: ...
'''


def _hint(schema: object) -> str:
    kind = schema.get("type") if isinstance(schema, dict) else None
    return _HINTS.get(kind, "Any") if isinstance(kind, str) else "Any"


def stub(contract: Contract) -> str:
    """Return a tool's stub: its signature, and its input and output schemas as its docstring."""
    arguments = ", ".join(
        f"{slot.path[0]}: {_hint(slot.schema)}" + ("" if slot.required else " = ...")
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
    body = "\n\n".join(f"    {line}" for line in doc)
    signature = f"def {contract.name}(*, {arguments})" if arguments else f"def {contract.name}()"
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
