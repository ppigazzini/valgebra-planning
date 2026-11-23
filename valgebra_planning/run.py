"""A program run on concrete values, each argument and result checked against its contract.

The runner calls the tools a program names with the values the program binds, takes each
branch by testing the value it reads, and checks every argument record against the tool's
input set, every policy's step before the call, every result against its output set and the
returned value against the goal. It fails closed: a broken check stops the run before the call
is made, the result bound or the value returned, and the run names the check. A field the
program reads and the value it holds lacks ends the run, named as a check too, since no value
can be passed on in its place. A proof of the
program rests on the contracts; a tool that answers outside its own is what a run must not pass
on. With ``enforce=False`` a break is recorded and the run goes on, so one run reports every
break it meets: a diagnosis, never a guard.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import Awaitable, Callable, Generator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from valgebra_planning._valgebra import JSON, Validator, json_key
from valgebra_planning.contracts import (
    Contract,
    Postcondition,
    Task,
    contract_names,
    declared,
    failures,
    listed,
    unclashed,
)
from valgebra_planning.policy import Monitor, Policy
from valgebra_planning.program import Block, Branch, Call, Const, For, Next, Ref, Return, Stop

__all__ = [
    "CanceledRunError",
    "Outcome",
    "Refusal",
    "RefusedResultError",
    "Run",
    "ToolError",
    "arun",
    "blame",
    "run",
]

#: How many times a retried call is made before the run gives up on it.
ATTEMPTS = 8


def retry_assumptions(contract: Contract) -> tuple[str, ...]:
    """Return what a retry of a tool rests on: its errors transient, its annotations unless trusted.

    A retry is a strong cyclic solution (Daniele et al. 2000, Def. 7), ending under fairness
    after finitely many failures, with no bound on how many (Cimatti et al. 2003, §2.3); the
    runner makes the call at most `ATTEMPTS` times, so a retry rests on the tool's errors
    being transient within that many attempts. The annotations saying the tool is read-only
    or idempotent are a hint the server gives, untrusted unless the server is.
    """
    return (
        *annotated(contract),
        f"an error of {contract.name} is transient within {ATTEMPTS} attempts",
    )


def annotated(contract: Contract) -> tuple[str, ...]:
    """Return the assumption that a tool's annotations hold, unless its server is trusted."""
    return (
        ()
        if contract.trusted
        else (f"the annotations of {contract.name}, a hint its server gives",)
    )


#: Every value a result may be where its contract declares no output set.
_JSON = Validator(JSON)


class ToolError(Exception):
    """A tool answered with an error: an MCP result with ``isError`` set."""


class RefusedResultError(Exception):
    """A tool's result its caller refused before handing it back, against its output schema.

    The message is the tool's name alone: the refused value reaches no reader through it.
    """


@dataclass(frozen=True, slots=True)
class Refusal:
    """Which check ended a run.

    One of `inputs`, `arguments`, `policy`, `given`, `result`, `closed`, `postcondition`,
    `goal`, `error`, `raised`, `read`, `ended` or `canceled`: `closed` is a result inside the
    output schema its server declares and outside its closed reading, and `postcondition` a
    read outside what a postcondition of the library says it shows.

    ``tool`` is the tool the check was on, where it was on one.
    """

    check: str
    tool: str | None = None
    retried: bool = False


#: How a run ends, as `Run.outcome` reads it.
Outcome = Literal["returned", "stopped", "refused", "error", "ended", "canceled"]

#: A call the interpreter asks of its driver: the tool's name and the arguments it is given.
_Asked = tuple[str, dict[str, Any]]
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class _Raised:
    """What a driver hands back for a call that raised: the exception `_Runner.attempts` reads."""

    error: Exception


#: A walk of the interpreter: it yields each call it asks, is sent the call's result or what it
#: raised as `_Raised`, and returns what it ends in.
_Steps = Generator[_Asked, Any, _T]


