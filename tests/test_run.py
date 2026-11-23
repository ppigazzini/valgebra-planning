from __future__ import annotations

import asyncio
import json
import random
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from valgebra_planning._valgebra import Sampler
from valgebra_planning.contracts import Contract, Task, read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.planner import plan
from valgebra_planning.policy import read_policy
from valgebra_planning.program import Block, Call, Next, Ref, Return
from valgebra_planning.run import CanceledRunError, Refusal, ToolError, arun, blame, run
from valgebra_planning.verify import verify

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
TOOLS = {
    contract.name: contract
    for contract in map(read_contract, json.loads((EXAMPLE / "tools.json").read_text())["tools"])
}
TASKS = {
    spec["name"]: read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text())["tasks"]
}
RUNS = 40


def _random_tools(rng: random.Random) -> dict[str, Callable[..., Any]]:
    """Return tools answering with values drawn from their own output schemas."""

    def answering(contract: Contract) -> Callable[..., Any]:
        def tool(**_: Any) -> Any:
            if contract.returns is None:
                return None
            whole = contract.result()
            return rng.choice(Sampler(whole.root, rng).draw(whole.schema, 8))

        return tool

    return {name: answering(contract) for name, contract in TOOLS.items()}


def _inputs(task: Task, rng: random.Random) -> dict[str, Any]:
    drawn = {}
    for var, slots in task.held.items():
        whole = next(slot for slot in slots if slot.path == ())
        drawn[var] = rng.choice(Sampler(whole.root, rng).draw(whole.schema, 8))
    return drawn


def test_a_strong_program_breaks_no_contract_whatever_the_tools_return() -> None:
    task = TASKS["settle a parcel"]
    found = plan(TOOLS, task)
    assert found.strong
    assert found.program is not None
    rng = random.Random(0)
    for _ in range(RUNS):
        result = run(found.program, TOOLS, task, _inputs(task, rng), _random_tools(rng))
        assert result.broken == ()
        assert result.stopped is None
        assert task.goal.is_valid(result.value)


def test_a_weak_program_returns_or_stops_on_a_value_it_names() -> None:
    task = TASKS["return a parcel"]
    found = plan(TOOLS, task)
    assert found.program is not None
    named = {value for gap in found.uncovered for value in gap.examples}
    rng = random.Random(1)
    for _ in range(RUNS):
        result = run(found.program, TOOLS, task, _inputs(task, rng), _random_tools(rng))
        assert result.broken == ()
        if result.stopped is None:
            assert task.goal.is_valid(result.value)
        else:
            assert result.value in named


def test_the_naive_chain_is_refuted_and_breaks_a_contract_when_run() -> None:
    task = TASKS["return a parcel"]
    naive = Block(
        (
            Call("find_parcel", (("tracking", Ref("tracking")),), "c1"),
            Call(
                "book_return",
                (
                    ("parcel_id", Ref("c1", ("parcel", "parcel_id"))),
                    ("state", Ref("c1", ("parcel", "state"))),
                    ("weight_g", Ref("c1", ("parcel", "weight_g"))),
                ),
                "c2",
            ),
        ),
        Return(Ref("c2", ("label_id",))),
    )
    refuted = {link.claim for link in verify(naive, TOOLS, task).links if link.verdict == "refuted"}
    assert refuted == {
        "c1.parcel.state <= book_return.state",
        "c1.parcel.weight_g <= book_return.weight_g",
    }
    rng = random.Random(2)
    broken = [
        run(naive, TOOLS, task, _inputs(task, rng), _random_tools(rng)).broken for _ in range(RUNS)
    ]
    assert any(broken)


NAIVE = Block(
    (
        Call("find_parcel", (("tracking", Ref("tracking")),), "c1"),
        Call(
            "book_return",
            (
                ("parcel_id", Ref("c1", ("parcel", "parcel_id"))),
                ("state", Ref("c1", ("parcel", "state"))),
                ("weight_g", Ref("c1", ("parcel", "weight_g"))),
            ),
            "c2",
        ),
    ),
    Return(Ref("c2", ("label_id",))),
)
PARCEL = {
    "parcel_id": "pcl_1",
    "state": "lost",
    "weight_g": 500,
    "country": "IT",
    "contact": "a@b.c",
}


