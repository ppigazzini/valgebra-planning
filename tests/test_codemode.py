from __future__ import annotations

import ast
import json
from pathlib import Path

from valgebra_planning.codemode import repair, stubs
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.planner import plan
from valgebra_planning.program import render

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
LISTING = json.loads((EXAMPLE / "tools.json").read_text())["tools"]
TOOLS = {contract.name: contract for contract in map(read_contract, LISTING)}
SETTLE = next(
    read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text())["tasks"]
    if spec["name"] == "settle a parcel"
)
NAIVE = (
    "c1 = find_parcel(tracking=tracking)\n"
    "c2 = book_return(parcel_id=c1.parcel.parcel_id, state=c1.parcel.state, "
    "weight_g=c1.parcel.weight_g)\n"
    "return c2.label_id\n"
)


def test_the_stubs_are_python_and_carry_each_tool_s_own_schemas() -> None:
    text = stubs(TOOLS)
    module = ast.parse(text)
    functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    assert set(TOOLS) <= set(functions)
    for tool in LISTING:
        doc = ast.get_docstring(functions[tool["name"]]) or ""
        written = doc.split("Arguments, as JSON Schema: ", 1)[1].split("\n", 1)[0]
        assert json.loads(written) == tool["inputSchema"]


def test_a_model_s_program_is_repaired_by_the_critique_it_is_handed() -> None:
    planned = plan(TOOLS, SETTLE).program
    assert planned is not None
    told: list[str | None] = []

    def propose(feedback: str | None) -> str:
        told.append(feedback)
        return NAIVE if feedback is None else render(planned)

    repaired = repair(propose, TOOLS, SETTLE)
    assert repaired.accepted
    assert len(repaired.rounds) == 2
    assert told[1] is not None
    assert "refuted: c1.parcel.state <= book_return.state" in told[1]


def test_a_program_the_reader_refuses_is_handed_back_as_the_reason() -> None:
    told: list[str | None] = []

    def propose(feedback: str | None) -> str:
        told.append(feedback)
        return "import os\n" if feedback is None else NAIVE

    repaired = repair(propose, TOOLS, SETTLE, rounds=2)
    assert not repaired.accepted
    assert told[1] is not None
    assert told[1].startswith("the program is not read: line 1")


def test_the_rounds_run_out_on_a_program_never_mended() -> None:
    repaired = repair(lambda _: NAIVE, TOOLS, SETTLE, rounds=3)
    assert not repaired.accepted
    assert len(repaired.rounds) == 3
