from __future__ import annotations

import itertools
import random
from pathlib import Path
from typing import Any

import pytest

from valgebra_planning import planner
from valgebra_planning._valgebra import Sampler
from valgebra_planning.cells import abstract
from valgebra_planning.contracts import Contract, read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.flows import read_flow
from valgebra_planning.library import read_library
from valgebra_planning.mining import mine
from valgebra_planning.planner import plan, predicates
from valgebra_planning.policy import read_policy
from valgebra_planning.program import Branch, Ref, Return, render, steps
from valgebra_planning.verify import verify

ORDER_ID = {"type": "string", "pattern": "^ord_[0-9]+$"}
CLAIM = {"type": "string", "pattern": "^cl_[0-9]+$"}


def _settle(name: str, status: str) -> dict[str, object]:
    return {
        "name": name,
        "inputSchema": {
            "type": "object",
            "properties": {"order_id": ORDER_ID, "status": {"const": status}},
            "required": ["order_id", "status"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"claim_id": CLAIM},
            "required": ["claim_id"],
        },
    }


FIND: dict[str, object] = {
    "name": "find_order",
    "inputSchema": {
        "type": "object",
        "properties": {"order_id": ORDER_ID},
        "required": ["order_id"],
    },
    "outputSchema": {
        "type": "object",
        "properties": {
            "order": {
                "type": "object",
                "properties": {"status": {"enum": ["open", "shipped", "lost"]}},
                "required": ["status"],
            }
        },
        "required": ["order"],
    },
}
SETTLE = [_settle("void_order", "open"), _settle("refund", "shipped"), _settle("trace", "lost")]
TASK = read_task("settle an order", {"order_id": ORDER_ID}, CLAIM)


def _library(*specs: dict[str, object]) -> dict[str, Contract]:
    return {contract.name: contract for contract in map(read_contract, specs)}


def test_a_strong_program_branches_on_every_status_and_verifies() -> None:
    tools = _library(FIND, *SETTLE)
    found = plan(tools, TASK)
    assert found.strong
    assert found.program is not None
    assert found.uncovered == ()
    checked = verify(found.program, tools, TASK)
    assert checked.proven, render(found.program)
    assert checked.count("proven") == len(checked.links)
    text = render(found.program)
    assert text.count("if c1.order.status in ") == 2
    # The last status is settled by a direct call once the other two are tested away.
    assert steps(found.program) == 4


def test_a_weak_program_names_the_status_no_tool_settles() -> None:
    tools = _library(FIND, *SETTLE[:2])
    found = plan(tools, TASK)
    assert not found.strong
    assert found.program is not None
    assert [(gap.ref, gap.examples, gap.undecided) for gap in found.uncovered] == [
        ("c1.order.status", ("lost",), 0)
    ]
    checked = verify(found.program, tools, TASK)
    assert checked.count("refuted") == checked.count("undecided") == 0
    assert len(checked.gaps) == 1


def test_no_program_reaches_a_goal_no_tool_returns() -> None:
    tools = _library(FIND, *SETTLE)
    unreachable = read_task("nothing returns it", {"order_id": ORDER_ID}, {"const": "zz"})
    found = plan(tools, unreachable)
    assert found.program is None
    assert not found.strong


def test_a_value_already_held_inside_the_goal_is_returned_at_once() -> None:
    tools = _library(FIND)
    held = read_task("echo", {"order_id": ORDER_ID}, {"type": "string"})
    found = plan(tools, held)
    assert found.program is not None
    assert isinstance(found.program.end, Return)
    assert found.program.calls == ()


def test_the_depth_bounds_the_calls_and_tests_on_a_path() -> None:
    tools = _library(FIND, *SETTLE)
    assert plan(tools, TASK, depth=3, weak=False).program is None
    found = plan(tools, TASK, depth=4, weak=False)
    assert found.program is not None
    assert isinstance(found.program.end, Branch)


def _arrays(*keys: tuple[str, str]) -> dict[str, Any]:
    record = {
        "type": "object",
        "properties": {name: {"type": kind} for name, kind in keys},
        "required": [name for name, _ in keys],
    }
    return {"type": "array", "items": record}


def _passing(name: str, takes: dict[str, Any], gives: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "inputSchema": {"type": "object", "properties": {"x": takes}, "required": ["x"]},
        "outputSchema": {"type": "object", "properties": {"y": gives}, "required": ["y"]},
    }