def _recording(answers: dict[str, Any], made: list[str]) -> dict[str, Callable[..., Any]]:
    """Return tools answering fixed values, each call recorded in ``made``."""

    def answering(name: str) -> Callable[..., Any]:
        def tool(**_: Any) -> Any:
            made.append(name)
            return answers[name]

        return tool

    return {name: answering(name) for name in answers}


def test_an_argument_record_outside_the_input_set_is_never_sent() -> None:
    made: list[str] = []
    tools = _recording(
        {"find_parcel": {"parcel": PARCEL}, "book_return": {"label_id": "lbl_1"}}, made
    )
    task = TASKS["return a parcel"]
    result = run(NAIVE, TOOLS, task, {"tracking": "AB123456789IT"}, tools)
    assert made == ["find_parcel"]
    assert result.value is None
    assert result.refused is not None
    assert result.refused.startswith("book_return refuses its arguments")
    assert result.broken == (result.refused,)


def test_a_result_outside_its_contract_is_never_bound() -> None:
    made: list[str] = []
    delivered = {**PARCEL, "state": "delivered"}
    tools = _recording(
        {"find_parcel": {"parcel": delivered}, "book_return": {"label_id": "not a label"}}, made
    )
    task = TASKS["return a parcel"]
    result = run(NAIVE, TOOLS, task, {"tracking": "AB123456789IT"}, tools)
    assert made == ["find_parcel", "book_return"]
    assert result.value is None
    assert result.refused is not None
    assert "outside its contract" in result.refused


def test_without_enforcement_every_break_is_recorded_and_the_run_goes_on() -> None:
    made: list[str] = []
    tools = _recording(
        {"find_parcel": {"parcel": PARCEL}, "book_return": {"label_id": "not a label"}}, made
    )
    task = TASKS["return a parcel"]
    result = run(NAIVE, TOOLS, task, {"tracking": "AB123456789IT"}, tools, enforce=False)
    assert made == ["find_parcel", "book_return"]
    assert result.refused is None
    assert result.value == "not a label"
    assert len(result.broken) == 3


def test_a_constant_is_passed_to_the_tool_as_written() -> None:
    made: list[str] = []
    seen: list[dict[str, Any]] = []
    delivered = {**PARCEL, "state": "delivered"}

    def book_return(**arguments: Any) -> Any:
        seen.append(arguments)
        return {"label_id": "lbl_2"}

    tools = _recording({"find_parcel": {"parcel": delivered}}, made) | {"book_return": book_return}
    program = read_program(
        "c1 = find_parcel(tracking=tracking)\n"
        'c2 = book_return(parcel_id=c1.parcel.parcel_id, state="delivered", weight_g=500)\n'
        "return c2.label_id\n"
    )
    result = run(program, TOOLS, TASKS["return a parcel"], {"tracking": "AB123456789IT"}, tools)
    assert result.value == "lbl_2"
    assert seen == [{"parcel_id": "pcl_1", "state": "delivered", "weight_g": 500}]


#: Text no schema below declares and no code spells: in a message, a part of the value refused.
SECRETS = st.text(min_size=1, max_size=40).map(lambda text: f"secret:{text}")

#: A tool taking an `id` of three letters and returning a closed record of a string `v`.
GET = read_contract(
    {
        "name": "get",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "pattern": "^[a-f]{3}$"}},
            "required": ["id"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"v": {"type": "string"}},
            "required": ["v"],
            "additionalProperties": False,
        },
    }
)


@given(secret=SECRETS)
@settings(max_examples=40, deadline=None)
def test_no_message_of_the_runner_holds_a_part_of_the_value_it_refused(secret: str) -> None:
    # An argument outside its pattern, a result holding a key its schema closes out, and a
    # returned value outside the goal: each refused, each named without the value.
    program = read_program("c1 = get(id=id)\nreturn c1.v\n")
    task = read_task("get one", {"id": {"type": "string"}}, {"enum": ["abc"]})
    runs = [
        run(program, {"get": GET}, task, {"id": secret}, {"get": lambda **_: {"v": "abc"}}),
        run(program, {"get": GET}, task, {"id": "abc"}, {"get": lambda **_: {"v": "a", secret: 1}}),
        run(program, {"get": GET}, task, {"id": "abc"}, {"get": lambda **_: {"v": secret}}),
    ]
    assert [ran.why.check for ran in runs if ran.why] == ["arguments", "result", "goal"]
    assert all(secret not in message for ran in runs for message in ran.broken), runs


