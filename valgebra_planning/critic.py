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
import re
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from valgebra_planning.contracts import Contract, Postcondition, Task
from valgebra_planning.flows import Flow
from valgebra_planning.mining import SlotKey, Types
from valgebra_planning.policy import Policy
from valgebra_planning.program import (
    Block,
    Branch,
    Call,
    Const,
    For,
    Next,
    Ref,
    Return,
    Stop,
    aliases,
    named,
    slot,
)
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
                raise _fail(
                    node,
                    "a field is read by its name, as `c1.items`, and an array's items by a loop, "
                    "`for item in c1.items:`",
                )
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
    """Read the name a tool is called by: an identifier, its alias where the name is none.

    A typed tool API's namespace, ``tools.find_parcel``, is read as the name after it. Any
    other attribute is refused, where its last name was read: ``g1.grasp`` called ``grasp``,
    a tool of another name, while a tool named ``g1.grasp`` is called by its alias.
    """
    if isinstance(node, ast.Name):
        return node.id
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "tools"
    ):
        return node.attr
    raise _fail(node, "a tool is called by its name, or by its alias where the name has a dot")


def _named(node: ast.expr) -> str | None:
    return node.id if isinstance(node, ast.Name) else None


def _guarded_call(
    statement: ast.Try, rest: Sequence[ast.stmt] = (), *, looping: bool = False
) -> Call:
    """Read ``try: x = tool(...) except ToolError: <block>`` as a call with an error block.

    A handler that runs out runs on into what follows the `try`, as Python runs it.
    """
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
    handled = _block(_onto(handler.body, rest), handler, looping=looping)
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
    return Call(_tool(value.func), _bindings(value), statement.targets[0].id, retry=retry)


def _bindings(call: ast.Call) -> tuple[tuple[str, Ref | Const], ...]:
    """Read a call's arguments: each by keyword, or by name in a dictionary unpacked into it.

    ``**{"from": sender}`` passes an argument Python spells no keyword for, its key a string. An
    argument named twice is refused, as Python refuses the call.
    """
    bindings: dict[str, Ref | Const] = {}
    for keyword in call.keywords:
        named: Sequence[tuple[ast.expr | None, ast.expr]] = [(None, keyword.value)]
        if keyword.arg is None:
            if not isinstance(keyword.value, ast.Dict):
                raise _fail(call, "a call passes every argument by name")
            named = list(zip(keyword.value.keys, keyword.value.values, strict=True))
        for key, value in named:
            name = keyword.arg
            if name is None and isinstance(key, ast.Constant) and isinstance(key.value, str):
                name = key.value
            if name is None:
                raise _fail(call, "a call passes every argument by name")
            if name in bindings:
                raise _fail(call, f"the argument {name!r} is passed twice")
            bindings[name] = _argument(value)
    return tuple(bindings.items())


def _constants(node: ast.expr) -> tuple[Any, ...]:
    """Return the constants a test lists, each the literal it writes.

    An item is read as `render` writes it, by `repr`, so ``-1``, ``-0.5``, ``[1, 2]`` and
    ``{"a": 1}`` read back as the planner wrote them; an item that is no JSON value, as
    ``b"obj_1"``, ``...`` or ``1j``, is refused at its line, since no tool's argument holds it.
    """
    items = node.elts if isinstance(node, (ast.Tuple, ast.List, ast.Set)) else [node]
    values = []
    for item in items:
        try:
            value = ast.literal_eval(item)
        except ValueError:
            raise _fail(
                node, "a value is tested against a tool's argument or listed constants"
            ) from None
        if not _json(value):
            raise _fail(item, f"a test lists JSON values, and {value!r} is none")
        values.append(value)
    return tuple(values)


def _slot(node: ast.expr) -> tuple[str, str] | None:
    """Read a tool's argument as a test names it, ``send.to`` or ``send["from"]``, or None."""
    if isinstance(node, ast.Attribute):
        return _tool(node.value), node.attr
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return _tool(node.value), node.slice.value
    return None


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
        if not left.value:
            raise _fail(statement, "a key is tested by its name, and an empty one is none")
        return Branch(_ref(right), "", "", then, otherwise, key=left.value)
    ref = _ref(left)
    if isinstance(op, (ast.In, ast.NotIn)) and (tested := _slot(right)) is not None:
        return Branch(ref, *tested, then, otherwise)
    if (
        isinstance(op, (ast.In, ast.NotIn))
        and isinstance(right, ast.Constant)
        and isinstance(right.value, (str, bytes))
    ):
        raise _fail(statement, "`in` a string tests a substring; list the members as a tuple")
    if isinstance(op, (ast.Eq, ast.NotEq)) and not isinstance(right, ast.Constant):
        raise _fail(statement, "a value is compared for equality with a constant")
    if not isinstance(op, (ast.In, ast.NotIn, ast.Eq, ast.NotEq)):
        raise _fail(statement, "a test is a membership or an equality")
    return Branch(ref, "", "", then, otherwise, _constants(right))