#: Arrays of records beside arrays of strings, whose meets valgebra leaves partly undecided: cut
#: in their listing order, the 120 orders count 6 to 9 undecided cells, this one 8, its reverse 6.
STRINGS: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
ARRAYS: list[dict[str, Any]] = [
    _passing("t0", STRINGS | {"minItems": 1}, _arrays(("c", "string")) | {"minItems": 1}),
    _passing("t1", _arrays(("a", "string"), ("b", "integer")), _arrays(("b", "integer"))),
    _passing("t2", _arrays(("c", "string")) | {"minItems": 1}, _arrays(("a", "string"))),
    _passing("t3", STRINGS, STRINGS | {"minItems": 1}),
    _passing("t4", _arrays(("b", "integer")), STRINGS | {"minItems": 1}),
]


def test_the_cut_is_the_same_whatever_order_the_tools_are_listed_in() -> None:
    task = read_task("a string", {"text": {"type": "string"}}, {"type": "string"})
    cuts = set()
    for order in (ARRAYS, ARRAYS[::-1]):
        cut = abstract(predicates({spec["name"]: read_contract(spec) for spec in order}, task))
        names = [predicate.names for predicate in cut.predicates]
        cuts.add(
            frozenset(
                (cell.kind, frozenset(names[i] for i in cell.inside), cell.status)
                for cell in cut.cells
            )
        )
    assert len(cuts) == 1


def test_a_cell_is_asked_in_the_form_valgebra_decides() -> None:
    # Asked as a meet built up cut by cut, the cut of these keeps 22 cells, 8 undecided: valgebra
    # proves some meets empty only with the complement of a predicate the cell lies outside by a
    # relation, and some only without the complement of one a predicate inside it excludes.
    task = read_task("a string", {"text": {"type": "string"}}, {"type": "string"})
    found = predicates({spec["name"]: read_contract(spec) for spec in ARRAYS}, task)
    cut = abstract(found)
    assert len(cut.cells) <= 19
    assert sum(cell.status == "undecided" for cell in cut.cells) <= 5
    # A part dropped as empty holds no value: every value drawn lies in exactly one cell.
    rng = random.Random(0)
    drawn = [v for p in found for v in Sampler(p.root, rng).draw(p.schema, 20)]
    assert drawn
    assert all(sum(cell.values.is_valid(v) for cell in cut.cells) == 1 for v in drawn)


def test_a_cell_of_many_overlapping_ranges_is_decided() -> None:
    # Ranges sharing one value overlap, so no relation settles a cut. Asked only as its kind
    # with the complement of every range it lies outside, a cell is a meet too wide for valgebra
    # to decide, and the cut of these keeps 143 cells, 15 undecided; asked as the meet its cell
    # holds with the new range first, it keeps 135, each decided.
    rng = random.Random(2)

    def quota() -> dict[str, Any]:
        low, high = rng.randrange(10000, 15000), rng.randrange(15000, 20000)
        return {"type": "integer", "minimum": low, "maximum": high}

    specs = [_passing(f"t{i}", quota(), quota()) for i in range(32)]
    held = {"x": {"type": "integer", "minimum": 15000, "maximum": 15000}}
    task = read_task("a quota", held, {"type": "integer"})
    found = predicates({spec["name"]: read_contract(spec) for spec in specs}, task)
    cut = abstract(found)
    assert all(cell.status == "inhabited" for cell in cut.cells)
    # A part dropped as empty holds no value: every value drawn lies in exactly one cell.
    draw = random.Random(0)
    drawn = [v for p in found for v in Sampler(p.root, draw).draw(p.schema, 20)]
    assert drawn
    assert all(sum(cell.values.is_valid(v) for cell in cut.cells) == 1 for v in drawn)


def test_two_spellings_of_one_set_are_one_predicate() -> None:
    spelled = [
        _passing("u", {"type": "string", "description": "a code"}, {"type": "integer"}),
        _passing("v", {"type": "string", "title": "Code"}, {"type": "integer", "default": 0}),
    ]
    task = read_task("an integer", {"text": {"type": "string"}}, {"type": "integer"})
    found = predicates({spec["name"]: read_contract(spec) for spec in spelled}, task)
    by_name = {name: predicate for predicate in found for name in predicate.names}
    assert by_name["argument:u.x"] is by_name["argument:v.x"]
    assert by_name["result:u.y"] is by_name["result:v.y"]


#: Two tools returning one set, and a call whose argument a rule lets one of them fill.
TWINS = {
    "other": {"name": "other", "inputSchema": {"type": "object"}, "outputSchema": {}},
    "ask": {"name": "ask", "inputSchema": {"type": "object"}, "outputSchema": {}},
    "act": {
        "name": "act",
        "inputSchema": {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "pattern": "^id_"}},
            "required": ["id"],
        },
    },
}
TWIN_RESULT = {
    "type": "object",
    "properties": {"v": {"type": "string"}},
    "required": ["v"],
    "additionalProperties": False,
}
ACTED = read_task("act", {}, {"type": "string", "pattern": "^id_"})


