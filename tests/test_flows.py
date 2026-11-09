from __future__ import annotations

import json
from pathlib import Path

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.flows import Flow, read_flow
from valgebra_planning.planner import plan
from valgebra_planning.program import Block, render
from valgebra_planning.verify import verify

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
TOOLS = {
    contract.name: contract
    for contract in map(read_contract, json.loads((EXAMPLE / "tools.json").read_text())["tools"])
}
TASK = next(
    read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text())["tasks"]
    if spec["name"] == "notify a parcel's contact"
)
VERIFIED = read_flow(json.loads((EXAMPLE / "flows.json").read_text())["flows"][0])
DIRECT = read_program(
    "c1 = find_parcel(tracking=tracking)\n"
    "c2 = notify(email=c1.parcel.contact)\n"
    "return c2.notice_id\n"
)


def _flow_links(program: Block, flow: Flow) -> list[tuple[str, str]]:
    checked = verify(program, TOOLS, TASK, flows=[flow])
    return [(link.claim, link.verdict) for link in checked.links if link.claim.startswith("flow")]


def test_a_value_from_an_untrusted_source_does_not_reach_the_sink() -> None:
    assert VERIFIED.sink == "notify.email"
    assert _flow_links(DIRECT, VERIFIED) == [
        (
            (
                "flow notices go to verified addresses: c1.parcel.contact from find_parcel"
                " reaches notify.email"
            ),
            "refuted",
        )
    ]


def test_the_planner_routes_the_value_through_the_sanitizer() -> None:
    unruled = plan(TOOLS, TASK).program
    assert unruled is not None
    assert render(unruled) == render(DIRECT)
    found = plan(TOOLS, TASK, flows=[VERIFIED])
    assert found.program is not None
    assert "verify_contact(email=c1.parcel.contact)" in render(found.program)
    checked = verify(found.program, TOOLS, TASK, flows=[VERIFIED])
    assert checked.count("proven") == len(checked.links)
    assert _flow_links(found.program, VERIFIED) == []


def test_a_test_narrowing_the_value_into_the_endorsed_set_lets_it_through() -> None:
    endorsed = read_flow(
        {
            "name": "only the operations desk",
            "sources": ["find_parcel"],
            "sink": "notify.email",
            "endorsed": {"enum": ["ops@parcels.example"]},
        }
    )
    [(_, verdict)] = _flow_links(DIRECT, endorsed)
    assert verdict == "refuted"
    narrowed = read_program(
        "c1 = find_parcel(tracking=tracking)\n"
        'if c1.parcel.contact == "ops@parcels.example":\n'
        "    c2 = notify(email=c1.parcel.contact)\n"
        "    return c2.notice_id\n"
        "else:\n"
        "    stop(c1.parcel.contact)\n"
    )
    [(_, verdict)] = _flow_links(narrowed, endorsed)
    assert verdict == "proven"


def test_a_sink_called_under_a_test_on_an_untrusted_value_is_an_implicit_flow() -> None:
    implicit = read_flow(
        {**json.loads((EXAMPLE / "flows.json").read_text())["flows"][0], "implicit": True}
    )
    branched = read_program(
        "c1 = find_parcel(tracking=tracking)\n"
        'if c1.parcel.state == "lost":\n'
        "    c2 = verify_contact(email=c1.parcel.contact)\n"
        "    c3 = notify(email=c2.email)\n"
        "    return c3.notice_id\n"
        "else:\n"
        "    stop(c1.parcel.state)\n"
    )
    claims = _flow_links(branched, implicit)
    assert claims == [
        (
            "flow notices go to verified addresses: notify runs under a test on find_parcel",
            "refuted",
        )
    ]
    assert _flow_links(branched, VERIFIED) == []
