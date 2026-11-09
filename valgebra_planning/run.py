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
from valgebra_planning.program import Block, Branch, Call, Const, Ref, Return, Stop

#: How many times a retried call is made before the run gives up on it.
ATTEMPTS = 8


class ToolError(Exception):
    """A tool answered with an error: an MCP result with ``isError`` set."""


@dataclass(frozen=True, slots=True)
class Run:
    """What a run returned or where it stopped, and every contract it broke on the way."""

    value: Any
    stopped: str | None
    broken: tuple[str, ...]
    #: The broken check that ended the run before it went on, when contracts are enforced.
    refused: str | None = None


@dataclass(slots=True)
class _Runner:
    contracts: Mapping[str, Contract]
    tools: Mapping[str, Callable[..., Any]]
    task: Task
    monitors: list[Monitor]
    enforce: bool
    broken: list[str] = field(default_factory=list)

    def breaks(self, check: str) -> Run | None:
        """Record a broken check; return the run it ends when contracts are enforced."""
        self.broken.append(check)
        if self.enforce:
            return Run(None, None, tuple(self.broken), refused=check)
        return None

    def block(self, block: Block, env: dict[str, Any]) -> Run:
        for call in block.calls:
            outcome = self.call(call, env)
            if isinstance(outcome, Run):
                return outcome
            env = outcome
        end = block.end
        if isinstance(end, Return):
            value = _read(env, end.ref)
            if not self.task.goal.is_valid(value):
                refused = self.breaks(f"returned {value!r:.120}, outside the goal")
                if refused is not None:
                    return refused
            return Run(value, None, tuple(self.broken))
        if isinstance(end, Stop):
            return Run(_read(env, end.ref), str(end.ref), tuple(self.broken))
        return self.branch(end, env)

    def call(self, call: Call, env: dict[str, Any]) -> dict[str, Any] | Run:
        """Make one call; return the values held after it, or the run it ended."""
        contract = self.contracts[call.tool]
        arguments = {
            name: ref.value if isinstance(ref, Const) else _read(env, ref)
            for name, ref in call.bindings
        }
        refused = self.admitted(call.tool, contract, arguments)
        if refused is not None:
            return refused
        made = self.attempts(call, arguments, env)
        if isinstance(made, Run):
            return made
        result = made[0]
        if contract.returns is not None and not contract.returns.is_valid(result):
            refused = self.breaks(f"{call.tool} returned {result!r:.120}, outside its contract")
            if refused is not None:
                return refused
        return {**env, call.result: result}

    def admitted(self, tool: str, contract: Contract, arguments: dict[str, Any]) -> Run | None:
        """Check a call before it is made: its argument record, then each policy's step."""
        if not contract.accepts.is_valid(arguments):
            refused = self.breaks(f"{tool} refuses its arguments {arguments!r:.120}")
            if refused is not None:
                return refused
        for monitor in self.monitors:
            if monitor.observe(tool, arguments):
                refused = self.breaks(monitor.violations[-1])
                if refused is not None:
                    return refused
        return None

    def attempts(
        self, call: Call, arguments: dict[str, Any], env: dict[str, Any]
    ) -> tuple[Any] | Run:
        """Make the call, retried as it says; return its result, or the run its error ended."""
        for _ in range(ATTEMPTS if call.retry else 1):
            try:
                return (self.tools[call.tool](**arguments),)
            except ToolError:
                continue
        if call.on_error is not None:
            return self.block(call.on_error, env)
        self.broken.append(f"{call.tool} answered with an error nothing handles")
        return Run(None, f"an error of {call.tool}", tuple(self.broken))

    def branch(self, branch: Branch, env: dict[str, Any]) -> Run:
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
    return _Runner(contracts, tools, task, monitors, enforce).block(program, dict(inputs))
