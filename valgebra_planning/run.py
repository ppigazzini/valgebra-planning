"""A program run on concrete values, each argument and result checked against its contract.

The runner calls the tools a program names with the values the program binds, takes each
branch by testing the value it reads, and checks every argument record against the tool's
input set, every result against its output set and the returned value against the goal. A
broken contract is recorded, never raised, so one run reports every break it meets.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from valgebra_planning.contracts import Contract, Task, listed
from valgebra_planning.program import Block, Branch, Ref, Return, Stop


@dataclass(frozen=True, slots=True)
class Run:
    """What a run returned or where it stopped, and every contract it broke on the way."""

    value: Any
    stopped: str | None
    broken: tuple[str, ...]


@dataclass(slots=True)
class _Runner:
    contracts: Mapping[str, Contract]
    tools: Mapping[str, Callable[..., Any]]
    task: Task
    broken: list[str] = field(default_factory=list)

    def block(self, block: Block, env: dict[str, Any]) -> Run:
        for call in block.calls:
            contract = self.contracts[call.tool]
            arguments = {name: _read(env, ref) for name, ref in call.bindings}
            if not contract.accepts.is_valid(arguments):
                self.broken.append(f"{call.tool} refuses its arguments {arguments!r:.120}")
            result = self.tools[call.tool](**arguments)
            if contract.returns is not None and not contract.returns.is_valid(result):
                self.broken.append(f"{call.tool} returned {result!r:.120}, outside its contract")
            env = {**env, call.result: result}
        end = block.end
        if isinstance(end, Return):
            value = _read(env, end.ref)
            if not self.task.goal.is_valid(value):
                self.broken.append(f"returned {value!r:.120}, outside the goal")
            return Run(value, None, tuple(self.broken))
        if isinstance(end, Stop):
            return Run(_read(env, end.ref), str(end.ref), tuple(self.broken))
        return self.branch(end, env)

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
) -> Run:
    """Run ``program`` on ``inputs``, calling ``tools`` by name, checking every contract."""
    return _Runner(contracts, tools, task).block(program, dict(inputs))