@dataclass(frozen=True, slots=True)
class Run:
    """What a run returned or where it stopped, and every contract it broke on the way.

    `outcome` reads how it ended, and `ok` whether it returned with nothing broken; read either
    before ``value``, which holds a different thing for each outcome:

    - ``returned``: ``value`` is the value returned, ``stopped`` and ``refused`` are None;
    - ``stopped``: ``value`` is the value the program stopped on, the one its plan leaves
      uncovered, and ``stopped`` the reference holding it;
    - ``refused``: a check broke with contracts enforced, ``refused`` names it, ``why`` says
      which, and ``value`` is None;
    - ``error``: a tool answered with an error no block handles, ``stopped`` names the call,
      and ``value`` is None;
    - ``ended``: the program ended an iteration outside a loop, and ``value`` is None;
    - ``canceled``: `arun` was canceled while a call was awaited, ``stopped`` names the call and
      each obligation a policy may leave pending, and ``value`` is None.

    With ``enforce=False`` a run may return with ``broken`` holding every check it broke:
    its outcome is ``returned`` and it is not ``ok``.
    """

    value: Any
    stopped: str | None
    broken: tuple[str, ...]
    #: The broken check that ended the run before it went on, when contracts are enforced.
    refused: str | None = None
    #: Which check ended the run, for `blame`; one field, so a run nothing ends pays for one.
    why: Refusal | None = None

    @property
    def outcome(self) -> Outcome:
        """Return how the run ended."""
        if self.refused is not None:
            return "refused"
        if self.why is not None and self.why.check == "error":
            return "error"
        ending: dict[str | None, Outcome] = {"ended": "ended", "canceled": "canceled"}
        if self.why is not None and self.why.check in ending:
            return ending[self.why.check]
        return "returned" if self.stopped is None else "stopped"

    @property
    def ok(self) -> bool:
        """Return whether the run returned a value and broke no check on the way."""
        return self.outcome == "returned" and not self.broken


