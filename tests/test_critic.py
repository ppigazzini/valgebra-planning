from __future__ import annotations

import json
from pathlib import Path

import pytest

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import ProgramError, critique, read_program
from valgebra_planning.flows import read_flow
from valgebra_planning.mining import mine
from valgebra_planning.planner import plan
from valgebra_planning.policy import read_policy
from valgebra_planning.program import Branch, render

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
TOOLS = {
    contract.name: contract
    for contract in map(read_contract, json.loads((EXAMPLE / "tools.json").read_text())["tools"])
}
TASKS = {
    spec["name"]: read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text())["tasks"]
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


FREIGHT = read_policy(json.loads((EXAMPLE / "policies.json").read_text())["policies"][0])
VERIFIED = read_flow(json.loads((EXAMPLE / "flows.json").read_text())["flows"][0])
OBSERVED = json.loads((EXAMPLE / "observed.json").read_text())["calls"]
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
