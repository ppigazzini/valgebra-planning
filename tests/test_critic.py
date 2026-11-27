from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from valgebra_planning import critic
from valgebra_planning.codemode import repair
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import (
    MAX_CALLS,
    MAX_NESTING,
    MAX_PATHS,
    MAX_VALUES,
    ProgramError,
    critique,
    read_program,
)
from valgebra_planning.flows import read_flow
from valgebra_planning.library import read_library
from valgebra_planning.mining import mine
from valgebra_planning.planner import plan
from valgebra_planning.policy import read_policy
from valgebra_planning.program import Block, Branch, For, Ref, Return, Stop, render

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


@pytest.mark.parametrize("name", sorted(TASKS))
def test_every_planned_program_reads_back_as_itself(name: str) -> None:
    found = plan(TOOLS, TASKS[name])
    assert found.program is not None
    text = render(found.program)
    assert render(read_program(text)) == text


def test_a_program_written_against_typed_tool_apis_is_read_and_proven() -> None:
    source = '''
async def plan(tracking):
    """Return a parcel or say why not."""
    c1 = await tools.find_parcel(tracking=tracking)
    if c1["parcel"]["state"] != "delivered":
        stop(c1["parcel"]["state"])
    if c1["parcel"]["weight_g"] in tools.book_return.weight_g:
        c2 = await tools.book_return(
            parcel_id=c1["parcel"]["parcel_id"],
            state=c1["parcel"]["state"],
            weight_g=c1["parcel"]["weight_g"],
        )
        return c2["label_id"]
    else:
        c3 = await tools.book_freight_return(
            parcel_id=c1["parcel"]["parcel_id"],
            state=c1["parcel"]["state"],
            weight_g=c1["parcel"]["weight_g"],
        )
        return c3["label_id"]
'''
    result = critique(source, TOOLS, TASKS["return a parcel"])
    end = result.program.end
    assert isinstance(end, Branch)
    assert end.values == ("delivered",)
    assert result.verification.count("proven") == len(result.verification.links) == 9
    assert result.notes == ("uncovered: c1.parcel.state (at else)",)


def test_the_naive_chain_gets_its_counterexamples_and_the_guards_to_add() -> None:
    source = (
        "c1 = find_parcel(tracking=tracking)\n"
        "c2 = book_return(parcel_id=c1.parcel.parcel_id, state=c1.parcel.state,"
        " weight_g=c1.parcel.weight_g)\n"
        "return c2.label_id\n"
    )
    result = critique(source, TOOLS, TASKS["return a parcel"])
    assert not result.accepted
    assert len(result.notes) == 2
    state, weight = result.notes
    assert state.startswith("refuted: c1.parcel.state <= book_return.state; ")
    assert "lies outside" in state
    assert state.endswith(
        "test `if c1.parcel.state in book_return.state:` and make the call in that branch"
    )
    assert weight.startswith("refuted: c1.parcel.weight_g <= book_return.weight_g")


def test_a_construct_the_reader_does_not_read_is_refused_by_line() -> None:
    with pytest.raises(ProgramError, match="line 2"):
        read_program("c1 = find_parcel(tracking=tracking)\nwhile c1:\n    pass\nreturn c1\n")
    with pytest.raises(ProgramError, match="by name"):
        read_program("c1 = find_parcel(tracking)\nreturn c1\n")
    with pytest.raises(ProgramError, match="equality"):
        read_program("if a == b:\n    return a\nelse:\n    return b\n")


FREIGHT = read_policy(
    json.loads((EXAMPLE / "policies.json").read_text(encoding="utf-8"))["policies"][0]
)
VERIFIED = read_flow(json.loads((EXAMPLE / "flows.json").read_text(encoding="utf-8"))["flows"][0])
OBSERVED = json.loads((EXAMPLE / "observed.json").read_text(encoding="utf-8"))["calls"]
DIRECT_NOTICE = """\
c1 = find_parcel(tracking=tracking)
c2 = notify(email=c1.parcel.contact)
return c2.notice_id
"""


def test_a_policy_the_program_breaks_is_refuted_with_its_remedy() -> None:
    plain = plan(TOOLS, TASKS["settle a parcel"]).program
    assert plain is not None
    checked = critique(render(plain), TOOLS, TASKS["settle a parcel"], policies=[FREIGHT])
    assert not checked.accepted
    refused = [note for note in checked.notes if "policy freight approved first holds" in note]
    assert refused
    assert all(note.startswith("refuted: ") for note in refused)
    assert all("make the call it requires where it requires it" in note for note in refused)


