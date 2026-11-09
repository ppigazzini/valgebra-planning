from __future__ import annotations

import json
from pathlib import Path

import pytest

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import ProgramError, critique, read_program
from valgebra_planning.planner import plan
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
        read_program("c1 = find_parcel(tracking=tracking)\nfor x in c1:\n    pass\nreturn c1\n")
    with pytest.raises(ProgramError, match="by name"):
        read_program("c1 = find_parcel(tracking)\nreturn c1\n")
    with pytest.raises(ProgramError, match="equality"):
        read_program("if a == b:\n    return a\nelse:\n    return b\n")