@dataclass(slots=True)
class _Runner:
    contracts: Mapping[str, Contract]
    tools: Mapping[str, Callable[..., Any]]
    task: Task
    monitors: list[Monitor]
    enforce: bool
    broken: list[str] = field(default_factory=list)
    postconditions: tuple[Postcondition, ...] = ()
    #: The last call made that may act, where it returned: its tool, arguments and result.
    last: tuple[str, dict[str, Any], Any] | None = None

    def breaks(self, message: str, check: str, tool: str | None = None) -> Run | None:
        """Record a broken check; return the run it ends when contracts are enforced."""
        self.broken.append(message)
        if self.enforce:
            return Run(None, None, tuple(self.broken), message, Refusal(check, tool))
        return None

    def given(self, inputs: Mapping[str, Any]) -> Run | None:
        """Check each input the task names against its set; return the run a broken one ends."""
        for var, slots in self.task.held.items():
            whole = next(slot for slot in slots if slot.path == ())
            if var not in inputs:
                message = f"the input {var} the task names is not given"
            elif not whole.values.is_valid(inputs[var]):
                detail = failures(whole.values, inputs[var], declared(whole.schema))
                message = f"the input {var} lies outside the task's ({detail})"
            else:
                continue
            refused = self.breaks(message, "inputs")
            if refused is not None:
                return refused
        return None

    def block(self, block: Block, env: dict[str, Any]) -> _Steps[Run | None]:
        """Run a block; return the run it ends, or None where an iteration of a loop ends."""
        for call in block.calls:
            outcome = yield from self.call(call, env)
            if outcome is None or isinstance(outcome, Run):
                return outcome
            env = outcome
        end = block.end
        if isinstance(end, Return):
            value = _read(env, end.ref)
            if not self.task.goal.is_valid(value):
                detail = failures(self.task.goal, value, declared(self.task.goal_schema))
                refused = self.breaks(f"returned a value outside the goal ({detail})", "goal")
                if refused is not None:
                    return refused
            return self.settled() or Run(value, None, tuple(self.broken))
        if isinstance(end, Stop):
            return self.settled() or Run(_read(env, end.ref), str(end.ref), tuple(self.broken))
        if isinstance(end, Next):
            return None
        if isinstance(end, For):
            return (yield from self.loop(end, env))
        return (yield from self.branch(end, env))

    def loop(self, end: For, env: dict[str, Any]) -> _Steps[Run | None]:
        """Run a loop's body for each item, then what follows; a body ending the run ends it."""
        items = _read(env, end.ref)
        for item in items if isinstance(items, list) else ():
            ended = yield from self.block(end.body, {**env, end.item: item})
            if ended is not None:
                return ended
        return (yield from self.block(end.after, env))

    def settled(self) -> Run | None:
        """Check each policy where the run ends; return the run a pending state ends."""
        for monitor in self.monitors:
            if monitor.ended():
                refused = self.breaks(monitor.violations[-1], "policy")
                if refused is not None:
                    return refused
        return None

    def call(self, call: Call, env: dict[str, Any]) -> _Steps[dict[str, Any] | Run | None]:
        """Make one call; return the values held after it, the run it ended, or None (`block`)."""
        contract = self.contracts[call.tool]
        arguments = {
            name: ref.value if isinstance(ref, Const) else _read(env, ref)
            for name, ref in call.bindings
        }
        refused = self.admitted(call.tool, contract, arguments)
        if refused is not None:
            return refused
        made = yield from self.attempts(call, arguments, env)
        if made is None or isinstance(made, Run):
            return made
        result = made[0]
        refused = self.returned(call, contract, arguments, result) or self.shows(
            call, arguments, result
        )
        if refused is not None:
            return refused
        if "readOnlyHint" not in contract.hints:
            self.last = (call.tool, arguments, result)
        return {**env, call.result: result}

    def shows(self, call: Call, arguments: dict[str, Any], result: Any) -> Run | None:
        """Check a read a postcondition covers: its result inside what the read shows.

        A postcondition covers a call to its ``read`` on arguments equal, as JSON values, to
        those its ``after`` was given at each pair, where that ``after`` is the last call made
        that may act and returned a result inside ``when``.
        """
        if self.last is None:
            return None
        tool, given, returned = self.last
        for post in self.postconditions:
            if post.read != call.tool or post.after != tool or not post.when.is_valid(returned):
                continue
            if any(
                mine not in arguments
                or theirs not in given
                or json_key(arguments[mine]) != json_key(given[theirs])
                for mine, theirs in post.given
            ):
                continue
            if not post.shows.is_valid(result):
                detail = failures(post.shows, result, declared(*post.schemas))
                message = f"{call.tool} returned a value outside {post.name} ({detail})"
                refused = self.breaks(message, "postcondition", call.tool)
                if refused is not None:
                    return refused
        return None

    def returned(
        self, call: Call, contract: Contract, arguments: dict[str, Any], result: Any
    ) -> Run | None:
        """Check a call's result: its set, then each policy's step on it."""
        # Where the contract declares no output set the result is still a JSON value: the cells
        # partition the JSON values, so a proof says nothing of any other.
        values = _JSON if contract.returns is None else contract.returns
        if not values.is_valid(result):
            if inspect.iscoroutine(result):
                # A coroutine handed back unawaited never runs: closed, it is never scheduled.
                result.close()
            check = "result"
            if contract.returns is None:
                outside = "the JSON values"
            elif contract.declared is not None and contract.declared.is_valid(result):
                outside, check = "its output schema read closed", "closed"
            elif contract.inferred:
                outside = "the output schema inferred from observed calls"
            else:
                outside = "its contract"
            detail = failures(values, result, contract_names(contract))
            message = f"{call.tool} returned a value outside {outside} ({detail})"
            refused = self.breaks(message, check, call.tool)
            if refused is not None:
                return refused
        for monitor in self.monitors:
            if monitor.returned(call.tool, arguments, result, contract.hints):
                refused = self.breaks(monitor.violations[-1], "policy", call.tool)
                if refused is not None:
                    return refused
        return None

    def admitted(self, tool: str, contract: Contract, arguments: dict[str, Any]) -> Run | None:
        """Check a call before it is made: its argument record, then each policy's step."""
        if not contract.accepts.is_valid(arguments):
            detail = failures(contract.accepts, arguments, contract_names(contract))
            refused = self.breaks(f"{tool} refuses its arguments ({detail})", "arguments", tool)
            if refused is not None:
                return refused
        for monitor in self.monitors:
            if monitor.observe(tool, arguments, contract.hints):
                refused = self.breaks(monitor.violations[-1], "policy", tool)
                if refused is not None:
                    return refused
        return None

    def attempts(
        self, call: Call, arguments: dict[str, Any], env: dict[str, Any]
    ) -> _Steps[tuple[Any] | Run | None]:
        """Make the call, retried as it says; return its result, or how its error block ended.

        The call is asked of the driver, which makes it and hands back its result, or the
        exception it raised as `_Raised`.
        """
        if call.tool not in self.tools:
            # Nothing is called, so no run goes on past the call, enforced or not.
            message = f"{call.tool} is no tool the run was given"
            self.broken.append(message)
            return Run(None, None, tuple(self.broken), message, Refusal("given", call.tool))
        for _ in range(ATTEMPTS if call.retry else 1):
            answer = yield (call.tool, arguments)
            if not isinstance(answer, _Raised):
                return (answer,)
            if isinstance(answer.error, ToolError):
                continue
            if isinstance(answer.error, RefusedResultError):
                # No result is handed back, so no run goes on past the call, enforced or not.
                message = (
                    f"{call.tool} returned a value outside its contract, refused by its caller"
                )
                self.broken.append(message)
                return Run(None, None, tuple(self.broken), message, Refusal("result", call.tool))
            # Neither retried nor handed to an error block: an error block answers a tool's
            # error, and an exception may be a stop asked of the tool or a defect of its driver.
            # Its text may hold a value, so the message names its class alone.
            message = f"{call.tool} raised {type(answer.error).__name__}"
            self.broken.append(message)
            return Run(None, None, tuple(self.broken), message, Refusal("raised", call.tool))
        # The call may not have acted: what follows starts from either set of policy states, as
        # the verifier walks it.
        for monitor in self.monitors:
            monitor.errored()
        if "readOnlyHint" not in self.contracts[call.tool].hints:
            # It may have acted with no result to read: no postcondition covers what follows.
            self.last = None
        if call.on_error is not None:
            return (yield from self.block(call.on_error, env))
        self.broken.append(f"{call.tool} answered with an error nothing handles")
        stopped = f"an error of {call.tool}"
        return Run(None, stopped, tuple(self.broken), None, Refusal("error", call.tool, call.retry))

    def branch(self, branch: Branch, env: dict[str, Any]) -> _Steps[Run | None]:
        if branch.key:
            value = _read(env, branch.ref)
            holds = isinstance(value, dict) and branch.key in value
            return (yield from self.block(branch.then if holds else branch.otherwise, env))
        if branch.tool:
            tested = self.contracts[branch.tool].argument(branch.argument).values
        else:
            tested = listed(branch.values)
        taken = branch.then if tested.is_valid(_read(env, branch.ref)) else branch.otherwise
        return (yield from self.block(taken, env))