def _twins(order: tuple[str, ...]) -> dict[str, Contract]:
    specs = {name: {**TWINS[name]} for name in order}
    for name in ("other", "ask"):
        specs[name]["outputSchema"] = TWIN_RESULT
    return {name: read_contract(spec) for name, spec in specs.items()}


@pytest.mark.parametrize("order", list(itertools.permutations(TWINS)))
def test_a_strong_program_a_flow_admits_is_found_at_its_depth_whatever_the_order(
    order: tuple[str, ...],
) -> None:
    only = read_flow({"name": "the ask's", "sources": ["ask"], "sink": "act.x", "only": True})
    found = plan(_twins(order), ACTED, depth=2, flows=(only,))
    assert found.strong
    assert found.program is not None
    assert render(found.program) == "c1 = ask()\nc2 = act(x=c1.v)\nreturn c2.id"


@pytest.mark.parametrize("order", list(itertools.permutations(TWINS)))
def test_a_strong_program_mined_types_admit_is_found_at_its_depth_whatever_the_order(
    order: tuple[str, ...],
) -> None:
    calls = [
        {"tool": "ask", "arguments": {}, "result": {"v": "k1"}},
        {"tool": "act", "arguments": {"x": "k1"}, "result": {"id": "id_1"}},
        {"tool": "other", "arguments": {}, "result": {"v": "z9"}},
    ]
    contracts = _twins(order)
    found = plan(contracts, ACTED, depth=2, types=mine(calls, contracts))
    assert found.strong


def test_a_plan_with_no_program_says_whether_the_bound_cut_a_path() -> None:
    tools = _twins(("other", "ask", "act"))
    shallow = plan(tools, ACTED, depth=1)
    assert shallow.program is None
    assert shallow.reason is not None
    assert shallow.reason.startswith("no program within the bound of 1 calls and tests, which cut")
    unreachable = plan(tools, read_task("never", {}, {"type": "string", "pattern": "^zz_"}))
    assert unreachable.program is None
    assert unreachable.reason == (
        "no program: no path reached the bound of 6, so no deeper search finds one from the "
        "first 16 bindings of each call the search tries; each of ask.v, other.v meets the goal "
        "and lies outside it, and lists no members a test into the goal could name"
    )
    assert plan(tools, ACTED).reason is None


def test_render_takes_a_program_and_refuses_none() -> None:
    with pytest.raises(TypeError, match="render takes a program, a Block, not NoneType"):
        render(None)  # ty: ignore[invalid-argument-type]


OBJECT = {"type": "string", "pattern": "^obj_[0-9]+$"}
GRASPED = {"type": "string", "pattern": "^grs_[0-9]+$"}


def _robot(pose: str = "estimate_pose") -> list[Contract]:
    """Return a pick's tools, the pose tool under ``pose``, as a listing in that order."""

    def listed(name: str, arguments: dict[str, Any], result: dict[str, Any]) -> Contract:
        return read_contract(
            {
                "name": name,
                "inputSchema": {
                    "type": "object",
                    "properties": arguments,
                    "required": [*arguments],
                },
                "outputSchema": {"type": "object", "properties": result, "required": [*result]},
                "annotations": {"readOnlyHint": True},
            }
        )

    return [
        listed(
            "detect_object",
            {"name": {"type": "string"}},
            {"object_id": OBJECT, "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
        ),
        listed(pose, {"object_id": OBJECT}, {"distance_m": {"type": "number", "minimum": 0}}),
        listed(
            "grasp",
            {"object_id": OBJECT, "distance_m": {"type": "number", "minimum": 0.2, "maximum": 0.6}},
            {"grasp_id": GRASPED},
        ),
        listed("say", {"text": {"type": "string"}}, {"spoken": {"type": "boolean"}}),
    ]


PICK = read_task("pick", {"name": {"type": "string"}}, GRASPED)


def test_the_program_is_the_same_whatever_order_the_tools_are_listed_in() -> None:
    programs = {
        render(found.program)
        for order in itertools.permutations(_robot())
        if (found := plan({tool.name: tool for tool in order}, PICK)).program is not None
    }
    assert len(programs) == 1


def test_the_weak_program_leaving_the_fewest_cells_uncovered_is_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Tried first by name, the pose tool's own weak program stops on more cells than the
    # detection's, which the ranking returns.
    tools = {tool.name: tool for tool in _robot("a_estimate_pose")}
    ranked = plan(tools, PICK)
    assert ranked.program is not None
    assert render(ranked.program).startswith("c1 = detect_object(name=name)")
    monkeypatch.setattr(planner, "RANKING", 0)
    first = plan(tools, PICK)
    assert first.program is not None
    assert render(first.program).startswith("if name in a_estimate_pose.object_id:")

    def cells(found: Any) -> int:
        return sum(len(gap.examples) + gap.undecided for gap in found.uncovered)

    assert (cells(ranked), cells(first)) == (2, 4)


UTTERED = {"type": "string", "pattern": "^utt_[0-9]+$"}


def _speaking() -> dict[str, Contract]:
    """Return a pick's tools with one that speaks, its result closed."""
    tools = {tool.name: tool for tool in _robot() if tool.name != "say"}
    say = read_contract(
        {
            "name": "say",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {"utterance_id": UTTERED},
                "required": ["utterance_id"],
                "additionalProperties": False,
            },
        }
    )
    return {**tools, "say": say}