def _falling(statement: ast.If, rest: Sequence[ast.stmt]) -> ast.If:
    """Return ``statement`` with ``rest`` after each side of it that runs out."""
    return ast.If(
        test=statement.test,
        body=_onto(statement.body, rest),
        orelse=_onto(statement.orelse, rest),
        lineno=statement.lineno,
    )


def _onto(side: Sequence[ast.stmt], rest: Sequence[ast.stmt]) -> list[ast.stmt]:
    """Return ``side`` followed by ``rest`` where control runs out of it, as Python runs on."""
    return [*side, *rest] if _runs_out(side) else list(side)


def _runs_out(statements: Sequence[ast.stmt]) -> bool:
    """Return whether control may reach the end of ``statements``, as Python runs them.

    It does past every statement but a `return`, a `raise`, a `continue`, a `break` and a
    `stop`, and past an `if` or a `try` where one of its sides does.
    """
    body = [s for s in statements if not isinstance(s, ast.Pass)]
    if not body:
        return True
    last = body[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return False
    if (
        isinstance(last, ast.Expr)
        and isinstance(last.value, ast.Call)
        and isinstance(last.value.func, ast.Name)
        and last.value.func.id == "stop"
    ):
        return False
    if isinstance(last, ast.If):
        return _runs_out(last.body) or _runs_out(last.orelse)
    if isinstance(last, ast.Try):
        return _runs_out(last.body) or any(_runs_out(h.body) for h in last.handlers)
    return True


def _loop(statement: ast.For, rest: Block) -> For:
    """Read ``for item in ref:`` as a loop whose body ends each iteration, then ``rest``."""
    if not isinstance(statement.target, ast.Name) or statement.orelse:
        raise _fail(statement, "a loop names one item and has no `else`")
    body = _block(statement.body, statement, looping=True)
    return For(_ref(statement.iter), statement.target.id, body, rest)


def _ending(last: ast.stmt, *, looping: bool) -> Return | Stop | Branch:
    """Read a block's last statement: a return, a stop, or a test with a block on each side.

    A `return` in a loop's body ends the program at the first item reaching it. Over a finite
    array each iteration takes the next item, so the program's structure stays acyclic and its
    terminal states are its returns and stops, each return in the goal where its link is
    proven: a strong solution's structure (Cimatti et al. 2003, Def. 2.10), no fairness assumed.
    """
    if isinstance(last, ast.Return) and last.value is not None:
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


def _misplaced(statement: ast.stmt, number: int) -> str:
    """Return what a statement standing before a block's last one should be written as."""
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
        called = statement.value.func
        if isinstance(called, ast.Name) and called.id == "stop":
            return (
                "a `stop` ends its path: nothing after it runs, so it is a block's last statement"
            )
        tool = called.id if isinstance(called, ast.Name) else "tool"
        return f"a call's result is bound to a name: `c{number} = {tool}(...)`"
    if isinstance(statement, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)):
        return "the tools are given: write the program alone, with no import, function or class"
    return (
        "before a block's last statement come calls, each bound to a name, a `try` around one, "
        "and loops"
    )


#: Each run of statements a reading has read, by the statements and whether a loop holds them,
#: so the rest after an `if`, which each side that runs out carries, is read once and shared.
_READ: ContextVar[dict[tuple[tuple[int, ...], bool], Block] | None] = ContextVar(
    "_READ", default=None
)


def _block(
    statements: Sequence[ast.stmt], parent: ast.AST | None = None, *, looping: bool = False
) -> Block:
    """Read statements as a block; in a loop's body, running out of them ends the iteration."""
    statements = [s for s in statements if not isinstance(s, ast.Pass)]
    read = _READ.get()
    key = (tuple(map(id, statements)), looping)
    if read is not None and statements and key in read:
        return read[key]
    block = _statements(statements, parent, looping=looping)
    if read is not None and statements:
        read[key] = block
    return block


