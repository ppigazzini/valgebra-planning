"""A value's provenance, held per path: a program the verifier proves never breaks a rule in a run.

A `fresh` or `key` rule reads when and for what a value was returned, facts about the value, not
about the name holding it. The runner binds each name to the call that returned its value on the
path it runs, so it is the oracle: over programs drawn on the robot library, each rebinding its
names inside error blocks, branches and loops, one whose every link the verifier proves never
breaks the rule when run against answers drawn freely (Roşu and Chen 2012, Def. 11; Debenedetti
et al. 2025, §5.3; Bertoli et al. 2006, Def. 3.4).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from valgebra_planning.contracts import read_task
from valgebra_planning.critic import read_program
from valgebra_planning.library import read_library
from valgebra_planning.run import ToolError, run
from valgebra_planning.verify import verify

ROBOT_DIR = Path(__file__).parent.parent / "examples" / "robot-skills"
ROBOT = read_library(ROBOT_DIR)
#: Any value returned is the goal, so a program is proven on its bindings and its rules alone.
TASK = read_task(
    "t",
    {
        "o": {"type": "string"},
        "p": {"type": "string"},
        "a": {"type": "string"},
        "b": {"type": "string"},
        "d": {"type": "number"},
    },
    True,
)
GIVEN = {"o": "obj_1", "p": "obj_2", "a": "mug", "b": "bottle", "d": 0.4}
NAMES = ("c1", "c2", "c3")
#: The results an object or a distance is read from: a loop's item holds both.
_OBJECTS = ("detect", "walk_to", "item")
_DISTANCES = ("estimate_pose", "item")
FLOWS = (ROBOT.flows["measured"], ROBOT.flows["scanned"])


#: What a name is bound to on the path drawn: the tool returning it, and the object it was
#: called on, a loop's item's being its scan's.
_Bound = dict[str, tuple[str, str]]


@st.composite
def _block(draw: st.DrawFn, bound: _Bound, depth: int) -> list[str]:
    """Draw a block's lines over the names ``bound`` before it.

    A grasp mostly reads a distance measured or scanned under a name at the object that name was
    measured for, so the rules hold on many paths, while the calls drawn between them rebind
    the names, act, fail into an error block, branch and loop, which is where a reading by
    name and one by value part.
    """
    lines: list[str] = []
    shapes = ["call", "call", "grasp", "grasp", "try", "if", "for"]
    for _ in range(draw(st.integers(1, 4))):
        objects = ["o", "p", *(f"{n}.object_id" for n, (k, _) in bound.items() if k in _OBJECTS)]
        shape = draw(st.sampled_from(shapes if depth < 2 else shapes[:5]))
        name = draw(st.sampled_from(NAMES))
        if shape in ("call", "try"):
            tool = draw(
                st.sampled_from(["estimate_pose", "estimate_pose", "walk_to", "detect", "scan"])
            )
            target = draw(st.sampled_from(objects))
            argument = (
                f"label={draw(st.sampled_from(['a', 'b']))}"
                if tool == "detect"
                else f"object_id={target}"
            )
            call = f"{name} = {tool}({argument})"
            if shape == "call":
                lines.append(call)
                bound[name] = (tool, target)
                continue
            handler = dict(bound)
            block = draw(_block(handler, depth + 1))
            lines += [
                "try:",
                f"    {call}",
                "except ToolError:",
                *(f"    {line}" for line in block),
            ]
            _joined(bound, [handler, {**bound, name: (tool, target)}])
        elif shape == "grasp":
            measured = [(n, at) for n, (k, at) in bound.items() if k in _DISTANCES]
            if not measured:
                # Nothing is measured yet: measure first, under a name drawn as freely.
                source, target = draw(st.sampled_from(NAMES)), draw(st.sampled_from(objects))
                lines.append(f"{source} = estimate_pose(object_id={target})")
                bound[source] = ("estimate_pose", target)
                measured = [(source, target)]
            source, target = draw(st.sampled_from(measured))
            if draw(st.integers(0, 3)) == 0:
                target = draw(st.sampled_from(objects))
            distance = "d" if draw(st.integers(0, 9)) == 0 else f"{source}.distance_m"
            lines.append(f"{name} = grasp(object_id={target}, distance_m={distance})")
            bound[name] = ("grasp", target)
        elif shape == "if":
            tested = [n for n, (k, _) in bound.items() if k == "detect"]
            if not tested:
                continue
            then = dict(bound)
            block = draw(_block(then, depth + 1))
            lines += [
                f"if {draw(st.sampled_from(tested))}.found in (True,):",
                *(f"    {line}" for line in block),
            ]
            _joined(bound, [then, dict(bound)])
        else:
            scanned = [(n, at) for n, (k, at) in bound.items() if k == "scan"]
            if not scanned:
                continue
            array, target = draw(st.sampled_from(scanned))
            # What follows a loop reads the values held before it, the runner's and the verifier's
            # reading alike, so the body's names are its own.
            block = draw(_block({**bound, "it": ("item", target)}, depth + 1))
            lines += [f"for it in {array}.items:", *(f"    {line}" for line in block)]
    return lines or ["c1 = detect(label=a)"]


def _joined(bound: _Bound, paths: list[_Bound]) -> None:
    """Keep what a name is bound to where every path reaching here binds it so, and drop it."""
    for name in {*bound, *(n for path in paths for n in path)}:
        held = {path.get(name) for path in paths}
        kept = held.pop() if len(held) == 1 else None
        if kept is None or name == "it":
            bound.pop(name, None)
        else:
            bound[name] = kept


def _answering(data: st.DataObject) -> dict[str, Any]:
    """Return the robot's tools, each answering with a value drawn from its contract or an error."""
    objects, distances = st.sampled_from(["obj_1", "obj_2"]), st.floats(0, 1)
    item = st.fixed_dictionaries({"object_id": objects, "distance_m": distances})

    def answer(name: str, arguments: dict[str, Any]) -> Any:
        if data.draw(st.integers(0, 3)) == 0:
            message = f"{name} failed"
            raise ToolError(message)
        drawn = {
            "detect": lambda: {"object_id": data.draw(objects), "found": data.draw(st.booleans())},
            "estimate_pose": lambda: {"distance_m": data.draw(distances)},
            "walk_to": lambda: {"object_id": arguments["object_id"], "arrived": True},
            "scan": lambda: {"items": data.draw(st.lists(item, max_size=2))},
            "grasp": lambda: {"grasp_id": "g_1"},
        }
        return drawn[name]()

    return {name: (lambda name=name, **given: answer(name, given)) for name in ROBOT.tools}