def test_a_preferred_goal_is_returned_where_it_can_be_and_the_next_fills_its_stops() -> None:
    tools = _speaking()
    either = read_task("pick or say", {"name": {"type": "string"}}, {"anyOf": [GRASPED, UTTERED]})
    shallowest = plan(tools, either, depth=8)
    assert shallowest.program is not None
    assert render(shallowest.program) == "c1 = say(text=name)\nreturn c1.utterance_id"
    ordered = read_task(
        "pick, else say", {"name": {"type": "string"}}, GRASPED, fallbacks=[UTTERED]
    )
    found = plan(tools, ordered, depth=8)
    assert found.program is not None
    assert render(found.program) == (
        "c1 = detect_object(name=name)\n"
        "if c1.confidence in grasp.distance_m:\n"
        "    c2 = grasp(object_id=c1.object_id, distance_m=c1.confidence)\n"
        "    return c2.grasp_id\n"
        "else:\n"
        "    c2 = say(text=name)\n"
        "    return c2.utterance_id"
    )
    assert found.strong
    assert found.uncovered == ()
    assert found.preferred == ("then",)
    assert verify(found.program, tools, ordered).proven


def test_where_no_program_reaches_the_preferred_goal_the_plan_returns_in_the_next() -> None:
    tools = {name: tool for name, tool in _speaking().items() if name != "grasp"}
    ordered = read_task(
        "pick, else say", {"name": {"type": "string"}}, GRASPED, fallbacks=[UTTERED]
    )
    found = plan(tools, ordered)
    assert found.program is not None
    assert render(found.program) == "c1 = say(text=name)\nreturn c1.utterance_id"
    assert found.preferred == ()


def _confirming() -> dict[str, Contract]:
    """Return a pick's tools and a confirmation whose answer nothing reads."""
    tools = {tool.name: tool for tool in _robot() if tool.name != "say"}
    confirm = read_contract(
        {
            "name": "confirm_with_user",
            "inputSchema": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {"confirmed": {"type": "boolean"}},
                "required": ["confirmed"],
            },
            "annotations": {"readOnlyHint": True},
        }
    )
    return {**tools, "confirm_with_user": confirm}


CONFIRMED_FIRST = {
    "name": "motion confirmed first",
    "initial": "start",
    "bad": ["violated"],
    "transitions": [
        {"from": "start", "tool": "confirm_with_user", "to": "ok"},
        {"from": "start", "tool": "grasp", "to": "violated"},
    ],
}


def test_a_call_one_branch_alone_needs_is_made_after_the_test_that_decides_it() -> None:
    found = plan(_confirming(), PICK, depth=7, policies=[read_policy(CONFIRMED_FIRST)])
    assert found.program is not None
    text = render(found.program)
    assert text.index("if ") < text.index("confirm_with_user(") < text.index("grasp(")
    checked = verify(found.program, _confirming(), PICK, policies=[read_policy(CONFIRMED_FIRST)])
    assert all(link.verdict == "proven" for link in checked.links)


def test_a_call_every_path_needs_stays_before_the_test() -> None:
    # Owed from the start until the user is asked, so every path asks, the stop's included.
    asked = {
        "name": "the user is asked on every path",
        "initial": "owed",
        "bad": [],
        "pending": ["owed"],
        "transitions": [{"from": "owed", "tool": "confirm_with_user", "to": "asked"}],
    }
    rules = [read_policy(CONFIRMED_FIRST), read_policy(asked)]
    found = plan(_confirming(), PICK, depth=7, policies=rules)
    assert found.program is not None
    text = render(found.program)
    assert text.index("confirm_with_user(") < text.index("if ")
    checked = verify(found.program, _confirming(), PICK, policies=rules)
    assert all(link.verdict == "proven" for link in checked.links)


