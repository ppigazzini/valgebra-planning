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

import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from valgebra_planning._valgebra import JSON, Validator
from valgebra_planning.contracts import (
    Contract,
    Task,
    contract_names,
    declared,
    failures,
    listed,
    unclashed,
)
from valgebra_planning.policy import Monitor, Policy
from valgebra_planning.program import Block, Branch, Call, Const, For, Next, Ref, Return, Stop

__all__ = ["Refusal", "RefusedResultError", "Run", "ToolError", "blame", "run"]

#: How many times a retried call is made before the run gives up on it.
ATTEMPTS = 8


def retry_assumptions(contract: Contract) -> tuple[str, ...]:
    """Return what a retry of a tool rests on: its errors transient, its annotations unless trusted.

    A retry is a strong cyclic solution (Daniele et al. 2000, Def. 7), sound under fairness
    (Cimatti et al. 2003, Def. 2.10): an error is transient, so one of the runner's attempts
    succeeds. The annotations saying the tool is
    read-only or idempotent are a hint the server gives, untrusted unless the server is.
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
    """Which check ended a run: `arguments`, `policy`, `result`, `goal`, `error` or `read`.

    ``tool`` is the tool the check was on, where it was on one.
    """

    check: str
    tool: str | None = None
    retried: bool = False


@dataclass(frozen=True, slots=True)
class Run:
    """What a run returned or where it stopped, and every contract it broke on the way."""

    value: Any
    stopped: str | None
    broken: tuple[str, ...]
    #: The broken check that ended the run before it went on, when contracts are enforced.
    refused: str | None = None
    #: Which check ended the run, for `blame`; one field, so a run nothing ends pays for one.
    why: Refusal | None = None


@dataclass(slots=True)
class _Runner:
    contracts: Mapping[str, Contract]
    tools: Mapping[str, Callable[..., Any]]
    task: Task
    monitors: list[Monitor]
    enforce: bool
    broken: list[str] = field(default_factory=list)

    def breaks(self, message: str, check: str, tool: str | None = None) -> Run | None:
        """Record a broken check; return the run it ends when contracts are enforced."""
        self.broken.append(message)
        if self.enforce:
            return Run(None, None, tuple(self.broken), message, Refusal(check, tool))
        return None

    def block(self, block: Block, env: dict[str, Any]) -> Run | None:
        """Run a block; return the run it ends, or None where an iteration of a loop ends."""
        for call in block.calls:
            outcome = self.call(call, env)
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
            return self.loop(end, env)
        return self.branch(end, env)

    def loop(self, end: For, env: dict[str, Any]) -> Run | None:
        """Run a loop's body for each item, then what follows; a body ending the run ends it."""
        items = _read(env, end.ref)
        for item in items if isinstance(items, list) else ():
            ended = self.block(end.body, {**env, end.item: item})
            if ended is not None:
                return ended
        return self.block(end.after, env)

    def settled(self) -> Run | None:
        """Check each policy where the run ends; return the run a pending state ends."""
        for monitor in self.monitors:
            if monitor.ended():
                refused = self.breaks(monitor.violations[-1], "policy")
                if refused is not None:
                    return refused
        return None

    def call(self, call: Call, env: dict[str, Any]) -> dict[str, Any] | Run | None:
        """Make one call; return the values held after it, the run it ended, or None (`block`)."""
        contract = self.contracts[call.tool]
        arguments = {
            name: ref.value if isinstance(ref, Const) else _read(env, ref)
            for name, ref in call.bindings
        }
        refused = self.admitted(call.tool, contract, arguments)
        if refused is not None:
            return refused
        made = self.attempts(call, arguments, env)
        if made is None or isinstance(made, Run):
            return made
        result = made[0]
        # Where the contract declares no output set the result is still a JSON value: the cells
        # partition the JSON values, so a proof says nothing of any other.
        values = _JSON if contract.returns is None else contract.returns
        if not values.is_valid(result):
            if inspect.iscoroutine(result):
                # A coroutine handed back unawaited never runs: closed, it is never scheduled.
                result.close()
            if contract.returns is None:
                outside = "the JSON values"
            elif contract.inferred:
                outside = "the output schema inferred from observed calls"
            else:
                outside = "its contract"
            detail = failures(values, result, contract_names(contract))
            message = f"{call.tool} returned a value outside {outside} ({detail})"
            refused = self.breaks(message, "result", call.tool)
            if refused is not None:
                return refused
        return {**env, call.result: result}

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
    ) -> tuple[Any] | Run | None:
        """Make the call, retried as it says; return its result, or how its error block ended."""
        for _ in range(ATTEMPTS if call.retry else 1):
            try:
                return (self.tools[call.tool](**arguments),)
            except ToolError:
                continue
            except RefusedResultError:
                # No result is handed back, so no run goes on past the call, enforced or not.
                message = (
                    f"{call.tool} returned a value outside its contract, refused by its caller"
                )
                self.broken.append(message)
                return Run(None, None, tuple(self.broken), message, Refusal("result", call.tool))
        # The call may not have acted: what follows starts from either set of policy states, as
        # the verifier walks it.
        for monitor in self.monitors:
            monitor.errored()
        if call.on_error is not None:
            return self.block(call.on_error, env)
        self.broken.append(f"{call.tool} answered with an error nothing handles")
        stopped = f"an error of {call.tool}"
        return Run(None, stopped, tuple(self.broken), None, Refusal("error", call.tool, call.retry))

    def branch(self, branch: Branch, env: dict[str, Any]) -> Run | None:
        if branch.key:
            value = _read(env, branch.ref)
            holds = isinstance(value, dict) and branch.key in value
            return self.block(branch.then if holds else branch.otherwise, env)
        if branch.tool:
            tested = self.contracts[branch.tool].argument(branch.argument).values
        else:
            tested = listed(branch.values)
        taken = branch.then if tested.is_valid(_read(env, branch.ref)) else branch.otherwise
        return self.block(taken, env)


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


