"""A program written elsewhere, read from Python, verified, and answered with a critique.

A language model proposes, a sound critic checks (Kambhampati et al. 2024, §3.1): the program's
every link is asked of valgebra by `valgebra_planning.verify.verify`, under the rules the task
is held to, and the answer names each link that is not proven, the value that refutes it where
one is found, and what the author can change: for an argument a value only overlaps, the test
that makes the call safe; for a rule, the order, the source or the error block it needs.

The reader takes the Python an agent writes against typed tool APIs: a function or a bare
block of statements; ``c1 = find_parcel(tracking=tracking)`` or ``tools.find_parcel(...)``,
awaited or not; fields as attributes or as string subscripts; ``return``; ``stop(ref)``; and
an argument as a value the program holds or a JSON constant (``state="delivered"``);
``if``/``elif``/``else`` testing a value against a tool's argument (``ref in tool.argument``)
or against listed constants (``==``, ``!=``, ``in``, ``not in``), or a key of a record
(``"field" in ref``); ``retry(tool(...))``; and
``try:`` around one call with ``except ToolError:``; ``for item in ref:`` over an array, whose
body ends each iteration where its statements run out and never returns; and ``pass``. Anything
else is refused by line, never skipped.
"""

from __future__ import annotations

import ast
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from valgebra_planning.contracts import Contract, Task
from valgebra_planning.flows import Flow
from valgebra_planning.mining import Types
from valgebra_planning.policy import Policy
from valgebra_planning.program import Block, Branch, Call, Const, For, Next, Ref, Return, Stop
from valgebra_planning.verify import Link, Verification, verify

__all__ = ["Critique", "ProgramError", "critique", "read_program"]


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


def _argument(node: ast.expr) -> Ref | Const:
    """Read an argument: a reference, or a JSON constant written in Python."""
    if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
        return _ref(node)
    try:
        value = ast.literal_eval(node)
    except ValueError:
        raise _fail(node, "an argument is a value the program holds or a JSON constant") from None
    if not _json(value):
        raise _fail(node, "a constant is a JSON value: no tuple, set, bytes or complex number")
    return Const.of(value)