SIDES = {"type": "string", "enum": ["left", "right"]}
ACTIONS = {"type": "string", "enum": ["grasp", "walk"]}
PURPOSES = {"type": "string", "enum": ["pick_up", "approach"]}


def _enumerated() -> dict[str, Contract]:
    """Return a pick whose confirmation and grasp each take an enum no tool returns a value for."""

    def listed(name: str, arguments: dict[str, Any], result: dict[str, Any]) -> Contract:
        return read_contract(
            {
                "name": name,
                "inputSchema": {
                    "type": "object",
                    "properties": arguments,
                    "required": [*arguments],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": result,
                    "required": [*result],
                    "additionalProperties": False,
                },
            }
        )

    return {
        contract.name: contract
        for contract in (
            listed("detect_object", {"name": {"type": "string"}}, {"object_id": OBJECT}),
            listed(
                "confirm_with_user",
                {"action": ACTIONS, "purpose": PURPOSES, "object_id": OBJECT},
                {"confirmed": {"type": "boolean"}},
            ),
            listed("grasp", {"object_id": OBJECT, "hand": SIDES}, {"grasp_id": GRASPED}),
        )
    }


def test_a_plan_with_no_program_names_what_only_a_tested_input_fills() -> None:
    asked = read_policy(CONFIRMED_FIRST)
    found = plan(_enumerated(), PICK, depth=8, policies=[asked])
    assert found.program is None
    assert found.reason is not None
    assert found.reason.endswith(
        "; no tool's result guarantees a value for grasp.hand, which a task input fills only "
        "under a test; nor for confirm_with_user.action, confirm_with_user.purpose, of tools only "
        "a policy names, whose results lead to no goal"
    )
    assert "whether a deeper search finds one is not known" in found.reason
    given = read_task(
        "pick",
        {"name": {"type": "string"}, "hand": SIDES, "action": ACTIONS, "purpose": PURPOSES},
        GRASPED,
    )
    assert plan(_enumerated(), given, depth=8, policies=[asked]).program is not None


INTENT = {"confirm_with_user.action": "grasp", "confirm_with_user.purpose": "pick_up"}


def test_a_constant_the_task_fixes_is_written_where_its_argument_is() -> None:
    asked = read_policy(CONFIRMED_FIRST)
    task = read_task("pick", {"name": {"type": "string"}, "hand": SIDES}, GRASPED, constants=INTENT)
    found = plan(_enumerated(), task, depth=8, policies=[asked])
    assert found.program is not None
    text = render(found.program)
    assert "confirm_with_user(action='grasp', purpose='pick_up', object_id=" in text
    checked = verify(found.program, _enumerated(), task, policies=[asked])
    fixed = [link.verdict for link in checked.links if "is the task's" in link.claim]
    assert fixed
    assert set(fixed) == {"proven"}
    assert all(link.verdict == "proven" for link in checked.links)


def test_a_program_passing_another_value_than_the_task_fixes_is_refuted() -> None:
    task = read_task("pick", {"name": {"type": "string"}, "hand": SIDES}, GRASPED, constants=INTENT)
    source = (
        "c1 = detect_object(name=name)\n"
        'c2 = confirm_with_user(action="walk", purpose="pick_up", object_id=c1.object_id)\n'
        "c3 = grasp(object_id=c1.object_id, hand=hand)\n"
        "return c3.grasp_id\n"
    )
    links = verify(read_program(source), _enumerated(), task).links
    (wrong,) = [link for link in links if link.claim.startswith("confirm_with_user.action is")]
    assert (wrong.verdict, wrong.counterexample) == ("refuted", ("walk",))


def test_a_constant_on_an_optional_argument_is_written_and_held() -> None:
    """An argument the task fixes is the task's whether the tool requires it or not.

    Left out, the tool reads its own default, which no task fixed: the robot's confirmation
    takes its action as an optional argument, so the plan writes it and the verifier refutes a
    call leaving it out.
    """
    robot = read_library(Path(__file__).parent.parent / "examples" / "robot-skills")
    task = robot.problem("confirm a grasp").task
    found = plan(robot.tools, task)
    assert found.program is not None
    assert "confirm_with_user(object_id=o, action='grasp')" in render(found.program)
    assert verify(found.program, robot.tools, task).proven
    claim = "confirm_with_user.action is the task's 'grasp'"
    for source, verdict in (
        ("c1 = confirm_with_user(object_id=o)\nreturn c1.confirmed\n", "refuted"),
        ('c1 = confirm_with_user(object_id=o, action="grasp")\nreturn c1.confirmed\n', "proven"),
    ):
        links = verify(read_program(source), robot.tools, task).links
        assert [link.verdict for link in links if link.claim == claim] == [verdict]