def _statements(statements: Sequence[ast.stmt], parent: ast.AST | None, *, looping: bool) -> Block:
    """Read statements as a block, each `if` and handler running on into what follows it."""
    calls: list[Call] = []
    for at, statement in enumerate(statements):
        rest = statements[at + 1 :]
        if isinstance(statement, ast.For):
            return Block(tuple(calls), _loop(statement, _block(rest, statement, looping=looping)))
        if isinstance(statement, ast.If) and rest:
            # Python runs what follows an `if` after each side that runs out, an absent
            # `else` among them: each such side carries the rest.
            return Block(tuple(calls), _branch(_falling(statement, rest), looping=looping))
        if isinstance(statement, ast.Try):
            calls.append(_guarded_call(statement, rest, looping=looping))
        elif isinstance(statement, ast.Assign):
            calls.append(_call(statement))
        elif rest:
            raise _fail(statement, _misplaced(statement, len(calls) + 1))
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

    Raises `ProgramError` naming the line of a construct it does not read, and the line of
    text Python cannot parse, as Python names it.
    """
    try:
        module = ast.parse(source)
    except SyntaxError as unparsed:
        # A block indented whole, as one copied from inside a function is, parses as a
        # function's body; a bare `return` parses on its own.
        try:
            module = ast.parse(
                "def plan():\n" + "\n".join("    " + line for line in source.splitlines())
            )
        except SyntaxError:
            line = unparsed.lineno if unparsed.lineno is not None else "?"
            what = f"line {line}: {unparsed.msg}"
            raise ProgramError(what) from unparsed
    body = module.body
    if len(body) == 1 and isinstance(body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        body = body[0].body
    body = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    reading = _READ.set({})
    try:
        program = _block(body)
    finally:
        _READ.reset(reading)
    paths = _paths(program, {})
    if paths > MAX_PATHS:
        what = (
            f"the program runs {paths} paths, past {MAX_PATHS}: each `if` in a row that runs on "
            "doubles them; end a side with a return or a stop"
        )
        raise ProgramError(what)
    return program


#: The paths a program may take: each `if` whose sides run on into what follows doubles them,
#: and the verifier walks each.
MAX_PATHS = 4096


def _paths(block: Block, counted: dict[int, int]) -> int:
    """Return the paths ``block`` takes to its ends, each shared block counted once."""
    if id(block) in counted:
        return counted[id(block)]
    end = block.end
    if isinstance(end, Branch):
        total = _paths(end.then, counted) + _paths(end.otherwise, counted)
    elif isinstance(end, For):
        total = _paths(end.body, counted) + _paths(end.after, counted)
    else:
        total = 1
    total += sum(_paths(call.on_error, counted) for call in block.calls if call.on_error)
    counted[id(block)] = total
    return total


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


def _guard(claim: str, spelled: Mapping[str, str]) -> str | None:
    """Return the test that narrows a binding's value into its argument, if the claim is one.

    ``spelled`` holds how a program names each argument, by the name a claim does: a claim
    writes ``g1.grasp.width``, and a test of it ``g1_grasp.width``.
    """
    subject, _, named = claim.partition(" <= ")
    if not named or subject.startswith("return ") or "." not in named:
        return None
    argument = spelled.get(named, named)
    if subject[0] in "'\"-0123456789[{" or subject in ("True", "False", "None"):
        return f"write a constant {argument} admits"
    return f"test `if {subject} in {argument}:` and make the call in that branch"


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


#: A fresh rule's claim on a source's value, and a keyed rule's: the source, the calls, the sink.
_FRESH = re.compile(
    r"^flow .+?: \S+ from (?P<source>\S+) was returned after every call (?:to (?P<named>.+) )?"
    r"before (?P<sink>\S+)(?: that may act)?$"
)
_KEY = re.compile(
    r"^flow .+?: \S+ from (?P<source>\S+) was returned for the (?P<key>\S+) (?P<sink>\S+) takes$"
)


def _reread(claim: str) -> str | None:
    """Return the reading a fresh or keyed rule's refuted claim asks for, if the claim is one.

    The value came from a source the rule trusts, so passing another from it is no remedy: it
    is read again where the rule asks, after the last call that may act, or that the rule
    names, before the sink, or for the key the sink's call takes.
    """
    if (fresh := _FRESH.match(claim)) is not None:
        named = fresh["named"]
        after = f"the last call to {named}" if named else "the last call that may act"
        source, sink = fresh["source"], fresh["sink"]
        return f"read {source} again after {after} before {sink}, and pass that reading"
    if (keyed := _KEY.match(claim)) is not None:
        source, key, sink = keyed["source"], keyed["key"], keyed["sink"]
        return f"read {source} for the {key} {sink} takes, and pass that reading"
    return None


def _remedy(link: Link, spelled: Mapping[str, str]) -> str | None:
    """Return what a program's author can change to prove a link, if the claim is one."""
    claim = link.claim
    keyed = _keyed(link)
    if keyed is not None:
        return keyed
    if claim.startswith("policy "):
        return _POLICY
    if claim.startswith("flow ") and " runs under a test on " in claim:
        return _IMPLICIT
    if claim.startswith("flow ") and (reading := _reread(claim)) is not None:
        return reading
    if claim.startswith("flow "):
        return _EXPLICIT
    if claim.endswith(" may be retried"):
        return _RETRY
    return _guard(claim, spelled)