def _json(value: Any) -> bool:
    """Return whether a Python value is a JSON value, in the types `json.loads` returns."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return not isinstance(value, float) or math.isfinite(value)
    if isinstance(value, list):
        return all(map(_json, value))
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json(item) for key, item in value.items())
    return False


def _tool(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    raise _fail(node, "a tool is called by its name")


def _named(node: ast.expr) -> str | None:
    return node.id if isinstance(node, ast.Name) else None


def _guarded_call(statement: ast.Try, *, looping: bool = False) -> Call:
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
    handled = _block(handler.body, handler, looping=looping)
    return Call(call.tool, call.bindings, call.result, call.retry, handled)


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
        bindings.append((keyword.arg, _argument(keyword.value)))
    return Call(_tool(value.func), tuple(bindings), statement.targets[0].id, retry=retry)


def _constants(node: ast.expr) -> tuple[Any, ...]:
    if isinstance(node, ast.Constant):
        return (node.value,)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and all(
        isinstance(item, ast.Constant) for item in node.elts
    ):
        return tuple(item.value for item in node.elts if isinstance(item, ast.Constant))
    raise _fail(node, "a value is tested against a tool's argument or listed constants")


def _branch(statement: ast.If, *, looping: bool = False) -> Branch:
    test = statement.test
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        raise _fail(statement, "a test compares one value once")
    op, right = test.ops[0], test.comparators[0]
    then = _block(statement.body, looping=looping)
    otherwise = _block(statement.orelse, statement, looping=looping)
    if isinstance(op, (ast.NotIn, ast.NotEq)):
        then, otherwise = otherwise, then
    left = test.left
    if isinstance(left, ast.Constant) and isinstance(left.value, str):
        if not isinstance(op, (ast.In, ast.NotIn)):
            raise _fail(statement, "a key is tested with `in` or `not in`")
        return Branch(_ref(right), "", "", then, otherwise, key=left.value)
    ref = _ref(left)
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


def _loop(statement: ast.For, rest: Block) -> For:
    """Read ``for item in ref:`` as a loop whose body ends each iteration, then ``rest``."""
    if not isinstance(statement.target, ast.Name) or statement.orelse:
        raise _fail(statement, "a loop names one item and has no `else`")
    body = _block(statement.body, statement, looping=True)
    return For(_ref(statement.iter), statement.target.id, body, rest)


def _ending(last: ast.stmt, *, looping: bool) -> Return | Stop | Branch:
    if isinstance(last, ast.Return) and last.value is not None:
        if looping:
            raise _fail(last, "a loop's body does not return")
        return Return(_ref(last.value))
    if isinstance(last, ast.If):
        return _branch(last, looping=looping)
    if (
        isinstance(last, ast.Expr)
        and isinstance(last.value, ast.Call)
        and _tool(last.value.func) == "stop"
        and len(last.value.args) == 1
    ):
        return Stop(_ref(last.value.args[0]))
    raise _fail(last, "a block ends in a return, a stop or a test")


def _block(
    statements: Sequence[ast.stmt], parent: ast.AST | None = None, *, looping: bool = False
) -> Block:
    """Read statements as a block; in a loop's body, running out of them ends the iteration."""
    statements = [s for s in statements if not isinstance(s, ast.Pass)]
    calls: list[Call] = []
    for at, statement in enumerate(statements):
        rest = statements[at + 1 :]
        if isinstance(statement, ast.For):
            return Block(tuple(calls), _loop(statement, _block(rest, statement, looping=looping)))
        if isinstance(statement, ast.If) and not statement.orelse and rest:
            # An `if` with no `else` before more statements: what follows is its other branch.
            return Block(tuple(calls), _branch(_with_else(statement, rest), looping=looping))
        if isinstance(statement, ast.Try):
            calls.append(_guarded_call(statement, looping=looping))
        elif isinstance(statement, ast.Assign):
            calls.append(_call(statement))
        elif rest:
            raise _fail(statement, "only calls and loops come before a block's last statement")
        else:
            return Block(tuple(calls), _ending(statement, looping=looping))
    if looping:
        return Block(tuple(calls), Next())
    raise _fail(
        statements[-1] if statements else parent or ast.Pass(),
        "a block ends in a return, a stop or a test",
    )


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

    @property
    def verified(self) -> bool:
        """Return whether every link is proven, whatever paths stop."""
        return self.verification.verified

    @property
    def weak(self) -> bool:
        """Return whether some path returns and each stop is one the task tolerates."""
        return self.verification.weak


def _guard(claim: str) -> str | None:
    """Return the test that narrows a binding's value into its argument, if the claim is one."""
    subject, _, slot = claim.partition(" <= ")
    if not slot or subject.startswith("return ") or "." not in slot:
        return None
    if subject[0] in "'\"-0123456789[{" or subject in ("True", "False", "None"):
        return f"write a constant {slot} admits"
    return f"test `if {subject} in {slot}:` and make the call in that branch"


_POLICY = (
    "this path reaches a bad state of the policy or ends in a pending one: make the call it "
    "requires where it requires it, or leave out the call it forbids"
)
_IMPLICIT = "make the sink call outside the branch that tests the untrusted value"
_EXPLICIT = (
    "pass a value from a source the rule trusts, or test it into the rule's endorsed set first"
)
_RETRY = (
    "retry only a read-only or idempotent tool; give this call a `try:`/`except ToolError:` block"
)
_UNHANDLED = (
    "give the call a `try:`/`except ToolError:` block, or `retry(...)` a read-only or "
    "idempotent tool"
)


#: The claim of a link reading a reference the program may not hold there.
_HELD = " is a value the program holds"


