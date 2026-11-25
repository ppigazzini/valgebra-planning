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
from valgebra_planning.flows import read_flow
from valgebra_planning.library import read_library
from valgebra_planning.planner import plan
from valgebra_planning.policy import read_policy
from valgebra_planning.program import Block, Call, Next, Ref, Return
from valgebra_planning.run import (
    CanceledRunError,
    Refusal,
    Run,
    RunRules,
    ToolError,
    UnansweredError,
    arun,
    blame,
    run,
)
from valgebra_planning.verify import verify

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
TOOLS = {
    contract.name: contract
    for contract in map(
        read_contract, json.loads((EXAMPLE / "tools.json").read_text(encoding="utf-8"))["tools"]
    )
}
TASKS = {
    spec["name"]: read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
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


RELEASE = read_policy(
    {
        "name": "release what you grasp",
        "initial": "free",
        "bad": [],
        "pending": ["holding"],
        "transitions": [
            {"from": "free", "tool": "grasp", "result": {"required": ["grasp_id"]}, "to": "holding"}
        ],
    }
)


def test_a_cancel_holds_every_state_the_call_may_have_reached() -> None:
    # A grasp canceled in flight may have grasped and answered: the state its result reaches
    # is one the run may be in, an obligation a cancel read as an error would leave unnamed.
    problem = ROBOT.problem("pick")
    program = read_program("c1 = grasp(object_id=o, distance_m=d)\nreturn c1.grasp_id\n")

    async def canceled() -> asyncio.CancelledError:
        asked = asyncio.Event()

        async def grasp(**_: Any) -> Any:
            asked.set()
            await asyncio.sleep(3600)

        running = asyncio.create_task(
            arun(program, ROBOT.tools, problem.task, GIVEN, {"grasp": grasp}, policies=[RELEASE])
        )
        await asked.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError) as stopped:
            await running
        return stopped.value

    cancel = asyncio.run(canceled())
    left = "canceled at grasp: release what you grasp may hold holding"
    if sys.version_info < (3, 13):
        assert cancel.__notes__ == [left]
        return
    assert isinstance(cancel, CanceledRunError)
    assert cancel.run.stopped == left


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


ROBOT = read_library(Path(__file__).parent.parent / "examples" / "robot-skills")
GIVEN = {"o": "obj_1", "p": "obj_2", "a": "mug", "b": "bottle", "d": 0.4}
#: The robot's answers: a pose read per object, a detection per label, a scan near an object.
POSES = {"obj_1": 0.25, "obj_2": 0.5}
SEEN = {"mug": "obj_1", "bottle": "obj_2"}


def _robot(fails: dict[str, int] | None = None, *, found: bool = True) -> dict[str, Any]:
    """Return the robot's tools; ``fails`` names the call of a tool, counted from 1, that errs."""
    count: dict[str, int] = {}

    def answer(name: str, arguments: dict[str, Any]) -> Any:
        count[name] = count.get(name, 0) + 1
        if (fails or {}).get(name) == count[name]:
            message = "lost track"
            raise ToolError(message)
        target = arguments.get("object_id", "")
        return {
            "detect": lambda: {"object_id": SEEN[arguments["label"]], "found": found},
            "estimate_pose": lambda: {"distance_m": POSES[target]},
            "walk_to": lambda: {"object_id": target, "arrived": True},
            "grasp": lambda: {"grasp_id": "g_1"},
            "scan": lambda: {"items": [{"object_id": target, "distance_m": POSES[target]}]},
        }[name]()

    return {name: (lambda name=name, **a: answer(name, a)) for name in ROBOT.tools}


def _held(source: str, task: str = "pick, measured", **options: Any) -> Any:
    """Run a program on the robot under a task's flow rules."""
    problem = ROBOT.problem(task)
    tools = _robot(options.pop("fails", None), found=options.pop("found", True))
    flows = options.pop("flows", problem.flows)
    return run(
        read_program(source), ROBOT.tools, problem.task, GIVEN, tools, flows=flows, **options
    )


