from __future__ import annotations

import json
from pathlib import Path

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.mining import mine
from valgebra_planning.planner import plan
from valgebra_planning.program import render
from valgebra_planning.verify import verify

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
TOOLS = {
    contract.name: contract
    for contract in map(read_contract, json.loads((EXAMPLE / "tools.json").read_text())["tools"])
}
OBSERVED = json.loads((EXAMPLE / "observed.json").read_text())["calls"]
TASK = next(
    read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text())["tasks"]
    if spec["name"] == "notify a parcel's contact"
)


def test_slots_holding_one_observed_value_are_one_type() -> None:
    types = mine(OBSERVED, TOOLS)
    assert types.agree(
        ("result", "find_parcel.parcel.contact"), ("argument", "verify_contact.email")
    )
    assert types.agree(("result", "verify_contact.email"), ("argument", "notify.email"))
    assert not types.agree(("result", "find_parcel.parcel.contact"), ("argument", "notify.email"))
    assert types.agree(
        ("result", "find_parcel.parcel.parcel_id"), ("argument", "book_return.parcel_id")
    )
    # A slot never observed carries no type, so nothing narrows what fills it.
    assert types.agree(
        ("result", "find_parcel.parcel.parcel_id"), ("argument", "redirect.parcel_id")
    )
    assert not types.typed(
        ("result", "find_parcel.parcel.parcel_id"), ("argument", "redirect.parcel_id")
    )


def test_a_boolean_or_a_null_links_nothing() -> None:
    calls = [
        {"tool": "notify", "arguments": {"email": None}, "result": {"notice_id": True}},
        {"tool": "verify_contact", "arguments": {"email": True}, "result": {"email": None}},
    ]
    assert mine(calls, TOOLS).of == {}


def test_mined_types_route_the_notice_through_the_sanitizer_as_observed() -> None:
    unmined = plan(TOOLS, TASK)
    assert unmined.program is not None
    assert "verify_contact" not in render(unmined.program)
    assert unmined.assumed == ()
    found = plan(TOOLS, TASK, types=mine(OBSERVED, TOOLS))
    assert found.program is not None
    assert render(found.program) == (
        "c1 = find_parcel(tracking=tracking)\n"
        "c2 = verify_contact(email=c1.parcel.contact)\n"
        "c3 = notify(email=c2.email)\n"
        "return c3.notice_id"
    )
    assert found.assumed == (
        "c1.parcel.contact -> verify_contact.email",
        "c2.email -> notify.email",
    )


def test_the_verifier_reads_no_mined_type() -> None:
    direct = read_program(
        "c1 = find_parcel(tracking=tracking)\n"
        "c2 = notify(email=c1.parcel.contact)\n"
        "return c2.notice_id\n"
    )
    checked = verify(direct, TOOLS, TASK)
    assert checked.count("proven") == len(checked.links)