def test_a_constant_at_a_sink_an_only_flow_holds_is_refused() -> None:
    told = read_flow(
        {
            "name": "the action is the one detected",
            "sources": ["detect_object"],
            "sink": "confirm_with_user.action",
            "only": True,
        }
    )
    task = read_task("pick", {"name": {"type": "string"}, "hand": SIDES}, GRASPED, constants=INTENT)
    asked = read_policy(CONFIRMED_FIRST)
    assert plan(_enumerated(), task, depth=8, policies=[asked], flows=[told]).program is None


@pytest.mark.parametrize(
    ("constants", "says"),
    [
        ({"hand": "left"}, "a constant names `tool.argument`"),
        ({"grasp.arm": "left"}, "which is no argument of a tool"),
        ({"lift.hand": "left"}, "an argument of a tool not listed: lift"),
        ({"grasp.hand": "both"}, "to a value outside its set"),
    ],
    ids=["no-tool", "no-argument", "unlisted", "outside"],
)
def test_a_constant_naming_no_argument_or_outside_its_set_is_refused(
    constants: dict[str, Any], says: str
) -> None:
    with pytest.raises(ValueError, match=says):
        plan(_enumerated(), read_task("pick", {}, GRASPED, constants=constants))


YES_FIRST = {
    "name": "motion after a yes",
    "initial": "start",
    "bad": ["violated"],
    "transitions": [
        {
            "from": "start",
            "tool": "confirm_with_user",
            "result": {"properties": {"confirmed": {"const": True}}},
            "to": "ok",
        },
        {"from": "start", "tool": "grasp", "to": "violated"},
    ],
}


def test_the_planner_tests_the_answer_a_result_guard_reads() -> None:
    yes = read_policy(YES_FIRST)
    task = read_task("pick", {"name": {"type": "string"}, "hand": SIDES}, GRASPED, constants=INTENT)
    found = plan(_enumerated(), task, depth=8, policies=[yes])
    assert found.program is not None
    lines = render(found.program).splitlines()
    for index, line in enumerate(lines):
        if "grasp(" in line:
            # Each grasp sits under a test of the answer, and the test's other side stops on it.
            above = [text for text in lines[:index] if "confirmed in (True,)" in text]
            assert above, line
    assert sum("stop(" in line and ".confirmed)" in line for line in lines) >= 1
    checked = verify(found.program, _enumerated(), task, policies=[yes])
    assert all(link.verdict == "proven" for link in checked.links)


@pytest.mark.parametrize(
    ("schema", "read"),
    [
        ({"properties": {"confirmed": {"const": True}}}, (("confirmed",), (True,))),
        (
            {"type": "object", "properties": {"answer": {"enum": ["yes", "ok"]}}},
            (("answer",), ("yes", "ok")),
        ),
        ({"properties": {"a": {"properties": {"b": {"const": 1}}}}}, (("a", "b"), (1,))),
        ({"properties": {"distance": {"minimum": 1}}}, None),
        ({"properties": {"a": {"const": 1}, "b": {"const": 2}}}, None),
        (None, None),
    ],
    ids=["const", "enum", "nested", "bound", "two-fields", "none"],
)
def test_a_guard_a_test_writes_is_one_field_listing_its_members(schema: Any, read: Any) -> None:
    assert planner._listing(schema) == read


REACHED = {"type": "number", "minimum": 0, "maximum": 20}
NEAR = {"type": "number", "minimum": 0.2, "maximum": 0.6}


def _walking() -> dict[str, Contract]:
    """Return a pick whose grasp takes a distance a walk changes."""

    def listed(
        name: str, arguments: dict[str, Any], result: dict[str, Any], hints: Any
    ) -> Contract:
        spec = {
            "name": name,
            "inputSchema": {"type": "object", "properties": arguments, "required": [*arguments]},
            "outputSchema": {
                "type": "object",
                "properties": result,
                "required": [*result],
                "additionalProperties": False,
            },
        }
        if hints:
            spec["annotations"] = hints
        return read_contract(spec)

    seeing = {"readOnlyHint": True}
    return {
        contract.name: contract
        for contract in (
            listed("detect_object", {"name": {"type": "string"}}, {"object_id": OBJECT}, seeing),
            listed("estimate_pose", {"object_id": OBJECT}, {"distance": REACHED}, seeing),
            listed("walk_to", {"object_id": OBJECT}, {"arrived": {"type": "boolean"}}, None),
            listed("grasp", {"object_id": OBJECT, "distance": NEAR}, {"grasp_id": GRASPED}, None),
        )
    }


