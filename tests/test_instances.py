"""The walk holds a policy with ``per`` as its instances, each by the keys holding its value."""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from valgebra_planning.contracts import Contract, read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.policy import Policy, read_policy
from valgebra_planning.run import run
from valgebra_planning.verify import verify

OBJECT_ID = {"type": "string", "pattern": "^obj_[0-9]+$"}
DONE = {"type": "object", "properties": {"done": {"type": "boolean"}}, "required": ["done"]}


def _tool(name: str, *, takes: bool = True) -> Contract:
    inputs = {"object_id": OBJECT_ID} if takes else {}
    return read_contract(
        {
            "name": name,
            "inputSchema": {"type": "object", "properties": inputs, "required": [*inputs]},
            "outputSchema": DONE,
        }
    )


TOOLS = {
    "confirm": _tool("confirm"),
    "grasp": _tool("grasp"),
    "reset": _tool("reset", takes=False),
    "clear": _tool("clear"),
    "mark": _tool("mark"),
    "flush": _tool("flush", takes=False),
}
#: A grasp spends its own object's yes; a reset, carrying no object, takes every yes back.
ASKED = read_policy(
    {
        "name": "a grasp needs its own yes",
        "initial": "unasked",
        "bad": ["violated"],
        "per": "object_id",
        "transitions": [
            {"from": "unasked", "tool": "confirm", "to": "yes"},
            {"from": "yes", "tool": "grasp", "to": "unasked"},
            {"from": "unasked", "tool": "grasp", "to": "violated"},
            {"from": "yes", "tool": "reset", "to": "unasked"},
        ],
    }
)
#: A mark is owed a clear of its own object, and a flush settles every one.
OWED = read_policy(
    {
        "name": "a mark is cleared",
        "initial": "idle",
        "bad": [],
        "pending": ["owed"],
        "per": "object_id",
        "transitions": [
            {"from": "idle", "tool": "mark", "to": "owed"},
            {"from": "owed", "tool": "clear", "to": "idle"},
            {"from": "owed", "tool": "flush", "to": "idle"},
        ],
    }
)
INPUTS = {"first": OBJECT_ID, "second": OBJECT_ID, "xs": {"type": "array", "items": OBJECT_ID}}
TASK = read_task("an object", INPUTS, OBJECT_ID)


def _verdicts(source: str, policy: Policy = ASKED, task: Any = TASK) -> dict[str, str]:
    checked = verify(read_program(source), TOOLS, task, errors=True, policies=[policy])
    return {link.where: link.verdict for link in checked.links if link.claim.startswith("policy")}


@pytest.mark.parametrize(
    ("source", "verdict"),
    [
        ("c1 = confirm(object_id=first)\nc2 = grasp(object_id=first)\nreturn first\n", "proven"),
        (
            "c1 = confirm(object_id=first)\nc2 = grasp(object_id=second)\nreturn first\n",
            "undecided",
        ),
        ("c2 = grasp(object_id=first)\nreturn first\n", "refuted"),
        (
            (
                "c1 = confirm(object_id=first)\nc2 = reset()\nc3 = grasp(object_id=first)\n"
                "return first\n"
            ),
            "refuted",
        ),
        (
            (
                'c1 = confirm(object_id="obj_1")\nc2 = grasp(object_id="obj_1")\n'
                'c3 = grasp(object_id="obj_2")\nreturn first\n'
            ),
            "refuted",
        ),
    ],
    ids=["its-own", "another-input", "none", "taken-back", "another-constant"],
)
def test_a_yes_admits_the_grasp_of_its_own_object(source: str, verdict: str) -> None:
    # One key holds one value, and a call carrying no object steps every instance: two inputs
    # of one set may be one object.
    assert set(_verdicts(source).values()) == {verdict}


def test_two_inputs_valgebra_proves_apart_are_two_objects() -> None:
    task = read_task(
        "an object",
        {"first": {"const": "obj_1"}, "second": {"const": "obj_2"}},
        {"type": "string"},
    )
    source = "c1 = confirm(object_id=first)\nc2 = grasp(object_id=second)\nreturn first\n"
    assert set(_verdicts(source, task=task).values()) == {"refuted"}


@pytest.mark.parametrize(
    "after",
    ["", "c3 = confirm(object_id=second)\nc4 = grasp(object_id=second)\n"],
    ids=["each-item", "and-one-more"],
)
def test_each_item_confirmed_and_grasped_is_proven(after: str) -> None:
    # The item's instance is its own within an iteration, and a past item's once it ends: the
    # one more object is told apart from every item.
    source = (
        "for x in xs:\n    c1 = confirm(object_id=x)\n    c2 = grasp(object_id=x)\n"
        f"{after}return first\n"
    )
    assert set(_verdicts(source).values()) == {"proven"}


def test_a_past_item_is_held_by_no_key_once_its_iteration_ends() -> None:
    # A mark of one item, then a stop at the next: the past item is owed, which a strong update
    # on the next item's key would have read as cleared.
    source = (
        "for x in xs:\n"
        "    c1 = clear(object_id=x)\n"
        '    if x in ("obj_2",):\n'
        "        stop(x)\n"
        "    c2 = mark(object_id=x)\n"
        "c3 = flush()\nreturn first\n"
    )
    verdicts = _verdicts(source, OWED)
    assert verdicts.pop("for:x.then") == "undecided"
    assert set(verdicts.values()) == {"proven"}