def _keyed(link: Link) -> str | None:
    """Return the key test that holds a field a link reads untested, if the link is one.

    A record found lacking the key, or one whose key valgebra leaves undecided, holds the field
    under a test showing the key; an unbound variable or an absent record above it does not.
    """
    subject = link.claim.removesuffix(_HELD)
    if subject == link.claim or not (link.counterexample or link.verdict == "undecided"):
        return None
    try:
        ref = _ref(ast.parse(subject, mode="eval").body)
    except (SyntaxError, ProgramError):
        return None
    if not ref.path:
        return None
    record = Ref(ref.variable, ref.path[:-1])
    return f"test `if {json.dumps(ref.path[-1])} in {record}:` and read `{ref}` in that branch"


def _remedy(link: Link) -> str | None:
    """Return what a program's author can change to prove a link, if the claim is one."""
    claim = link.claim
    keyed = _keyed(link)
    if keyed is not None:
        return keyed
    if claim.startswith("policy "):
        return _POLICY
    if claim.startswith("flow ") and " runs under a test on " in claim:
        return _IMPLICIT
    if claim.startswith("flow "):
        return _EXPLICIT
    if claim.endswith(" may be retried"):
        return _RETRY
    return _guard(claim)


def _crossed(block: Block, made: dict[str, str], types: Types) -> list[str]:
    """Return each binding whose value and argument the observed calls show as two types."""
    made = made | {call.result: call.tool for call in block.calls}
    notes = []
    for call in block.calls:
        for name, ref in call.bindings:
            if isinstance(ref, Const) or ref.variable not in made:
                continue
            source = ("result", ".".join((made[ref.variable], *ref.path)))
            target = ("argument", f"{call.tool}.{name}")
            if types.typed(source, target) and not types.agree(source, target):
                notes.append(f"assumed: the observed calls show {ref} and {target[1]} as two types")
        if call.on_error is not None:
            notes += _crossed(call.on_error, made, types)
    if isinstance(block.end, Branch):
        notes += _crossed(block.end.then, made, types) + _crossed(block.end.otherwise, made, types)
    elif isinstance(block.end, For):
        notes += _crossed(block.end.body, made, types) + _crossed(block.end.after, made, types)
    return notes


def _gap(ref: str) -> str:
    if ref.startswith("an error of "):
        return f"uncovered: {ref}; {_UNHANDLED}"
    return f"uncovered: {ref}"


def critique(
    source: str,
    contracts: Mapping[str, Contract],
    task: Task,
    *,
    examples: Mapping[str, Sequence[Any]] | None = None,
    errors: bool = False,
    policies: Sequence[Policy] = (),
    flows: Sequence[Flow] = (),
    types: Types | None = None,
) -> Critique:
    """Read, verify and critique a program proposed for ``task`` under the rules it is held to.

    ``errors``, ``policies`` and ``flows`` are held as `valgebra_planning.verify.verify` holds
    them. Mined ``types`` decide nothing: a binding crossing two types the observed calls show is
    a note, and the program is accepted or not on its links alone. Raises `ProgramError` for a
    program the reader does not read.
    """
    program = read_program(source)
    checked = verify(
        program, contracts, task, examples=examples, errors=errors, policies=policies, flows=flows
    )
    notes = []
    for link in checked.links:
        if link.verdict == "proven":
            continue
        where = f" (at {link.where})" if link.where else ""
        note = f"{link.verdict}: {link.claim}{where}"
        if link.counterexample:
            note += f"; {link.counterexample[0]!r} lies outside"
        remedy = _remedy(link)
        if remedy is not None:
            note += f"; {remedy}"
        notes.append(note)
    notes += [
        f"{_gap(gap.ref)} (at {gap.where or 'top'})" + ("; the task tolerates it" * gap.tolerated)
        for gap in checked.gaps
    ]
    notes += [f"assumed: {a}" for a in sorted({a for link in checked.links for a in link.assumed})]
    if types is not None:
        notes += _crossed(program, {}, types)
    return Critique(program, checked, tuple(notes))