#: A tool listing no output set, and one listing it.
LOOSE = {
    contract.name: contract
    for contract in map(
        read_contract,
        [
            {"name": "ask", "inputSchema": {"type": "object"}},
            {
                "name": "act",
                "inputSchema": {"type": "object"},
                "outputSchema": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            },
        ],
    )
}
ACT = read_task("act", {}, {"type": "string"})
ASK_THEN_ACT = read_program("c1 = ask()\nc2 = act()\nreturn c2.id\n")


@pytest.mark.parametrize("answer", [(1, 2), {"answer": (1,)}, float("nan"), object()])
def test_a_result_no_output_set_declares_is_held_to_the_json_values(answer: Any) -> None:
    made: list[str] = []
    tools = {"ask": lambda: answer, "act": lambda: made.append("act") or {"id": "x"}}
    ended = run(ASK_THEN_ACT, LOOSE, ACT, {}, tools)
    assert ended.refused is not None
    assert ended.refused.startswith("ask returned a value outside the JSON values (")
    assert made == []
    assert blame(ended, LOOSE, proven=True) == (
        "ask returned a value that is no JSON value, which no contract admits"
    )
    json_value = {"ask": lambda: {"answer": [1, "x", None]}, "act": lambda: {"id": "x"}}
    assert run(ASK_THEN_ACT, LOOSE, ACT, {}, json_value).value == "x"


def test_a_coroutine_handed_back_unawaited_is_refused_and_never_runs() -> None:
    ran: list[str] = []
    handed: list[Any] = []

    async def confirm() -> Any:
        ran.append("confirm")
        return {"confirmed": True}

    def ask() -> Any:
        handed.append(confirm())
        return handed[-1]

    made: list[str] = []
    tools = {"ask": ask, "act": lambda: made.append("act") or {"id": "x"}}
    ended = run(ASK_THEN_ACT, LOOSE, ACT, {}, tools)
    assert ended.refused is not None
    assert ended.refused.startswith("ask returned a value outside the JSON values (")
    assert (ran, made) == ([], [])
    assert handed[0].cr_frame is None


FOUND = {**PARCEL, "state": "delivered"}
GUARDED = read_program(
    "c1 = find_parcel(tracking=tracking)\n"
    "try:\n"
    "    c2 = book_return(parcel_id=c1.parcel.parcel_id, state=c1.parcel.state,"
    " weight_g=c1.parcel.weight_g)\n"
    "except ToolError:\n"
    "    c3 = open_claim(parcel_id=c1.parcel.parcel_id)\n"
    "    return c3.claim_id\n"
    "return c2.label_id\n"
)
INPUTS = {"tracking": "AB123456789IT"}


def test_an_exception_a_tool_raises_ends_the_run_named_and_no_error_block_runs() -> None:
    made: list[str] = []

    def book_return(**_: Any) -> Any:
        made.append("book_return")
        message = "over-current at 41.7 A, joint secret-7"
        raise RuntimeError(message)

    tools = _recording(
        {"find_parcel": {"parcel": FOUND}, "open_claim": {"claim_id": "clm_1"}}, made
    )
    ended = run(
        GUARDED, TOOLS, TASKS["return a parcel"], INPUTS, {**tools, "book_return": book_return}
    )
    assert ended.refused == "book_return raised RuntimeError"
    assert ended.why == Refusal("raised", "book_return")
    assert made == ["find_parcel", "book_return"]
    assert "secret" not in repr(ended)
    assert blame(ended, TOOLS, proven=True) == (
        "book_return raised an exception, which no contract admits: the tool's"
    )


def test_a_retried_call_raising_an_exception_is_attempted_once() -> None:
    made: list[str] = []

    def find_parcel(**_: Any) -> Any:
        made.append("find_parcel")
        raise ValueError

    retried = read_program(
        "c1 = retry(find_parcel(tracking=tracking))\nreturn c1.parcel.parcel_id\n"
    )
    ended = run(retried, TOOLS, TASKS["return a parcel"], INPUTS, {"find_parcel": find_parcel})
    assert ended.refused == "find_parcel raised ValueError"
    assert made == ["find_parcel"]


