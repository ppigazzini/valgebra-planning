"""A program written elsewhere, read from Python, verified, and answered with a critique.

A language model proposes, a sound critic checks (Kambhampati et al. 2024): the program's
every link is asked of valgebra by `valgebra_planning.verify.verify`, and the answer names
each link that is not proven, the value that refutes it where one is found, and, for an
argument a value only overlaps, the test that makes the call safe.

The reader takes the Python an agent writes against typed tool APIs: a function or a bare
block of statements; ``c1 = find_parcel(tracking=tracking)`` or ``tools.find_parcel(...)``,
awaited or not; fields as attributes or as string subscripts; ``return``; ``stop(ref)``; and
``if``/``elif``/``else`` testing a value against a tool's argument (``ref in tool.argument``)
or against listed constants (``==``, ``!=``, ``in``, ``not in``); ``retry(tool(...))``; and
``try:`` around one call with ``except ToolError:``. Anything else is refused by line, never
skipped.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from valgebra_planning.contracts import Contract, Task
from valgebra_planning.program import Block, Branch, Call, Ref, Return, Stop
from valgebra_planning.verify import Verification, verify


class ProgramError(ValueError):
    """A program the reader does not read, with the line that stops it."""


def _fail(node: ast.AST, what: str) -> ProgramError:
    return ProgramError(f"line {getattr(node, 'lineno', '?')}: {what}")


def _ref(node: ast.expr) -> Ref:
    path: list[str] = []
    while True:
        if isinstance(node, ast.Attribute):
            path.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if not isinstance(node.slice.value, str):
                raise _fail(node, "a field is read by its name")
            path.append(node.slice.value)
            node = node.value
        elif isinstance(node, ast.Name):
            return Ref(node.id, tuple(reversed(path)))
        else:
            raise _fail(node, "a value is a variable and the fields below it")


def _tool(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    raise _fail(node, "a tool is called by its name")


def _named(node: ast.expr) -> str | None:
    return node.id if isinstance(node, ast.Name) else None


def _guarded_call(statement: ast.Try) -> Call:
    """Read ``try: x = tool(...) except ToolError: <block>`` as a call with an error block."""
    if (
        len(statement.body) != 1
        or not isinstance(statement.body[0], ast.Assign)
        or len(statement.handlers) != 1
        or statement.orelse
        or statement.finalbody
    ):
        raise _fail(statement, "a try holds one call and one `except ToolError:`")
    handler = statement.handlers[0]
    if handler.type is None or _tool(handler.type) != "ToolError":
        raise _fail(statement, "a try catches `ToolError` alone")
    call = _call(statement.body[0])
    return Call(call.tool, call.bindings, call.result, call.retry, _block(handler.body, handler))


def _call(statement: ast.Assign) -> Call:
    if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
        raise _fail(statement, "a call's result is bound to one name")
    value = statement.value
    if isinstance(value, ast.Await):
        value = value.value
    retry = (
        isinstance(value, ast.Call)
        and _named(value.func) == "retry"
        and len(value.args) == 1
        and not value.keywords
    )
    if retry and isinstance(value, ast.Call):
        value = value.args[0]
        if isinstance(value, ast.Await):
            value = value.value
    if not isinstance(value, ast.Call) or value.args:
        raise _fail(statement, "a call passes every argument by name")
    bindings = []
    for keyword in value.keywords:
        if keyword.arg is None:
            raise _fail(statement, "a call passes every argument by name")
        bindings.append((keyword.arg, _ref(keyword.value)))
    return Call(_tool(value.func), tuple(bindings), statement.targets[0].id, retry=retry)


def _constants(node: ast.expr) -> tuple[Any, ...]:
    if isinstance(node, ast.Constant):
        return (node.value,)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and all(
        isinstance(item, ast.Constant) for item in node.elts
    ):
        return tuple(item.value for item in node.elts if isinstance(item, ast.Constant))
    raise _fail(node, "a value is tested against a tool's argument or listed constants")


def _branch(statement: ast.If) -> Branch:
    test = statement.test
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        raise _fail(statement, "a test compares one value once")
    op, right = test.ops[0], test.comparators[0]
    ref = _ref(test.left)
    then, otherwise = _block(statement.body), _block(statement.orelse, statement)
    if isinstance(op, (ast.NotIn, ast.NotEq)):
        then, otherwise = otherwise, then
    if isinstance(op, (ast.In, ast.NotIn)) and isinstance(right, ast.Attribute):
        tool, argument = _tool(right.value), right.attr
        return Branch(ref, tool, argument, then, otherwise)
    if isinstance(op, (ast.Eq, ast.NotEq)) and not isinstance(right, ast.Constant):
        raise _fail(statement, "a value is compared for equality with a constant")
    if not isinstance(op, (ast.In, ast.NotIn, ast.Eq, ast.NotEq)):
        raise _fail(statement, "a test is a membership or an equality")
    return Branch(ref, "", "", then, otherwise, _constants(right))


def _with_else(statement: ast.If, rest: Sequence[ast.stmt]) -> ast.If:
    return ast.If(
        test=statement.test, body=statement.body, orelse=list(rest), lineno=statement.lineno
    )


def _block(statements: Sequence[ast.stmt], parent: ast.AST | None = None) -> Block:
    if not statements:
        raise _fail(parent or ast.Pass(), "a branch ends in a return, a stop or a test")
    calls = []
    for at, statement in enumerate(statements[:-1]):
        if isinstance(statement, ast.If) and not statement.orelse:
            # An `if` with no `else` before more statements: what follows is its other branch.
            branch = _branch(_with_else(statement, statements[at + 1 :]))
            return Block(tuple(calls), branch)
        if isinstance(statement, ast.Try):
            calls.append(_guarded_call(statement))
            continue
        if not isinstance(statement, ast.Assign):
            raise _fail(statement, "only calls come before a block's last statement")
        calls.append(_call(statement))
    last = statements[-1]
    if isinstance(last, ast.Return) and last.value is not None:
        return Block(tuple(calls), Return(_ref(last.value)))
    if isinstance(last, ast.If):
        return Block(tuple(calls), _branch(last))
    if (
        isinstance(last, ast.Expr)
        and isinstance(last.value, ast.Call)
        and _tool(last.value.func) == "stop"
        and len(last.value.args) == 1
    ):
        return Block(tuple(calls), Stop(_ref(last.value.args[0])))
    raise _fail(last, "a block ends in a return, a stop or a test")


def read_program(source: str) -> Block:
    """Read a program from Python source: one function, or a block of statements.

    Raises `ProgramError` naming the line of a construct it does not read.
    """
    try:
        module = ast.parse(source)
    except SyntaxError:
        # A bare block may `return` outside a function, which Python refuses to parse.
        module = ast.parse(
            "def plan():\n" + "\n".join("    " + line for line in source.splitlines())
        )
    body = module.body
    if len(body) == 1 and isinstance(body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        body = body[0].body
    body = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    return _block(body)


@dataclass(frozen=True, slots=True)
class Critique:
    """A proposed program, its verification, and the critique to hand back to its author."""

    program: Block
    verification: Verification
    notes: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        """Return whether every link is proven and no path stops."""
        return self.verification.proven


def _guard(claim: str) -> str | None:
    """Return the test that narrows a binding's value into its argument, if the claim is one."""
    subject, _, slot = claim.partition(" <= ")
    if not slot or subject.startswith("return ") or "." not in slot:
        return None
    return f"test `if {subject} in {slot}:` and make the call in that branch"


def critique(
    source: str,
    contracts: Mapping[str, Contract],
    task: Task,
    *,
    examples: Mapping[str, Sequence[Any]] | None = None,
) -> Critique:
    """Read, verify and critique a program proposed for ``task``.

    Raises `ProgramError` for a program the reader does not read.
    """
    program = read_program(source)
    checked = verify(program, contracts, task, examples=examples)
    notes = []
    for link in checked.links:
        if link.verdict == "proven":
            continue
        where = f" (at {link.where})" if link.where else ""
        note = f"{link.verdict}: {link.claim}{where}"
        if link.counterexample:
            note += f"; {link.counterexample[0]!r} lies outside"
        guard = _guard(link.claim) if link.verdict != "proven" else None
        if guard is not None:
            note += f"; {guard}"
        notes.append(note)
    notes += [f"uncovered: {gap.ref} (at {gap.where or 'top'})" for gap in checked.gaps]
    return Critique(program, checked, tuple(notes))