APPROACHED = {
    "name": "a grasp follows a walk",
    "initial": "far",
    "bad": ["violated"],
    "transitions": [
        {"from": "far", "tool": "walk_to", "to": "near"},
        {"from": "far", "tool": "grasp", "to": "violated"},
    ],
}
STALE = (
    "c1 = detect_object(name=name)\n"
    "c2 = estimate_pose(object_id=c1.object_id)\n"
    "c3 = walk_to(object_id=c1.object_id)\n"
    "if c2.distance in grasp.distance:\n"
    "    c4 = grasp(object_id=c1.object_id, distance=c2.distance)\n"
    "    return c4.grasp_id\n"
    "stop(c2.distance)\n"
)
AGAIN = STALE.replace(
    "c3 = walk_to(object_id=c1.object_id)\n",
    "c3 = walk_to(object_id=c1.object_id)\nc5 = estimate_pose(object_id=c1.object_id)\n",
).replace("c2.distance", "c5.distance")


def test_a_value_read_before_a_call_that_may_act_is_no_fresh_reading_after_it() -> None:
    task = read_task("pick", {"name": {"type": "string"}}, GRASPED, fresh=["grasp.distance"])
    claim = "grasp.distance is read after every call before it that may act"

    def verdicts(source: str) -> set[str]:
        links = verify(read_program(source), _walking(), task).links
        return {link.verdict for link in links if link.claim == claim}

    assert verdicts(STALE) == {"refuted"}
    assert verdicts(AGAIN) == {"proven"}
    before = STALE.replace("c3 = walk_to(object_id=c1.object_id)\n", "")
    assert verdicts(before) == {"proven"}
    plain = read_task("pick", {"name": {"type": "string"}}, GRASPED)
    assert all(
        link.verdict == "proven" for link in verify(read_program(STALE), _walking(), plain).links
    )


LISTED = {"name": {"type": "string"}, "names": {"type": "array", "items": {"type": "string"}}}
READ = "c1 = detect_object(name=name)\nc2 = estimate_pose(object_id=c1.object_id)\n"
GRASP = "c4 = grasp(object_id=c1.object_id, distance=c2.distance)\n"


@pytest.mark.parametrize(
    ("source", "verdict"),
    [
        (READ + "for n in names:\n    " + GRASP + "stop(c2.distance)\n", "refuted"),
        (
            READ
            + "for n in names:\n    c3 = walk_to(object_id=c1.object_id)\n"
            + GRASP
            + "return c4.grasp_id\n",
            "refuted",
        ),
        (
            READ
            + "for n in names:\n    c3 = detect_object(name=n)\n"
            + GRASP
            + "return c4.grasp_id\n",
            "proven",
        ),
        (
            "c1 = detect_object(name=name)\nfor n in names:\n"
            "    c2 = estimate_pose(object_id=c1.object_id)\n    " + GRASP + "stop(c1.object_id)\n",
            "proven",
        ),
        (
            READ
            + 'for n in names:\n    if n == "x":\n        c3 = walk_to(object_id=c1.object_id)\n'
            + GRASP
            + "return c4.grasp_id\n",
            "refuted",
        ),
        (
            READ
            + "for n in names:\n    try:\n        c3 = detect_object(name=n)\n"
            + "    except ToolError:\n        c5 = walk_to(object_id=c1.object_id)\n"
            + GRASP
            + "return c4.grasp_id\n",
            "refuted",
        ),
        (
            READ
            + "for n in names:\n    for m in names:\n        c3 = walk_to(object_id=c1.object_id)\n"
            + GRASP
            + "return c4.grasp_id\n",
            "refuted",
        ),
    ],
    ids=[
        "in-a-body-that-acts",
        "after-a-body-that-acts",
        "after-a-read-only-body",
        "read-in-the-body",
        "after-a-branch-that-acts",
        "after-an-error-block-that-acts",
        "after-a-loop-in-the-body-that-acts",
    ],
)
def test_a_value_read_before_a_loop_whose_body_may_act_is_stale_in_it_and_after_it(
    source: str, verdict: str
) -> None:
    task = read_task("pick", LISTED, GRASPED, fresh=["grasp.distance"])
    claim = "grasp.distance is read after every call before it that may act"
    links = verify(read_program(source), _walking(), task).links
    assert {link.verdict for link in links if link.claim == claim} == {verdict}


def test_the_planner_reads_again_after_a_call_that_may_act() -> None:
    walked = read_policy(APPROACHED)
    task = read_task("pick", {"name": {"type": "string"}}, GRASPED, fresh=["grasp.distance"])
    found = plan(_walking(), task, depth=8, policies=[walked])
    assert found.program is not None
    text = render(found.program)
    assert text.index("walk_to(") < text.rindex("estimate_pose(")
    checked = verify(found.program, _walking(), task, policies=[walked])
    assert all(link.verdict == "proven" for link in checked.links)