class _AbsentError(Exception):
    """A reference the values a run holds do not reach."""

    def __init__(self, ref: Ref) -> None:
        super().__init__(str(ref))
        self.ref = ref


def _read(env: Mapping[str, Any], ref: Ref) -> Any:
    """Return the value ``ref`` names in ``env``, or raise `_AbsentError` where none holds it."""
    if ref.variable not in env:
        raise _AbsentError(ref)
    value = env[ref.variable]
    for name in ref.path:
        if not isinstance(value, dict) or name not in value:
            raise _AbsentError(ref)
        value = value[name]
    return value


def _program(runner: _Runner, program: Block, inputs: Mapping[str, Any]) -> _Steps[Run]:
    """Walk a program from its inputs to the run it ends in, asking the driver for each call."""
    refused = runner.given(inputs)
    if refused is not None:
        return refused
    try:
        ended = yield from runner.block(program, dict(inputs))
    except _AbsentError as absent:
        message = f"{absent.ref} is no value the run holds"
        runner.broken.append(message)
        return Run(None, None, tuple(runner.broken), message, Refusal("read"))
    if ended is None:
        runner.broken.append("the program ends an iteration outside a loop")
        return Run(None, "the end of an iteration", tuple(runner.broken), None, Refusal("ended"))
    return ended