#: The grasps the verifier's names admit and the values refuse, each with the run's reason.
_STALE_AND_ELSEWHERE = [
    (
        "an error block grasps at the reading made before the walk",
        (
            "c1 = estimate_pose(object_id=o)\nc2 = walk_to(object_id=o)\ntry:\n"
            "    c1 = estimate_pose(object_id=o)\nexcept ToolError:\n"
            "    c3 = grasp(object_id=o, distance_m=c1.distance_m)\n    return c3.grasp_id\n"
            "c4 = grasp(object_id=o, distance_m=c1.distance_m)\nreturn c4.grasp_id\n"
        ),
        {"fails": {"estimate_pose": 2}},
        "c1.distance_m from estimate_pose was returned before walk_to, which may act",
    ),
    (
        "an error block grasps one object at another's reading",
        (
            "c1 = estimate_pose(object_id=o)\ntry:\n    c1 = estimate_pose(object_id=p)\n"
            "except ToolError:\n    c3 = grasp(object_id=p, distance_m=c1.distance_m)\n"
            "    return c3.grasp_id\nc4 = grasp(object_id=p, distance_m=c1.distance_m)\n"
            "return c4.grasp_id\n"
        ),
        {"fails": {"estimate_pose": 2}},
        "c1.distance_m from estimate_pose was returned for another object_id than grasp takes",
    ),
    (
        "a grasp reads a detection the program has since replaced",
        (
            "c1 = detect(label=a)\nc2 = estimate_pose(object_id=c1.object_id)\n"
            "c1 = detect(label=b)\n"
            "c3 = grasp(object_id=c1.object_id, distance_m=c2.distance_m)\nreturn c3.grasp_id\n"
        ),
        {},
        "c2.distance_m from estimate_pose was returned for another object_id than grasp takes",
    ),
    (
        "a branch rebinds the reading the other side grasps at",
        (
            "c1 = estimate_pose(object_id=o)\nc2 = detect(label=a)\nif c2.found in (True,):\n"
            "    c1 = estimate_pose(object_id=p)\n"
            "    c3 = grasp(object_id=p, distance_m=c1.distance_m)\n"
            "    return c3.grasp_id\nc4 = grasp(object_id=p, distance_m=c1.distance_m)\n"
            "return c4.grasp_id\n"
        ),
        {"found": False},
        "c1.distance_m from estimate_pose was returned for another object_id than grasp takes",
    ),
    (
        "a loop rebinds the reading the grasp after it reads",
        (
            "c2 = estimate_pose(object_id=p)\nc5 = scan(object_id=o)\nfor it in c5.items:\n"
            "    c2 = estimate_pose(object_id=o)\n"
            "c3 = grasp(object_id=o, distance_m=c2.distance_m)\n"
            "return c3.grasp_id\n"
        ),
        {},
        "c2.distance_m from estimate_pose was returned for another object_id than grasp takes",
    ),
]


@pytest.mark.parametrize(
    ("source", "options", "reason"),
    [case[1:] for case in _STALE_AND_ELSEWHERE],
    ids=[case[0] for case in _STALE_AND_ELSEWHERE],
)
def test_a_run_refuses_the_grasp_a_flow_rule_refuses(
    source: str, options: dict[str, Any], reason: str
) -> None:
    """A run binds each name to the call that returned its value, on its own path.

    So it reads a fresh and keyed rule on the values: a reading made before a call that may act,
    or for another object than the grasp's, reaches the grasp on no path, the grasp is never made,
    and the run names the rule.
    """
    ran = _held(source, **options)
    assert ran.outcome == "refused"
    assert ran.why == Refusal("flow", "grasp")
    assert ran.refused == f"flow measured: {reason}"