def _crossing(
    ref: Ref, target: SlotKey, made: dict[str, str], types: Types, inputs: frozenset[str]
) -> str | None:
    """Return the note a binding of ``ref`` into ``target`` crossing the observed types takes."""
    if ref.variable in made:
        source = ("result", ".".join((made[ref.variable], *ref.path)))
    elif ref.variable in inputs:
        source = ("input", ".".join((ref.variable, *ref.path)))
    else:
        return None
    if types.agree(source, target):
        return None
    if types.typed(source, target):
        return f"assumed: the observed calls show {ref} and {target[1]} as two types"
    return (
        f"assumed: the observed calls show {target[1]} taking a tool's result, and {ref} is a"
        " task input they never show"
    )


def _crossed(block: Block, made: dict[str, str], types: Types, inputs: frozenset[str]) -> list[str]:
    """Return each binding whose value and argument the observed calls show as two types.

    A task input never observed is noted where it fills an argument a tool's result was seen in.
    """
    made = made | {call.result: call.tool for call in block.calls}
    notes = [
        note
        for call in block.calls
        for name, ref in call.bindings
        if isinstance(ref, Ref)
        and (note := _crossing(ref, ("argument", f"{call.tool}.{name}"), made, types, inputs))
    ]
    nested = [call.on_error for call in block.calls if call.on_error is not None]
    if isinstance(block.end, Branch):
        nested += [block.end.then, block.end.otherwise]
    elif isinstance(block.end, For):
        nested += [block.end.body, block.end.after]
    for inner in nested:
        notes += _crossed(inner, made, types, inputs)
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
    postconditions: Sequence[Postcondition] = (),
) -> Critique:
    """Read, verify and critique a program proposed for ``task`` under the rules it is held to.

    ``errors``, ``policies``, ``flows`` and ``postconditions`` are held as
    `valgebra_planning.verify.verify` holds them. Mined ``types`` decide nothing: a binding
    crossing two types the observed calls show is a note, and the program is accepted or not on
    its links alone. Raises `ProgramError` for a program the reader does not read. A tool
    whose name Python does not spell is called by its alias (`valgebra_planning.program.alias`).
    """
    program = named(read_program(source), aliases(contracts))
    checked = verify(
        program,
        contracts,
        task,
        examples=examples,
        errors=errors,
        policies=policies,
        flows=flows,
        postconditions=postconditions,
    )
    spelled = {
        argument.name: slot(contract.name, argument.path[0])
        for contract in contracts.values()
        for argument in contract.arguments
    }
    notes = []
    for link in checked.links:
        if link.verdict == "proven":
            continue
        where = f" (at {link.where})" if link.where else ""
        note = f"{link.verdict}: {link.claim}{where}"
        if link.counterexample:
            note += f"; {link.counterexample[0]!r} lies outside"
        remedy = _remedy(link, spelled)
        if remedy is not None:
            note += f"; {remedy}"
        notes.append(note)
    notes += [
        f"{_gap(gap.ref)} (at {gap.where or 'top'})" + ("; the task tolerates it" * gap.tolerated)
        for gap in checked.gaps
    ]
    notes += [f"assumed: {a}" for a in sorted({a for link in checked.links for a in link.assumed})]
    if types is not None:
        notes += _crossed(program, {}, types, frozenset(task.held))
    return Critique(program, checked, tuple(notes))