def run(
    program: Block,
    contracts: Mapping[str, Contract],
    task: Task,
    inputs: Mapping[str, Any],
    tools: Mapping[str, Callable[..., Any]],
    *,
    policies: Sequence[Policy] = (),
    enforce: bool = True,
    postconditions: Sequence[Postcondition] = (),
) -> Run:
    """Run ``program`` on ``inputs``, calling ``tools`` by name, checking every contract.

    Each policy is stepped on a call before it is made, and reaching a bad state is a broken
    check like a broken contract, as is a run ending, in a return or a stop, in a pending one.
    With ``enforce``, the default, the first broken check ends the run, named in `Run.refused`;
    without it every break is recorded and the run goes on. Before the first call, each input
    the task names is held to its set, a broken check like the others. A tool raising anything
    but `ToolError` ends the run, its message naming the exception's class alone; a
    `BaseException` that is no `Exception` propagates. A call to a tool ``tools`` does not give
    ends the run at that call. A read a postcondition covers is held to what it says the read
    shows, a broken check like a broken contract. Raises `ValueError` for a task naming an
    input as a tool.
    """
    unclashed(task, contracts)
    runner = _Runner(contracts, tools, task, [Monitor(policy) for policy in policies], enforce)
    runner.postconditions = tuple(postconditions)
    steps = _program(runner, program, inputs)
    try:
        tool, arguments = next(steps)
        while True:
            tool, arguments = steps.send(_made(tools[tool], arguments))
    except StopIteration as ended:
        return ended.value


def _made(tool: Callable[..., Any], arguments: dict[str, Any]) -> Any:
    """Return a call's result, or `_Raised` holding what it raised, which the run reads.

    Any exception a tool raises ends the run named, or is the error a retry or a block takes,
    so each is handed back to the interpreter, never lost; a `BaseException` that is no
    `Exception` propagates.
    """
    try:
        return tool(**arguments)
    except Exception as error:  # noqa: BLE001 - handed back whole: the run reads and names it
        return _Raised(error)


#: Whether asyncio's timeouts read a subclass of `asyncio.CancelledError` as their own cancel.
#: From Python 3.13 `asyncio.timeout` and `asyncio.wait_for` do. 3.11's `asyncio.timeout` and
#: 3.12's two compare the class with `is`, so a subclass raised inside them passes through as
#: itself, and the caller never sees the `TimeoutError` it asked for.
_SUBCLASS_CANCELS = sys.version_info >= (3, 13)


class CanceledRunError(asyncio.CancelledError):
    """A run `arun` ended, canceled while it awaited a call: ``run`` names what it left.

    It is a `asyncio.CancelledError`, so from Python 3.13 a timeout or a task group canceling
    the run reads it as the cancel it is. On 3.11 and 3.12, whose timeouts read only that exact
    class as their cancel, `arun` raises the cancel it received instead, with a note naming what
    the run left, and raises no `CanceledRunError`.
    """

    def __init__(self, run: Run) -> None:
        super().__init__(run.stopped)
        self.run = run