def test_an_interrupt_a_tool_raises_propagates() -> None:
    def find_parcel(**_: Any) -> Any:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(GUARDED, TOOLS, TASKS["return a parcel"], INPUTS, {"find_parcel": find_parcel})


def test_a_call_to_a_tool_the_run_was_not_given_ends_the_run_there() -> None:
    made: list[str] = []
    tools = _recording({"find_parcel": {"parcel": FOUND}}, made)
    ended = run(GUARDED, TOOLS, TASKS["return a parcel"], INPUTS, tools)
    assert ended.refused == "book_return is no tool the run was given"
    assert ended.why == Refusal("given", "book_return")
    assert made == ["find_parcel"]
    assert (
        blame(ended, TOOLS, proven=True) == "no tool book_return was given to the run: the caller's"
    )


@pytest.mark.parametrize(
    ("inputs", "refused"),
    [
        (
            {"tracking": "nope"},
            "the input tracking lies outside the task's (string_pattern_mismatch)",
        ),
        ({}, "the input tracking the task names is not given"),
    ],
)
def test_an_input_outside_the_task_ends_the_run_before_any_call(
    inputs: dict[str, Any], refused: str
) -> None:
    made: list[str] = []
    tools = _recording({"find_parcel": {"parcel": FOUND}}, made)
    ended = run(GUARDED, TOOLS, TASKS["return a parcel"], inputs, tools)
    assert ended.refused == refused
    assert ended.why == Refusal("inputs")
    assert made == []
    assert blame(ended, TOOLS, proven=True) == (
        "an input outside the task's input set: the caller's, before any call"
    )


def _ending(answers: dict[str, Any], program: Any = GUARDED, **options: Any) -> Any:
    tools = _recording(answers, [])
    return run(program, TOOLS, TASKS["return a parcel"], INPUTS, tools, **options)


def test_how_a_run_ends_is_its_outcome_and_only_a_clean_return_is_ok() -> None:
    booked = {"find_parcel": {"parcel": FOUND}, "book_return": {"label_id": "lbl_1"}}
    returned = _ending(booked)
    assert (returned.outcome, returned.ok, returned.value) == ("returned", True, "lbl_1")

    stopping = read_program(
        'c1 = find_parcel(tracking=tracking)\nif c1.parcel.state in ("delivered",):\n'
        "    return c1.parcel.parcel_id\nstop(c1.parcel.state)\n"
    )
    stopped = _ending({"find_parcel": {"parcel": PARCEL}}, stopping)
    # A stop holds the value it stopped on: a caller reading `value` alone reads a stop as a return.
    assert (stopped.outcome, stopped.ok, stopped.value) == ("stopped", False, "lost")

    refused = _ending({"find_parcel": {"parcel": FOUND}, "book_return": {"label_id": "no"}})
    assert (refused.outcome, refused.ok, refused.value) == ("refused", False, None)

    def unanswered(**_: Any) -> Any:
        raise ToolError

    errored = run(
        read_program("c1 = find_parcel(tracking=tracking)\nreturn c1.parcel.parcel_id\n"),
        TOOLS,
        TASKS["return a parcel"],
        INPUTS,
        {"find_parcel": unanswered},
    )
    assert (errored.outcome, errored.ok, errored.value) == ("error", False, None)

    broken = _ending(
        {"find_parcel": {"parcel": FOUND}, "book_return": {"label_id": "no"}}, enforce=False
    )
    assert (broken.outcome, broken.ok) == ("returned", False)
    assert broken.broken

    looping = run(Block((), Next()), TOOLS, TASKS["return a parcel"], INPUTS, {})
    assert (looping.outcome, looping.ok) == ("ended", False)
    assert blame(looping, TOOLS, proven=True) == (
        "the program ends an iteration outside a loop: the program's"
    )


def _async(tools: dict[str, Callable[..., Any]]) -> dict[str, Callable[..., Any]]:
    """Return the same tools as coroutine functions, each awaiting nothing but its answer."""

    def awaited(tool: Callable[..., Any]) -> Callable[..., Any]:
        async def answer(**arguments: Any) -> Any:
            return tool(**arguments)

        return answer

    return {name: awaited(tool) for name, tool in tools.items()}