def test_a_run_admits_a_value_from_a_source_fresh_and_keyed() -> None:
    # The controls: the branch's other side, the error block not entered, a reading made last.
    sides = (
        "c1 = estimate_pose(object_id=o)\nc2 = detect(label=a)\nif c2.found in (True,):\n"
        "    c1 = estimate_pose(object_id=p)\n"
        "    c3 = grasp(object_id=p, distance_m=c1.distance_m)\n"
        "    return c3.grasp_id\nc4 = grasp(object_id=p, distance_m=c1.distance_m)\n"
        "return c4.grasp_id\n"
    )
    assert _held(sides).value == "g_1"
    assert _held(_STALE_AND_ELSEWHERE[0][1]).value == "g_1"
    walked = (
        "c1 = walk_to(object_id=o)\nc2 = estimate_pose(object_id=c1.object_id)\n"
        "c3 = grasp(object_id=o, distance_m=c2.distance_m)\nreturn c3.grasp_id\n"
    )
    assert _held(walked).value == "g_1"


def test_a_source_that_acts_answers_with_a_fresh_value() -> None:
    # A call that may act reports the world after its own act, as the proxy keeps its answer.
    arrived = read_flow(
        {
            "name": "arrived",
            "sources": ["walk_to"],
            "sink": "grasp.object_id",
            "only": True,
            "fresh": True,
        }
    )
    walked = "c1 = walk_to(object_id=o)\nc2 = grasp(object_id=c1.object_id, distance_m=d)\n"
    assert _held(walked + "return c2.grasp_id\n", flows=[arrived]).value == "g_1"
    again = walked.replace("c2 = grasp", "c3 = walk_to(object_id=p)\nc2 = grasp")
    assert _held(again + "return c2.grasp_id\n", flows=[arrived]).refused == (
        "flow arrived: c1.object_id from walk_to was returned before walk_to, which may act"
    )


def test_a_scanned_item_is_keyed_by_the_scan_that_returned_it() -> None:
    near = (
        "c1 = scan(object_id={})\nfor it in c1.items:\n"
        "    c2 = grasp(object_id=o, distance_m=it.distance_m)\nstop(c1)\n"
    )
    assert _held(near.format("o"), "pick, scanned").outcome == "stopped"
    other = _held(near.format("p"), "pick, scanned")
    assert other.refused == (
        "flow scanned: it.distance_m from scan was returned for another object_id than grasp takes"
    )


def test_a_rule_holds_every_origin_it_names_and_admits_its_endorsed_set() -> None:
    plain = "c1 = grasp(object_id=o, distance_m={})\nreturn c1.grasp_id\n"
    measured = ROBOT.flows["measured"]
    assert _held(plain.format("d")).refused == "flow measured: d from d reaches grasp.distance_m"
    assert _held(plain.format("0.4")).refused == (
        "flow measured: the literal 0.4 reaches grasp.distance_m"
    )
    endorsed = read_flow(
        {
            "name": "near",
            "sources": ["estimate_pose"],
            "sink": "grasp.distance_m",
            "only": True,
            "endorsed": {"maximum": 0.5},
        }
    )
    assert _held(plain.format("0.4"), flows=[endorsed]).value == "g_1"
    assert _held(plain.format("0.6"), flows=[endorsed]).outcome == "refused"
    # A rule naming its sources alone refuses their values, and admits every other.
    untrusted = read_flow(
        {"name": "untrusted", "sources": ["estimate_pose"], "sink": measured.sink}
    )
    read = (
        "c1 = estimate_pose(object_id=o)\n"
        "c2 = grasp(object_id=o, distance_m=c1.distance_m)\nreturn c2.grasp_id\n"
    )
    assert _held(read, flows=[untrusted]).refused == (
        "flow untrusted: c1.distance_m from estimate_pose reaches grasp.distance_m"
    )
    assert _held(plain.format("d"), flows=[untrusted]).value == "g_1"