def run(
    program: Block,
    contracts: Mapping[str, Contract],
    task: Task,
    inputs: Mapping[str, Any],
    tools: Mapping[str, Callable[..., Any]],
    *,
    policies: Sequence[Policy] = (),
    enforce: bool = True,
) -> Run:
    """Run ``program`` on ``inputs``, calling ``tools`` by name, checking every contract.

    Each policy is stepped on a call before it is made, and reaching a bad state is a broken
    check like a broken contract, as is a run ending, in a return or a stop, in a pending one.
    With ``enforce``, the default, the first broken check ends the run, named in `Run.refused`;
    without it every break is recorded and the run goes on. Raises `ValueError` for a task
    naming an input as a tool.
    """
    unclashed(task, contracts)
    monitors = [Monitor(policy) for policy in policies]
    runner = _Runner(contracts, tools, task, monitors, enforce)
    try:
        ended = runner.block(program, dict(inputs))
    except _AbsentError as absent:
        message = f"{absent.ref} is no value the run holds"
        runner.broken.append(message)
        return Run(None, None, tuple(runner.broken), message, Refusal("read"))
    if ended is None:
        runner.broken.append("the program ends an iteration outside a loop")
        return Run(None, "the end of an iteration", tuple(runner.broken))
    return ended


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
    from observed calls is that assumption's, and an error a retry could not outlast is the
    fairness a retry assumes. An argument record, a policy or a returned value breaking in a
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
    if check == "error":
        if result.why.retried:
            return f"an error of {tool} outlasted {ATTEMPTS} attempts: the fairness a retry assumes"
        return f"an error of {tool} the program does not handle"
    broke = _CHECKED.get(check, check)
    if proven:
        return f"{broke} the verification proved broke: a defect to report"
    return f"{broke} no link of the program proves broke"