def test_a_key_met_first_in_a_loop_holds_a_new_value_where_no_iteration_ran() -> None:
    source = (
        'for x in xs:\n    c1 = confirm(object_id="obj_1")\n'
        'c2 = grasp(object_id="obj_1")\nreturn first\n'
    )
    assert _verdicts(source) == {"empty:x": "refuted", "items:x": "proven"}


def test_an_error_block_holds_the_instances_before_the_call_and_after_it() -> None:
    # The confirmation that answered with an error may not have acted: the block's grasp is of
    # an object that may have no yes, on the side the key was not told apart before the call.
    source = (
        "try:\n"
        '    c1 = confirm(object_id="obj_1")\n'
        "except ToolError:\n"
        '    c2 = grasp(object_id="obj_1")\n'
        "    return first\n"
        'c3 = grasp(object_id="obj_1")\n'
        "return first\n"
    )
    assert _verdicts(source) == {"error:confirm": "undecided", "": "proven"}


@pytest.mark.parametrize(("objects", "verdict"), [(6, "proven"), (7, "undecided")])
def test_past_six_keys_the_policy_steps_as_one_set_of_states(objects: int, verdict: str) -> None:
    # The keys are bounded, and past the bound the walk forgets them soundly: it reads the
    # policy as it did with no instance.
    confirms = "".join(f'c{at} = confirm(object_id="obj_{at}")\n' for at in range(1, objects + 1))
    source = f'{confirms}c0 = grasp(object_id="obj_1")\nreturn first\n'
    assert set(_verdicts(source).values()) == {verdict}


def test_a_lapse_is_named_where_the_proof_rests_on_it() -> None:
    lapsing = read_policy(
        {
            "name": "a yes for two minutes",
            "initial": "unasked",
            "bad": ["violated"],
            "per": "object_id",
            "transitions": [
                {"from": "unasked", "tool": "confirm", "to": "yes"},
                {"from": "unasked", "tool": "grasp", "to": "violated"},
                {"from": "yes", "after_s": 120, "to": "unasked"},
            ],
        }
    )
    source = "c1 = confirm(object_id=first)\nc2 = grasp(object_id=first)\nreturn first\n"
    checked = verify(read_program(source), TOOLS, TASK, policies=[lapsing])
    (link,) = (link for link in checked.links if link.claim.startswith("policy"))
    assert link.verdict == "proven"
    assert "the run reaches grasp within 120 s of entering yes" in link.assumed


#: The values a run of a drawn program may hold: two objects for the inputs, three for items.
FIRSTS = ("obj_1", "obj_2")
ITEMS = ([], ["obj_1"], ["obj_2"], ["obj_1", "obj_2"], ["obj_2", "obj_1"], ["obj_3"])
ARGUMENTS = ("first", "second", '"obj_1"', '"obj_3"')


def _statement(tool: str, argument: str, at: int, indent: str) -> str:
    if tool == "reset":
        return f"{indent}c{at} = reset()\n"
    return f"{indent}c{at} = {tool}(object_id={argument})\n"


@st.composite
def _programs(draw: st.DrawFn) -> tuple[str, bool]:
    """Draw a program of the three tools: calls, at most one loop over ``xs``, and its return."""
    step = st.tuples(st.sampled_from(["confirm", "grasp", "reset"]), st.sampled_from(ARGUMENTS))
    before = draw(st.lists(step, max_size=3))
    looped = draw(st.booleans())
    body = (
        draw(
            st.lists(
                st.tuples(step.map(lambda s: s[0]), st.sampled_from([*ARGUMENTS, "x", "x"])),
                min_size=1,
                max_size=3,
            )
        )
        if looped
        else []
    )
    after = draw(st.lists(step, max_size=3))
    lines, at = [], 1
    for tool, argument in before:
        lines.append(_statement(tool, argument, at, ""))
        at += 1
    if looped:
        lines.append("for x in xs:\n")
        for tool, argument in body:
            lines.append(_statement(tool, argument, at, "    "))
            at += 1
    for tool, argument in after:
        lines.append(_statement(tool, argument, at, ""))
        at += 1
    return "".join(lines) + "return first\n", looped


def _refused_somewhere(source: str) -> tuple[bool, bool]:
    """Return whether some run of the program is refused by the policy, and whether every one is."""
    program = read_program(source)
    tools = {name: (lambda **_: {"done": True}) for name in TOOLS}
    outcomes = []
    for first, second in itertools.product(FIRSTS, FIRSTS):
        for xs in ITEMS:
            inputs = {"first": first, "second": second, "xs": xs}
            ended = run(program, TOOLS, TASK, inputs, tools, policies=[ASKED])
            outcomes.append(ended.why is not None and ended.why.check == "policy")
    return any(outcomes), all(outcomes)


@settings(max_examples=150, deadline=None)
@given(drawn=_programs())
def test_a_program_the_walk_proves_has_no_run_the_policy_refuses(drawn: tuple[str, bool]) -> None:
    source, looped = drawn
    verdicts = set(_verdicts(source).values())
    some, every = _refused_somewhere(source)
    if verdicts == {"proven"}:
        assert not some
    if not looped and verdicts == {"refuted"}:
        assert every