def test_an_implicit_rule_refuses_a_sink_under_a_test_on_its_source() -> None:
    implicit = read_flow(
        {"name": "steered", "sources": ["detect"], "sink": "grasp.object_id", "implicit": True}
    )
    tested = (
        "c1 = detect(label=a)\nif c1.found in (True,):\n    c2 = grasp(object_id=o, distance_m=d)\n"
        "    return c2.grasp_id\nstop(c1)\n"
    )
    assert (
        _held(tested, flows=[implicit]).refused == "flow steered: grasp runs under a test on detect"
    )
    # What follows a loop runs under the test on its array, as the verifier reads it.
    looped = (
        "c1 = scan(object_id=o)\nfor it in c1.items:\n    c2 = detect(label=a)\n"
        "c3 = grasp(object_id=o, distance_m=d)\nreturn c3.grasp_id\n"
    )
    scanned = read_flow(
        {"name": "steered", "sources": ["scan"], "sink": "grasp.object_id", "implicit": True}
    )
    assert _held(looped, flows=[scanned]).refused == "flow steered: grasp runs under a test on scan"
    assert _held(looped, flows=[implicit]).value == "g_1"


def test_an_awaited_run_holds_the_same_rules() -> None:
    problem = ROBOT.problem("pick, measured")
    source, reason = _STALE_AND_ELSEWHERE[0][1], _STALE_AND_ELSEWHERE[0][3]
    tools = _robot({"estimate_pose": 2})
    ran = asyncio.run(
        arun(read_program(source), ROBOT.tools, problem.task, GIVEN, tools, flows=problem.flows)
    )
    assert ran.refused == f"flow measured: {reason}"


def test_a_run_not_enforced_records_each_broken_rule_and_goes_on() -> None:
    ran = _held(_STALE_AND_ELSEWHERE[0][1], fails={"estimate_pose": 2}, enforce=False)
    assert ran.outcome == "returned"
    assert ran.broken == (f"flow measured: {_STALE_AND_ELSEWHERE[0][3]}",)


def test_a_broken_flow_rule_is_blamed_on_the_proof_it_breaks() -> None:
    ran = _held(_STALE_AND_ELSEWHERE[2][1])
    assert blame(ran, ROBOT.tools, proven=True) == (
        "a flow rule the verification proved broke: a defect to report"
    )
    assert (
        blame(ran, ROBOT.tools, proven=False) == "a flow rule no link of the program proves broke"
    )


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (ToolError("dropped"), "stopped"),
        (UnansweredError("grasp did not answer: timed out"), "refused"),
    ],
    ids=["answered", "unanswered"],
)
def test_a_call_no_result_answered_may_have_reached_any_state(
    error: ToolError, outcome: str
) -> None:
    """A grasp that timed out may have grasped and answered, its result lost.

    An error the tool answered with is no result, so no transition reading one is taken; a call
    no result answered holds every state it may have reached, and the stop it falls into leaves
    `holding` owed, which ends the run.
    """
    problem = ROBOT.problem("pick")
    program = read_program(
        "try:\n    c1 = grasp(object_id=o, distance_m=d)\nexcept ToolError:\n    stop(o)\n"
        "return c1.grasp_id\n"
    )

    def grasp(**_: Any) -> Any:
        raise error

    ran = run(program, ROBOT.tools, problem.task, GIVEN, {"grasp": grasp}, policies=[RELEASE])
    assert ran.outcome == outcome
    if outcome == "refused":
        assert ran.why == Refusal("policy")
        assert "holding" in (ran.refused or "")


def test_a_call_no_result_answered_into_a_bad_state_ends_the_run() -> None:
    dropped = read_policy(
        {
            "name": "never drop",
            "initial": "free",
            "bad": ["dropped"],
            "transitions": [
                {
                    "from": "free",
                    "tool": "grasp",
                    "result": {"required": ["grasp_id"]},
                    "to": "dropped",
                }
            ],
        }
    )
    program = read_program(
        "try:\n    c1 = grasp(object_id=o, distance_m=d)\nexcept ToolError:\n    stop(o)\n"
        "return c1.grasp_id\n"
    )

    def grasp(**_: Any) -> Any:
        message = "grasp did not answer"
        raise UnansweredError(message)

    task = ROBOT.problem("pick").task
    ran = run(program, ROBOT.tools, task, GIVEN, {"grasp": grasp}, policies=[dropped])
    assert ran.why == Refusal("policy", "grasp")
    assert ran.refused == "never drop: grasp may have returned into dropped"


