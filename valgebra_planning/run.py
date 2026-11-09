"""A program run on concrete values, each argument and result checked against its contract.

The runner calls the tools a program names with the values the program binds, takes each
branch by testing the value it reads, and checks every argument record against the tool's
input set, every policy's step before the call, every result against its output set and the
returned value against the goal. It fails closed: a broken check stops the run before the call
is made, the result bound or the value returned, and the run names the check. A proof of the
program rests on the contracts; a tool that answers outside its own is what a run must not pass
on. With ``enforce=False`` a break is recorded and the run goes on, so one run reports every
break it meets: a diagnosis, never a guard.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from valgebra_planning.contracts import Contract, Task, listed
from valgebra_planning.policy import Monitor, Policy
from valgebra_planning.program import Block, Branch, Call, Const, For, Next, Ref, Return, Stop

#: How many times a retried call is made before the run gives up on it.
ATTEMPTS = 8


def retry_assumptions(contract: Contract) -> tuple[str, ...]:
    """Return what a retry of a tool rests on: its errors transient, its annotations unless trusted.

    A retry is a strong cyclic solution, sound under fairness (Daniele et al. 2000): an error is
    transient, so one of the runner's attempts succeeds. The annotations saying the tool is
    read-only or idempotent are a hint the server gives, untrusted unless the server is.
    """
    transient = f"an error of {contract.name} is transient within {ATTEMPTS} attempts"
    if contract.trusted:
        return (transient,)
    return (f"the annotations of {contract.name}, a hint its server gives", transient)


class ToolError(Exception):
    """A tool answered with an error: an MCP result with ``isError`` set."""


@dataclass(frozen=True, slots=True)
class Refusal:
    """Which check ended a run: `arguments`, `policy`, `result`, `goal` or `error`, and its tool."""

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
                refused = self.breaks(f"returned {value!r:.120}, outside the goal", "goal")
                if refused is not None:
                    return refused
            return Run(value, None, tuple(self.broken))
        if isinstance(end, Stop):
            return Run(_read(env, end.ref), str(end.ref), tuple(self.broken))
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
        if contract.returns is not None and not contract.returns.is_valid(result):
            outside = (
                "the output schema inferred from observed calls"
                if contract.inferred
                else "its contract"
            )
            message = f"{call.tool} returned {result!r:.120}, outside {outside}"
            refused = self.breaks(message, "result", call.tool)
            if refused is not None:
                return refused
        return {**env, call.result: result}

    def admitted(self, tool: str, contract: Contract, arguments: dict[str, Any]) -> Run | None:
        """Check a call before it is made: its argument record, then each policy's step."""
        if not contract.accepts.is_valid(arguments):
            refused = self.breaks(
                f"{tool} refuses its arguments {arguments!r:.120}", "arguments", tool
            )
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


def _read(env: Mapping[str, Any], ref: Ref) -> Any:
    value = env[ref.variable]
    for name in ref.path:
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
    check like a broken contract. With ``enforce``, the default, the first broken check ends
    the run, named in `Run.refused`; without it every break is recorded and the run goes on.
    """
    monitors = [Monitor(policy) for policy in policies]
    runner = _Runner(contracts, tools, task, monitors, enforce)
    ended = runner.block(program, dict(inputs))
    if ended is None:
        runner.broken.append("the program ends an iteration outside a loop")
        return Run(None, "the end of an iteration", tuple(runner.broken))
    return ended


#: What each check a proven program should never break holds, as `blame` names it.
_CHECKED = {
    "arguments": "an argument record",
    "policy": "a policy step",
    "goal": "the value returned",
}


def blame(result: Run, contracts: Mapping[str, Contract], *, proven: bool) -> str | None:
    """Return why a run broke, given whether its program was proven; None if nothing broke.

    A proven program breaks at run time only where something it rests on fails: a tool's result
    outside the output schema its server declared is the server's, one outside a schema shaped
    from observed calls is that assumption's, and an error a retry could not outlast is the
    fairness a retry assumes. An argument record, a policy step or a returned value breaking in a
    proven program is a check the verification proved: a defect of this package or of valgebra.
    An unproven program's break is its own link's.
    """
    if result.why is None:
        return None
    check, tool = result.why.check, result.why.tool
    if check == "result" and tool is not None:
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