@pytest.mark.parametrize("name", ["settle a parcel", "return a parcel"])
def test_arun_makes_the_checks_of_run_in_run_s_order(name: str) -> None:
    task = TASKS[name]
    found = plan(TOOLS, task)
    assert found.program is not None
    for seed in range(RUNS):
        inputs = _inputs(task, random.Random(f"inputs:{seed}"))
        sync = run(found.program, TOOLS, task, inputs, _random_tools(random.Random(seed)))
        tools = _async(_random_tools(random.Random(seed)))
        assert asyncio.run(arun(found.program, TOOLS, task, inputs, tools)) == sync


def test_arun_refuses_what_run_refuses_and_hands_an_error_to_its_block() -> None:
    task = TASKS["settle a parcel"]
    program = read_program(
        "c1 = find_parcel(tracking=tracking)\n"
        "try:\n"
        "    c2 = notify(email=c1.parcel.contact)\n"
        "except ToolError:\n"
        "    stop(c1.parcel)\n"
        "return c2.notice_id\n"
    )
    inputs = {"tracking": "AB123456789IT"}

    def failing(**_: Any) -> Any:
        message = "notify"
        raise ToolError(message)

    tools: dict[str, Callable[..., Any]] = {
        "find_parcel": lambda **_: {"parcel": PARCEL},
        "notify": failing,
    }
    sync = run(program, TOOLS, task, inputs, tools)
    assert sync.outcome == "stopped"
    assert asyncio.run(arun(program, TOOLS, task, inputs, _async(tools))) == sync


OWED = read_policy(
    {
        "name": "every notice is answered",
        "initial": "clear",
        "bad": [],
        "pending": ["owed"],
        "transitions": [{"from": "clear", "tool": "find_parcel", "to": "owed"}],
    }
)


def test_a_cancel_ends_the_run_naming_the_obligations_it_leaves() -> None:
    task = TASKS["settle a parcel"]
    program = read_program(
        "c1 = find_parcel(tracking=tracking)\n"
        "c2 = notify(email=c1.parcel.contact)\n"
        "return c2.notice_id\n"
    )

    async def find_parcel(**_: Any) -> Any:
        return {"parcel": PARCEL}

    async def canceled() -> asyncio.CancelledError:
        asked = asyncio.Event()

        async def notify(**_: Any) -> Any:
            asked.set()
            await asyncio.sleep(3600)

        running = asyncio.create_task(
            arun(
                program,
                TOOLS,
                task,
                {"tracking": "AB123456789IT"},
                {"find_parcel": find_parcel, "notify": notify},
                policies=[OWED],
            )
        )
        await asked.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError) as stopped:
            await running
        return stopped.value

    cancel = asyncio.run(canceled())
    left = "canceled at notify: every notice is answered may hold owed"
    if sys.version_info < (3, 13):
        # The timeouts of 3.11 and 3.12 read no subclass as their cancel: the cancel is raised.
        assert type(cancel) is asyncio.CancelledError
        assert cancel.__notes__ == [left]
        return
    assert isinstance(cancel, CanceledRunError)
    assert cancel.run.outcome == "canceled"
    assert cancel.run.stopped == left
    assert blame(cancel.run, TOOLS, proven=True) == "the run was canceled at notify: the caller's"


@pytest.mark.parametrize("bound", ["timeout", "wait_for"])
def test_a_timeout_reads_a_canceled_run_as_the_cancel_it_is(bound: str) -> None:
    task = TASKS["settle a parcel"]
    program = read_program("c1 = find_parcel(tracking=tracking)\nreturn c1.parcel.parcel_id\n")

    async def find_parcel(**_: Any) -> Any:
        await asyncio.sleep(3600)

    async def bounded() -> None:
        running = arun(
            program, TOOLS, task, {"tracking": "AB123456789IT"}, {"find_parcel": find_parcel}
        )
        if bound == "wait_for":
            await asyncio.wait_for(running, 0.01)
            return
        async with asyncio.timeout(0.01):
            await running

    with pytest.raises(TimeoutError):
        asyncio.run(bounded())