SPSA = read_contract(
    {
        "name": "get_spsa",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"iter": {"type": "integer"}},
            "required": ["iter"],
        },
    }
)
TUNE = read_task("tune", {"run_id": {"type": "string"}}, {"type": "integer"})


@pytest.mark.parametrize(
    ("raised", "texts", "kept"),
    [
        (ToolError("run 0 is no tune"), {"get_spsa": {"enum": ["run 0 is no tune"]}}, True),
        (ToolError("run 0 is no tune"), None, False),
        (ToolError("token sk-123 expired"), {"get_spsa": {"enum": ["run 0 is no tune"]}}, False),
        (UnansweredError("run 0 is no tune"), {"get_spsa": {"enum": ["run 0 is no tune"]}}, False),
    ],
    ids=["listed", "no-set", "outside", "unanswered"],
)
def test_a_run_keeps_an_error_s_text_inside_its_set(
    raised: ToolError, texts: dict[str, Any] | None, *, kept: bool
) -> None:
    """An error no block handles ends the run with its text where the library lists it.

    No contract bounds what a server writes in an error, so the text is kept as a proxy passes
    one on: only inside the set ``"errorTexts"`` names for the tool, and never for a call no
    result answered, whose message is the client's.
    """
    program = read_program("c1 = get_spsa(run_id=run_id)\nreturn c1.iter\n")

    def get_spsa(**_: Any) -> Any:
        raise raised

    ran = run(
        program,
        {"get_spsa": SPSA},
        TUNE,
        {"run_id": "0"},
        {"get_spsa": get_spsa},
        error_texts=texts,
    )
    assert ran.outcome == "error"
    assert ran.error == ("run 0 is no tune" if kept else None)


def test_a_run_s_flow_refusal_carries_the_rule_s_reason() -> None:
    reasoned = read_flow(
        {
            "name": "measured",
            "sources": ["estimate_pose"],
            "sink": "grasp.distance_m",
            "only": True,
            "reason": "grasp only at a distance the pose measured",
        }
    )
    ran = _held("c1 = grasp(object_id=o, distance_m=d)\nreturn c1.grasp_id\n", flows=[reasoned])
    assert ran.refused == (
        "flow measured: d from d reaches grasp.distance_m: grasp only at a distance the pose "
        "measured"
    )


#: A robot that asks before it walks, and a yes held for 120 seconds.
WALKING = {
    contract.name: contract
    for contract in map(
        read_contract,
        [
            {
                "name": name,
                "inputSchema": {"type": "object"},
                "outputSchema": {
                    "type": "object",
                    "properties": {"status": {"enum": answers}},
                    "required": ["status"],
                },
            }
            for name, answers in (("confirm", ["yes"]), ("look", ["ok"]), ("walk", ["walked"]))
        ],
    )
}
CONSENT: dict[str, Any] = {
    "name": "consent",
    "initial": "unasked",
    "bad": ["violated"],
    "transitions": [
        {
            "from": "unasked",
            "tool": "confirm",
            "result": {"properties": {"status": {"const": "yes"}}},
            "to": "yes",
        },
        {"from": "unasked", "tool": "walk", "to": "violated"},
        {"from": "yes", "after_s": 120, "to": "unasked"},
    ],
}
ASKED = "c1 = confirm()\nc2 = look()\nc3 = walk()\nreturn c3\n"
ANYTHING = read_task("t", {}, True)