def test_a_fresh_argument_naming_no_argument_is_refused() -> None:
    with pytest.raises(ValueError, match=r"reads grasp\.reach fresh, which is no argument"):
        plan(_walking(), read_task("pick", {}, GRASPED, fresh=["grasp.reach"]))


def _said(status: object) -> dict[str, Contract]:
    """Return a say whose status is ``status`` and a transcribe returning free text."""

    def tool(name: str, inputs: dict[str, Any], outputs: dict[str, Any]) -> Contract:
        return read_contract(
            {
                "name": name,
                "inputSchema": {"type": "object", "properties": inputs, "required": [*inputs]},
                "outputSchema": {
                    "type": "object",
                    "properties": outputs,
                    "required": [*outputs],
                    "additionalProperties": False,
                },
            }
        )

    return {
        "say": tool("say", {"text": {"type": "string"}}, {"status": status}),
        "transcribe": tool("transcribe", {}, {"text": {"type": "string"}}),
    }


SAYING = {"type": "string", "enum": ["spoken", "interrupted"]}


@pytest.mark.parametrize(
    ("goal", "tested"),
    [
        ({"const": "spoken"}, ("spoken",)),
        ({"type": "string", "pattern": "^sp"}, ("spoken",)),
        ({"enum": ["spoken", "heard"]}, ("spoken",)),
        ({"enum": ["spoken", "interrupted"]}, None),
    ],
    ids=["a-member", "a-pattern", "members-the-field-lacks", "every-member"],
)
def test_a_listed_field_is_tested_into_the_goal_on_the_members_it_lists(
    goal: object, tested: tuple[str, ...] | None
) -> None:
    task = read_task("say", {"text": {"type": "string"}}, goal)
    found = plan({"say": _said(SAYING)["say"]}, task, depth=4)
    assert found.program is not None
    test = found.program.end
    if tested is None:
        # A field inside the goal is returned, not tested.
        assert found.strong
        assert render(found.program) == "c1 = say(text=text)\nreturn c1.status"
        return
    assert not found.strong
    assert isinstance(test, Branch)
    assert (test.ref, test.values) == (Ref("c1", ("status",)), tested)
    # The other side stops: a search on past it would call only to test again.
    assert render(found.program) == (
        "c1 = say(text=text)\n"
        "if c1.status in ('spoken',):\n"
        "    return c1.status\n"
        "else:\n"
        "    stop(c1.status)"
    )
    assert [u.examples for u in found.uncovered] == [("interrupted",)]
    checked = verify(found.program, {"say": _said(SAYING)["say"]}, task)
    assert all(link.verdict == "proven" for link in checked.links)


#: An annotation and a value of it, each constraining no instance (JSON Schema 2020-12
#: Validation, §9; Core, §8.3).
_NOTED = [
    ("description", "how the say ended"),
    ("title", "status"),
    ("default", "spoken"),
    ("deprecated", False),
    ("readOnly", True),
    ("writeOnly", False),
    ("examples", ["spoken"]),
    ("$comment", "the robot's speech status"),
]


@pytest.mark.parametrize(("keyword", "value"), _NOTED, ids=[k for k, _ in _NOTED])
def test_an_annotation_hides_no_member_of_an_enum(keyword: str, value: object) -> None:
    """A field listing its members beside an annotation is tested into the goal as a bare one.

    The annotation constrains no instance, so the members the planner may name are the field's
    own, as for the say a server describes.
    """
    noted = {**SAYING, keyword: value}
    assert planner._listing({"properties": {"status": noted}}) == (
        ("status",),
        tuple(SAYING["enum"]),
    )
    task = read_task("say", {"text": {"type": "string"}}, {"const": "spoken"})
    found = plan({"say": _said(noted)["say"]}, task, depth=4)
    assert found.program is not None
    assert render(found.program) == (
        "c1 = say(text=text)\n"
        "if c1.status in ('spoken',):\n"
        "    return c1.status\n"
        "else:\n"
        "    stop(c1.status)"
    )


def test_no_input_and_no_field_listing_no_members_is_tested_into_the_goal() -> None:
    task = read_task("forge", {"text": {"type": "string"}}, {"const": "spoken"})
    found = plan({"transcribe": _said(SAYING)["transcribe"]}, task, depth=4)
    assert found.program is None
    assert found.reason is not None
    assert found.reason.endswith(
        "; transcribe.text meets the goal and lies outside it, and lists no members a test into "
        "the goal could name"
    )