@settings(max_examples=400, deadline=None)
@given(st.data())
def test_a_program_proven_under_a_rule_never_breaks_it_when_run(data: st.DataObject) -> None:
    flow = data.draw(st.sampled_from(FLOWS))
    source = "\n".join([*data.draw(_block({}, 0)), "return o"]) + "\n"
    program = read_program(source)
    if not verify(program, ROBOT.tools, TASK, flows=[flow]).proven:
        return
    ran = run(program, ROBOT.tools, TASK, GIVEN, _answering(data), flows=[flow], enforce=False)
    assert not [broken for broken in ran.broken if broken.startswith("flow ")], source


def _flow_links(source: str, task: str) -> dict[str, str]:
    """Return each flow link's verdict over a robot task's rules, by where and claim."""
    problem = ROBOT.problem(task)
    checked = verify(read_program(source), ROBOT.tools, problem.task, flows=problem.flows)
    return {
        f"{link.where}: {link.claim}": link.verdict
        for link in checked.links
        if link.claim.startswith("flow ")
    }


def _programs() -> dict[str, tuple[str, str]]:
    """Return the robot library's written programs, each with its task, by name."""
    written = json.loads((ROBOT_DIR / "programs.json").read_text(encoding="utf-8"))["programs"]
    return {spec["name"]: ("\n".join(spec["source"]) + "\n", spec["task"]) for spec in written}


_FRESH = "c1.distance_m from estimate_pose was returned after every call before grasp that may act"
_KEYED = "from estimate_pose was returned for the object_id grasp takes"


@pytest.mark.parametrize(
    ("name", "refuted", "proven"),
    [
        (
            "an error block grasps at the reading made before the walk",
            f"error:estimate_pose: flow measured: {_FRESH}",
            f": flow measured: {_FRESH}",
        ),
        (
            "an error block grasps one object at another's reading",
            f"error:estimate_pose: flow measured: c1.distance_m {_KEYED}",
            f": flow measured: c1.distance_m {_KEYED}",
        ),
        (
            "a grasp reads a detection the program has since replaced",
            f": flow measured: c2.distance_m {_KEYED}",
            None,
        ),
        (
            "the same loop, its item named as a call's result",
            (
                "for:c1: flow scanned: c1.distance_m from scan was returned for the object_id "
                "grasp takes"
            ),
            None,
        ),
        (
            "a branch rebinds the reading the other side grasps at",
            f"else: flow measured: c1.distance_m {_KEYED}",
            f"then: flow measured: c1.distance_m {_KEYED}",
        ),
        (
            "a loop rebinds the reading the grasp after it reads",
            f": flow measured: c2.distance_m {_KEYED}",
            None,
        ),
    ],
)
def test_a_value_read_under_a_rebound_name_is_the_value_its_path_holds(
    name: str, refuted: str, proven: str | None
) -> None:
    """A value read under a name is the one the path reaching the read bound to it.

    A failed call binds nothing, so its block reads the value from before, stale if it was; a
    rebinding is a new value, so a key compares values, not names; and a branch's or a loop's
    bindings stay on their own path. A loop's item, named like a result, was returned by the
    call returning its array.
    """
    links = _flow_links(*_programs()[name])
    assert links[refuted] == "refuted"
    if proven is not None:
        assert links[proven] == "proven"


def test_a_rule_holds_where_the_value_is_the_one_measured_for_the_object() -> None:
    measured = (
        "c1 = estimate_pose(object_id=o)\nc2 = grasp(object_id=o, distance_m=c1.distance_m)\n"
    )
    assert set(_flow_links(measured + "return c2.grasp_id\n", "pick, measured").values()) == {
        "proven"
    }
    other = measured.replace("grasp(object_id=o", "grasp(object_id=p")
    links = _flow_links(other + "return c2.grasp_id\n", "pick, measured")
    assert links[f": flow measured: c1.distance_m {_KEYED}"] == "refuted"
    scanned = (
        "c1 = scan(object_id=o)\nfor it in c1.items:\n"
        "    c2 = grasp(object_id=o, distance_m=it.distance_m)\nstop(c1)\n"
    )
    assert set(_flow_links(scanned, "pick, scanned").values()) == {"proven"}
    # A test narrows a value and keeps it the one value it was.
    tested = (
        'c1 = estimate_pose(object_id=o)\nif o in ("obj_1",):\n'
        "    c2 = grasp(object_id=o, distance_m=c1.distance_m)\n    return c2.grasp_id\nstop(o)\n"
    )
    assert set(_flow_links(tested, "pick, measured").values()) == {"proven"}