def test_a_flow_the_program_breaks_is_refuted_with_its_remedy() -> None:
    task = TASKS["notify a parcel's contact"]
    assert critique(DIRECT_NOTICE, TOOLS, task).accepted
    checked = critique(DIRECT_NOTICE, TOOLS, task, flows=[VERIFIED])
    assert not checked.accepted
    assert any(
        note.startswith("refuted: flow notices go to verified addresses")
        and "from a source the rule trusts" in note
        for note in checked.notes
    )


def test_an_error_nothing_handles_names_the_block_to_add() -> None:
    checked = critique(DIRECT_NOTICE, TOOLS, TASKS["notify a parcel's contact"], errors=True)
    assert not checked.accepted
    assert any(
        note.startswith("uncovered: an error of notify; give the call a `try:`")
        for note in checked.notes
    )
    retried = DIRECT_NOTICE.replace("notify(email", "retry(notify(email").replace(
        "contact)", "contact))"
    )
    checked = critique(retried, TOOLS, TASKS["notify a parcel's contact"], errors=True)
    assert any(
        note.startswith("refuted: notify may be retried; retry only a read-only")
        for note in checked.notes
    )


def test_mined_types_are_notes_and_decide_nothing() -> None:
    task = TASKS["notify a parcel's contact"]
    checked = critique(DIRECT_NOTICE, TOOLS, task, types=mine(OBSERVED, TOOLS))
    assert checked.accepted
    assert checked.notes == (
        "assumed: the observed calls show c1.parcel.contact and notify.email as two types",
    )


LITERAL = """\
c1 = find_parcel(tracking=tracking)
c2 = book_return(parcel_id=c1.parcel.parcel_id, state="delivered", weight_g=500)
return c2.label_id
"""


def test_a_constant_is_read_rendered_and_proven_by_membership() -> None:
    program = read_program(LITERAL)
    assert render(program) == LITERAL.replace('"delivered"', "'delivered'").rstrip("\n")
    assert render(read_program(render(program))) == render(program)
    claims = {
        link.claim: link.verdict
        for link in critique(LITERAL, TOOLS, TASKS["return a parcel"]).verification.links
    }
    assert claims["'delivered' <= book_return.state"] == "proven"
    assert claims["500 <= book_return.weight_g"] == "proven"


def test_a_constant_outside_its_argument_is_refuted_by_itself() -> None:
    checked = critique(LITERAL.replace("500", "99999"), TOOLS, TASKS["return a parcel"])
    assert not checked.accepted
    note = "refuted: 99999 <= book_return.weight_g; 99999 lies outside"
    assert checked.notes == (f"{note}; write a constant book_return.weight_g admits",)


def test_a_constant_is_one_json_value_in_either_spelling() -> None:
    checked = critique(LITERAL.replace("500", "500.0"), TOOLS, TASKS["return a parcel"])
    assert "500.0 <= book_return.weight_g" in {
        link.claim for link in checked.verification.links if link.verdict == "proven"
    }


@pytest.mark.parametrize("written", ["(1, 2)", "{1, 2}", "b'x'", "float('nan')", "1e999"])
def test_a_constant_that_is_no_json_value_is_refused(written: str) -> None:
    with pytest.raises(ProgramError, match="line 2"):
        read_program(LITERAL.replace("500", written))


def test_a_key_no_attribute_names_reads_back_as_a_subscript() -> None:
    source = 'c1 = get()\nc2 = use(tag=c1["a-b"]["c.d"])\nreturn c1["class"].k'
    program = read_program(source)
    assert render(program) == source
    assert render(read_program(render(program))) == source


@pytest.mark.parametrize(
    "values", [(True,), (None, "a"), (1, 2.5)], ids=["bool", "null", "numbers"]
)
def test_a_test_of_listed_values_renders_as_python_and_reads_back(values: tuple[Any, ...]) -> None:
    program = Block(
        (),
        Branch(
            Ref("c1", ("confirmed",)),
            "",
            "",
            Block((), Return(Ref("c1"))),
            Block((), Stop(Ref("c1"))),
            values,
        ),
    )
    text = render(program)
    assert render(read_program(text)) == text


@pytest.mark.parametrize(
    ("source", "named"),
    [
        ("c1 = find_parcel(parcel_id=parcel_id\nreturn c1\n", "line 1: '(' was never closed"),
        ("c1 = find_parcel(parcel_id=parcel_id)\n  return c1\n", "line 2: unexpected indent"),
    ],
)
def test_text_python_cannot_parse_is_refused_by_its_own_line(source: str, named: str) -> None:
    """A `SyntaxError` both readings raise is a `ProgramError` at the source's line.

    The reader parses the text as written, and again as a function's body, which reads a
    block indented whole; text neither reading parses is named by the first one's line and
    message, not the wrapper's line below it.
    """
    with pytest.raises(ProgramError, match=re.escape(named)):
        read_program(source)
    indented = "    c1 = find_parcel(parcel_id=parcel_id)\n    return c1\n"
    assert read_program(indented) == read_program(indented.replace("    ", ""))