async def arun(
    program: Block,
    contracts: Mapping[str, Contract],
    task: Task,
    inputs: Mapping[str, Any],
    tools: Mapping[str, Callable[..., Any]],
    *,
    policies: Sequence[Policy] = (),
    enforce: bool = True,
    postconditions: Sequence[Postcondition] = (),
) -> Run:
    """Run ``program`` as `run` does, awaiting each call a tool answers with an awaitable.

    The checks are `run`'s, in `run`'s order: one interpreter walks the program, and only the
    calls are made here. A tool may be a coroutine function or a plain one. A cancel while a
    call is awaited ends the run: the call may have acted, so each policy is read from either
    set of states, and `CanceledRunError` carries the run, its outcome ``canceled`` and
    ``stopped`` naming the call and each obligation a policy leaves pending. On Python 3.11 and
    3.12 the cancel received is raised instead, ``stopped`` its note (`CanceledRunError`).
    """
    unclashed(task, contracts)
    runner = _Runner(contracts, tools, task, [Monitor(policy) for policy in policies], enforce)
    runner.postconditions = tuple(postconditions)
    steps = _program(runner, program, inputs)
    try:
        tool, arguments = next(steps)
        while True:
            answer = _made(tools[tool], arguments)
            if inspect.isawaitable(answer):
                try:
                    answer = await _awaited(answer)
                except asyncio.CancelledError as cancel:
                    steps.close()
                    ended = _canceled(runner, tool)
                    if not _SUBCLASS_CANCELS:
                        cancel.add_note(f"{ended.stopped}")
                        raise
                    raise CanceledRunError(ended) from cancel
            tool, arguments = steps.send(answer)
    except StopIteration as ended:
        return ended.value


async def _awaited(pending: Awaitable[Any]) -> Any:
    """Return what an awaitable a tool answered with comes to, or `_Raised` holding its error."""
    try:
        return await pending
    except Exception as error:  # noqa: BLE001 - handed back whole: the run reads and names it
        return _Raised(error)


def _canceled(runner: _Runner, tool: str) -> Run:
    """Return the run a cancel ends at a call to ``tool``: what each policy leaves pending."""
    for monitor in runner.monitors:
        monitor.errored()
    pending = [
        f"{monitor.policy.name} may hold {state}"
        for monitor in runner.monitors
        for state in sorted(monitor.held() & monitor.policy.pending)
    ]
    left = "; ".join(pending) if pending else "no obligation left pending"
    stopped = f"canceled at {tool}: {left}"
    return Run(None, stopped, tuple(runner.broken), None, Refusal("canceled", tool))


#: What each check a proven program should never break holds, as `blame` names it.
_CHECKED = {
    "arguments": "an argument record",
    "policy": "a policy",
    "goal": "the value returned",
    "read": "a value the program reads",
}


def blame(result: Run, contracts: Mapping[str, Contract], *, proven: bool) -> str | None:
    """Return why a run broke, given whether its program was proven; None if nothing broke.

    A proven program breaks at run time only where something it rests on fails: a tool's result
    outside the output schema its server declared is the server's, one outside a schema shaped
    from observed calls is that assumption's, as one with a key its output schema does not
    declare is the closed reading's, a read outside what a postcondition says it shows is
    that declaration's, and an error a retry could not outlast is the fairness a retry
    assumes. An argument record, a policy or a returned value breaking in a
    proven program is a check the verification proved: a defect of this package or of valgebra.
    An unproven program's break is its own link's.
    """
    if result.why is None:
        return None
    check, tool = result.why.check, result.why.tool
    if check == "result" and tool is not None:
        if contracts[tool].returns is None:
            return f"{tool} returned a value that is no JSON value, which no contract admits"
        if contracts[tool].inferred:
            return f"the output schema of {tool} inferred from observed calls does not hold"
        return f"{tool} broke the output schema its server declares"
    if check == "closed":
        return f"{tool} returned a key its output schema does not declare: the closed reading"
    retried = f"an error of {tool} outlasted {ATTEMPTS} attempts: the fairness a retry assumes"
    named = {
        "inputs": "an input outside the task's input set: the caller's, before any call",
        "given": f"no tool {tool} was given to the run: the caller's",
        "raised": f"{tool} raised an exception, which no contract admits: the tool's",
        "postcondition": f"{tool} read a value a postcondition of the library excludes: its"
        " declaration",
        "error": retried
        if result.why.retried
        else f"an error of {tool} the program does not handle",
        "ended": "the program ends an iteration outside a loop: the program's",
        "canceled": f"the run was canceled at {tool}: the caller's",
    }
    if check in named:
        return named[check]
    broke = _CHECKED.get(check, check)
    if proven:
        return f"{broke} the verification proved broke: a defect to report"
    return f"{broke} no link of the program proves broke"
