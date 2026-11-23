from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.planner import plan
from valgebra_planning.run import ToolError, blame, run

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
TOOLS = {
    contract.name: contract
    for contract in map(read_contract, json.loads((EXAMPLE / "tools.json").read_text())["tools"])
}
TASKS = {
    spec["name"]: read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text())["tasks"]
}
INPUTS = {"tracking": "AB123456789IT"}


def _raising(**_: Any) -> Any:
    raise ToolError


def test_a_result_outside_the_declared_schema_is_the_server_s() -> None:
    program = plan(TOOLS, TASKS["settle a parcel"]).program
    assert program is not None
    ran = run(
        program, TOOLS, TASKS["settle a parcel"], INPUTS, {"find_parcel": lambda **_: {"x": 1}}
    )
    assert (
        blame(ran, TOOLS, proven=True) == "find_parcel broke the output schema its server declares"
    )


def test_a_result_outside_an_inferred_schema_is_that_assumption_s() -> None:
    program = plan(TOOLS, TASKS["settle a parcel"]).program
    assert program is not None
    shaped = TOOLS | {"find_parcel": replace(TOOLS["find_parcel"], inferred=True)}
    ran = run(
        program, shaped, TASKS["settle a parcel"], INPUTS, {"find_parcel": lambda **_: {"x": 1}}
    )
    cause = blame(ran, shaped, proven=True)
    assert cause == "the output schema of find_parcel inferred from observed calls does not hold"


def test_an_error_a_retry_cannot_outlast_is_the_fairness_it_assumes() -> None:
    task = TASKS["settle a parcel, tools may fail"]
    program = plan(TOOLS, task, errors=True).program
    assert program is not None
    ran = run(program, TOOLS, task, INPUTS, {"find_parcel": _raising})
    assert blame(ran, TOOLS, proven=True) == (
        "an error of find_parcel outlasted 8 attempts: the fairness a retry assumes"
    )


def test_an_error_no_block_handles_is_the_program_s() -> None:
    program = plan(TOOLS, TASKS["settle a parcel"]).program
    assert program is not None
    ran = run(program, TOOLS, TASKS["settle a parcel"], INPUTS, {"find_parcel": _raising})
    assert blame(ran, TOOLS, proven=True) == "an error of find_parcel the program does not handle"


def test_an_argument_check_breaking_is_a_defect_if_proven_and_the_program_s_if_not() -> None:
    naive = read_program(
        "c1 = find_parcel(tracking=tracking)\n"
        "c2 = book_return(parcel_id=c1.parcel.parcel_id, state=c1.parcel.state, "
        "weight_g=c1.parcel.weight_g)\nreturn c2.label_id\n"
    )
    parcel = {
        "parcel_id": "pcl_1",
        "state": "lost",
        "weight_g": 9,
        "country": "IT",
        "contact": "a@b.c",
    }
    ran = run(
        naive,
        TOOLS,
        TASKS["return a parcel"],
        INPUTS,
        {"find_parcel": lambda **_: {"parcel": parcel}},
    )
    assert (
        blame(ran, TOOLS, proven=False) == "an argument record no link of the program proves broke"
    )
    assert (
        blame(ran, TOOLS, proven=True)
        == "an argument record the verification proved broke: a defect to report"
    )


PARCEL = {
    "parcel_id": "pcl_1",
    "state": "delivered",
    "weight_g": 25000,
    "country": "IT",
    "contact": "ann@shop.example",
}


def test_a_result_with_a_key_its_schema_does_not_declare_is_the_closed_reading_s() -> None:
    listing = json.loads((EXAMPLE / "tools.json").read_text())["tools"]
    shut = {c.name: c for c in (read_contract(t, closed_results=True) for t in listing)}
    task = TASKS["settle a parcel"]
    program = plan(shut, task).program
    assert program is not None
    extra = {"find_parcel": lambda **_: {"parcel": PARCEL, "note": "x"}}
    ran = run(program, shut, task, INPUTS, extra)
    assert ran.why is not None
    assert (ran.why.check, ran.why.tool) == ("closed", "find_parcel")
    cause = blame(ran, shut, proven=True)
    assert (
        cause == "find_parcel returned a key its output schema does not declare: the closed reading"
    )
    # The same result outside the declared set is still the server's.
    broken = run(program, shut, task, INPUTS, {"find_parcel": lambda **_: {"note": "x"}})
    assert blame(broken, shut, proven=True) == (
        "find_parcel broke the output schema its server declares"
    )