@pytest.mark.parametrize("listed", ["(-1,)", "([1, 2],)", '({"a": 1},)', "(-0.5, 2)"])
def test_a_listed_constant_reads_back_as_it_is_rendered(listed: str) -> None:
    """A test's constants are the literals `render` writes, so a planned test reads back."""
    source = f"c1 = find_parcel(parcel_id=parcel_id)\nif c1.parcel.state in {listed}:\n"
    program = read_program(source + "    return c1\nreturn c1\n")
    assert read_program(render(program)) == program


@pytest.mark.parametrize("item", ['b"obj_1"', "...", "1j"])
def test_a_listed_constant_no_json_value_is_refused_by_its_line(item: str) -> None:
    source = f"c1 = find_parcel(parcel_id=parcel_id)\nif c1.parcel.state in ({item},):\n"
    with pytest.raises(ProgramError, match="line 2: a test lists JSON values"):
        read_program(source + "    return c1\nreturn c1\n")


def _ran(source: str) -> str:
    """Return a program's text as the reader reads it, rendered."""
    return render(read_program(source))


def test_an_if_with_no_else_runs_on_into_what_follows_it() -> None:
    """Python runs the statements after an `if` once its side runs out, in a loop's body too.

    The side that runs out carries them, so a lost parcel is notified and then booked, as
    Python runs it, where the reader made the booking the `if`'s other side alone.
    """
    source = (
        "c1 = list_parcels()\n"
        "for p in c1.parcels:\n"
        "    c2 = find_parcel(parcel_id=p.id)\n"
        "    if c2.parcel.state in ('lost',):\n"
        "        c3 = notify(parcel_id=p.id)\n"
        "    c4 = book_return(parcel_id=p.id)\n"
        "return c1\n"
    )
    assert _ran(source) == (
        "c1 = list_parcels()\n"
        "for p in c1.parcels:\n"
        "    c2 = find_parcel(parcel_id=p.id)\n"
        "    if c2.parcel.state in ('lost',):\n"
        "        c3 = notify(parcel_id=p.id)\n"
        "        c4 = book_return(parcel_id=p.id)\n"
        "    else:\n"
        "        c4 = book_return(parcel_id=p.id)\n"
        "return c1"
    )


def test_a_handler_runs_on_into_what_follows_its_try() -> None:
    source = (
        "c1 = list_parcels()\n"
        "for p in c1.parcels:\n"
        "    try:\n"
        "        c2 = find_parcel(parcel_id=p.id)\n"
        "    except ToolError:\n"
        "        c3 = notify(parcel_id=p.id)\n"
        "    c4 = book_return(parcel_id=p.id)\n"
        "return c1\n"
    )
    program = read_program(source)
    loop = program.end
    assert isinstance(loop, For)
    (guarded, booked) = loop.body.calls
    assert guarded.on_error is not None
    assert [call.tool for call in guarded.on_error.calls] == ["notify", "book_return"]
    assert booked.tool == "book_return"


def test_a_nested_if_with_no_else_ends_as_its_outer_side_does() -> None:
    source = (
        "c1 = find_parcel(parcel_id=parcel_id)\n"
        "if c1.parcel.state in ('lost',):\n"
        "    if c1.parcel.weight_g in (1,):\n"
        "        c2 = notify(parcel_id=parcel_id)\n"
        "    return c1\n"
        "return c1\n"
    )
    assert _ran(source) == (
        "c1 = find_parcel(parcel_id=parcel_id)\n"
        "if c1.parcel.state in ('lost',):\n"
        "    if c1.parcel.weight_g in (1,):\n"
        "        c2 = notify(parcel_id=parcel_id)\n"
        "        return c1\n"
        "    else:\n"
        "        return c1\n"
        "else:\n"
        "    return c1"
    )


def test_in_a_string_is_refused_as_the_substring_test_it_is() -> None:
    with pytest.raises(ProgramError, match="line 2: `in` a string tests a substring"):
        read_program(
            "c1 = find_parcel(parcel_id=parcel_id)\nif c1.parcel.state in 'a, b':\n"
            "    return c1\nreturn c1\n"
        )