class _Waiting:
    """Tools whose `look` takes ``seconds`` by a clock of their own."""

    def __init__(self, seconds: float) -> None:
        self.now, self.seconds = 0.0, seconds
        self.tools = {"confirm": self.confirm, "look": self.look, "walk": self.walk}

    def clock(self) -> float:
        return self.now

    def confirm(self) -> dict[str, str]:
        return {"status": "yes"}

    def look(self) -> dict[str, str]:
        self.now += self.seconds
        return {"status": "ok"}

    def walk(self) -> dict[str, str]:
        return {"status": "walked"}


def _awaited(*given: Any, **rules: Any) -> Run:
    return asyncio.run(arun(*given, **rules))


@pytest.mark.parametrize("runner", [run, _awaited])
def test_a_walk_after_the_yes_lapsed_is_refused_by_the_policy(runner: Callable[..., Run]) -> None:
    policy, program = read_policy(CONSENT), read_program(ASKED)
    assert verify(program, WALKING, ANYTHING, policies=[policy]).verified

    def ran(seconds: float) -> Run:
        waiting = _Waiting(seconds)
        rules: RunRules = {"policies": [policy], "clock": waiting.clock}
        return runner(program, WALKING, ANYTHING, {}, waiting.tools, **rules)

    assert ran(119).ok
    late = ran(120)
    assert late.refused == "consent: walk reached violated"
    assert late.why == Refusal("policy", "walk", lapsed=True)
    assert blame(late, WALKING, proven=True) == (
        "the run reached the call to walk after the lapse its proof assumed: the caller's timing"
    )


def test_a_policy_its_own_calls_break_is_no_lapse_s() -> None:
    program = read_program("c1 = walk()\nreturn c1\n")
    waiting = _Waiting(0)
    ran = run(
        program,
        WALKING,
        ANYTHING,
        {},
        waiting.tools,
        policies=[read_policy(CONSENT)],
        clock=waiting.clock,
    )
    assert ran.why == Refusal("policy", "walk")
    assert blame(ran, WALKING, proven=False) == "a policy no link of the program proves broke"


def test_a_policy_broken_from_either_state_an_error_leaves_is_no_lapse_s() -> None:
    # The confirmation may not have acted, so the walk breaks the policy with no lapse read.
    told = {
        **CONSENT,
        "transitions": [
            {"from": "unasked", "tool": "confirm", "to": "yes"},
            *CONSENT["transitions"][1:],
        ],
    }

    def failing() -> Any:
        raise ToolError

    ran = run(
        read_program(
            "try:\n    c1 = confirm()\nexcept ToolError:\n    c2 = walk()\n    return c2\n"
            "return c1\n"
        ),
        WALKING,
        ANYTHING,
        {},
        _Waiting(0).tools | {"confirm": failing},
        policies=[read_policy(told)],
        clock=lambda: 0.0,
    )
    assert ran.why == Refusal("policy", "walk")


def test_a_run_that_ends_past_a_deadline_is_the_caller_s_timing() -> None:
    due = {
        **CONSENT,
        "transitions": [
            *CONSENT["transitions"][:2],
            {"from": "yes", "after_s": 5, "to": "violated"},
        ],
    }
    waiting = _Waiting(0)

    def slow() -> float:
        waiting.now += 2
        return waiting.now

    ran = run(
        read_program("c1 = confirm()\nc2 = look()\nreturn c2\n"),
        WALKING,
        ANYTHING,
        {},
        waiting.tools,
        policies=[read_policy(due)],
        clock=slow,
        enforce=True,
    )
    assert ran.why is not None
    assert ran.why.lapsed
    assert ran.refused == "consent: a state lapsed into violated"
    cause = blame(ran, WALKING, proven=True)
    assert cause is not None
    assert cause.endswith("after the lapse its proof assumed: the caller's timing")


def test_a_rule_a_run_does_not_take_is_refused_by_its_name() -> None:
    misspelled: Any = {"policy": [read_policy(CONSENT)]}
    with pytest.raises(TypeError, match="policy"):
        run(read_program(ASKED), WALKING, ANYTHING, {}, _Waiting(0).tools, **misspelled)