#: A tool returning an array of strings, a string and an object, each declared.
LISTING = {
    "g": read_contract(
        {
            "name": "g",
            "inputSchema": {"type": "object", "properties": {}},
            "outputSchema": {
                "type": "object",
                "properties": {
                    "xs": {"type": "array", "items": {"type": "string"}},
                    "s": {"type": "string"},
                    "o": {"type": "object", "properties": {"p": {"type": "string"}}},
                },
                "required": ["xs", "s", "o"],
            },
        }
    )
}
_KEYED = "test a key on an object alone: `in` an array is membership, and a loop reads one"
_LOOPED = "loop over an array alone: Python walks a string's characters and an object's keys"


@pytest.mark.parametrize(
    ("source", "refuted"),
    [
        (
            'r = g()\nif "p" in r.xs:\n    return r.s\nstop(r)\n',
            f"refuted: r.xs is an object, whose keys `in` reads; [] lies outside; {_KEYED}",
        ),
        (
            'r = g()\nif "a" in r.s:\n    return r.s\nstop(r)\n',
            f"refuted: r.s is an object, whose keys `in` reads; '' lies outside; {_KEYED}",
        ),
        (
            "r = g()\nfor ch in r.s:\n    return ch\nstop(r)\n",
            f"refuted: r.s is an array, whose items `for` reads; '' lies outside; {_LOOPED}",
        ),
    ],
)
def test_a_test_python_reads_otherwise_is_refuted(source: str, refuted: str) -> None:
    # Python reads `"p" in ["p"]` and `"a" in "pa"` true and walks "pa"'s characters, where a
    # run reads a key and an array's items: the program means otherwise in its own language.
    task = read_task("a string", {}, {"type": "string"}, stops={"g": {}})
    checked = critique(source, LISTING, task)
    assert not checked.verification.verified
    assert refuted in checked.notes


@pytest.mark.parametrize(
    "source",
    [
        'r = g()\nif "p" in r.o:\n    return r.o.p\nstop(r)\n',
        "r = g()\nfor x in r.xs:\n    return x\nstop(r)\n",
    ],
)
def test_a_key_test_on_an_object_and_a_loop_on_an_array_are_proven(source: str) -> None:
    task = read_task("a string", {}, {"type": "string"}, stops={"g": {}})
    checked = critique(source, LISTING, task)
    assert checked.verification.verified, checked.notes


#: A tool taking any JSON value, and a task its result's count meets.
TAKING = {
    "f": read_contract(
        {
            "name": "f",
            "inputSchema": {"type": "object", "properties": {"x": {}}, "required": ["x"]},
            "outputSchema": {
                "type": "object",
                "properties": {"n": {"type": "integer"}, "v": {}},
                "required": ["n", "v"],
            },
        }
    )
}
COUNT = read_task("an n", {}, {"type": "integer"}, stops={"f": {}})


def _arrays(depth: int) -> str:
    return "[" * depth + "1" + "]" * depth


def _objects(depth: int) -> str:
    return '{"a": ' * depth + "1" + "}" * depth


@pytest.mark.parametrize(
    ("written", "line"),
    [
        (lambda d: f"c1 = f(x={_arrays(d)})\nreturn c1.n\n", 1),
        (lambda d: f"c1 = f(x={_objects(d)})\nreturn c1.n\n", 1),
        (lambda d: f"c1 = f(x=1)\nif c1.v in ({_arrays(d)},):\n    return c1.n\nstop(c1)\n", 2),
        (lambda d: f"c1 = f(x=1)\nif c1.v in (1, {_objects(d)}):\n    return c1.n\nstop(c1)\n", 2),
    ],
)
def test_a_constant_nesting_past_the_bound_is_refused_at_its_line(
    written: Callable[[int], str], line: int
) -> None:
    # valgebra compiles no set past 128 levels, an array two a level: a 64-deep array argument
    # and a 126-deep object in a test escaped the critic as the compiler's own errors.
    assert critique(written(MAX_NESTING), TAKING, COUNT).verification.verified
    for depth in (MAX_NESTING + 1, 64, 126, 127):
        with pytest.raises(ProgramError, match=f"^line {line}: a constant nests {depth} levels"):
            read_program(written(depth))


def test_a_program_past_the_value_bound_is_refused_with_its_count() -> None:
    # A list of n items is n + 1 values: the list and each item.
    at = f"c1 = f(x={list(range(MAX_VALUES - 1))})\nreturn c1.n\n"
    assert critique(at, TAKING, COUNT).verification.verified
    past = f"c1 = f(x={list(range(MAX_VALUES))})\nreturn c1.n\n"
    with pytest.raises(ProgramError, match=f"constants hold {MAX_VALUES + 1} JSON values"):
        read_program(past)


def test_repair_hands_a_constant_past_the_bound_back_and_goes_on() -> None:
    sources = iter([f"c1 = f(x={_objects(127)})\nreturn c1.n\n", "c1 = f(x=1)\nreturn c1.n\n"])
    repaired = repair(lambda _: next(sources), TAKING, COUNT)
    (_, first), (_, second) = repaired.rounds
    assert first == "line 1: a constant nests 127 levels, past 32"
    assert isinstance(second, critic.Critique)
    assert second.accepted


@pytest.mark.parametrize(("tests", "read"), [(12, True), (13, False)])
def test_a_program_past_the_path_bound_is_refused_with_its_count(tests: int, *, read: bool) -> None:
    # Each `if` whose sides run on doubles the paths: twelve in a row are MAX_PATHS.
    source = "c1 = find_parcel(parcel_id=parcel_id)\n" + "".join(
        f"if c1.parcel.weight_g in ({i},):\n    c{i + 2} = notify(parcel_id=parcel_id)\n"
        for i in range(tests)
    )
    source += "return c1\n"
    if read:
        read_program(source)
    else:
        with pytest.raises(ProgramError, match=f"runs {2**tests} paths, past {MAX_PATHS}"):
            read_program(source)


@pytest.mark.parametrize(("calls", "read"), [(MAX_CALLS, True), (MAX_CALLS + 1, False)])
def test_a_program_past_the_call_bound_is_refused_with_its_count(calls: int, *, read: bool) -> None:
    # A straight line, and a test whose sides share what follows it, which counts once.
    source = "c1 = find_parcel(parcel_id=parcel_id)\nif c1.parcel.weight_g in (1,):\n"
    source += "    c2 = notify(parcel_id=parcel_id)\n"
    source += "".join(f"c{i} = notify(parcel_id=parcel_id)\n" for i in range(3, calls + 1))
    source += "return c1\n"
    if read:
        read_program(source)
    else:
        with pytest.raises(ProgramError, match=f"writes {calls} calls, past {MAX_CALLS}"):
            read_program(source)


_STALE = (
    "c1 = estimate_pose(object_id=o)\nc2 = walk_to(object_id=o)\n"
    "c3 = grasp(object_id=o, distance_m=c1.distance_m)\nreturn c3.grasp_id\n"
)
_ELSEWHERE = (
    "c1 = estimate_pose(object_id=o)\n"
    "c3 = grasp(object_id=p, distance_m=c1.distance_m)\nreturn c3.grasp_id\n"
)


@pytest.mark.parametrize(
    ("source", "remedy"),
    [
        (
            _STALE,
            (
                "read estimate_pose again after the last call that may act before grasp, and "
                "pass that reading"
            ),
        ),
        (_ELSEWHERE, "read estimate_pose for the object_id grasp takes, and pass that reading"),
    ],
    ids=["stale", "another-object"],
)
def test_a_fresh_or_key_refutation_names_the_reading_to_make(source: str, remedy: str) -> None:
    """A value from the source the rule trusts was read at the wrong time or for the wrong key:
    the note names the source and when, or for what, to read it again."""
    robot = read_library(Path(__file__).parent.parent / "examples" / "robot-skills")
    problem = robot.problem("pick, measured")
    notes = critique(source, robot.tools, problem.task, flows=problem.flows).notes
    note = next(note for note in notes if note.startswith("refuted: flow"))
    assert note.endswith(f"; {remedy}")


def test_a_reading_leaves_nothing_shared_to_the_next() -> None:
    """The blocks a reading shares are its own, after one that ends in an error too."""
    running = 'if x in ("a",):\n    c1 = look(x=x)\nreturn x\n'
    read_program(running)
    assert critic._READ.get() is None
    with pytest.raises(ProgramError):
        read_program(running + "return (\n")
    with pytest.raises(ProgramError):
        read_program('if x in ("a",):\n    c1 = look(x=x)\nimport os\nreturn x\n')
    assert critic._READ.get() is None


@pytest.mark.parametrize(
    "step",
    [
        'if x.a{i} in ("p",):\n    c{i} = look(x=x)\n',
        "try:\n    c{i} = look(x=x)\nexcept ToolError:\n    d{i} = look(x=x)\n",
    ],
    ids=["if", "try"],
)
def test_what_follows_each_side_that_runs_on_is_read_once(
    step: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The paths double with each step, and the runs of statements read do not."""
    read = []
    statements = critic._statements
    monkeypatch.setattr(
        critic, "_statements", lambda *a, **k: read.append(a[0]) or statements(*a, **k)
    )
    steps = 11
    read_program("".join(step.format(i=i) for i in range(steps)) + "return x\n")
    assert len(read) <= 2 * steps + 1
