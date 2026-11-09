from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.library import read_library
from valgebra_planning.planner import plan
from valgebra_planning.policy import read_policy
from valgebra_planning.run import run
from valgebra_planning.verify import verify

EXAMPLE = Path(__file__).parent.parent / "examples" / "parcels"
TOOLS = {
    contract.name: contract
    for contract in map(read_contract, json.loads((EXAMPLE / "tools.json").read_text())["tools"])
}
SETTLE = next(
    read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text())["tasks"]
    if spec["name"] == "settle a parcel"
)
RETRIED = "c1 = retry(find_parcel(tracking=tracking))\nreturn c1.parcel.parcel_id\n"
NOTICED = """\
c1 = find_parcel(tracking=tracking)
c2 = notify(email=c1.parcel.contact)
c3 = close_case(parcel_id=c1.parcel.parcel_id, state=c1.parcel.state)
return c3.ticket_id
"""
AFTER_NOTICE = read_policy(
    {
        "name": "nothing idempotent after a notice",
        "initial": "start",
        "bad": ["violated"],
        "transitions": [
            {"from": "start", "tool": "notify", "to": "notified"},
            {"from": "notified", "hint": "idempotentHint", "to": "violated"},
        ],
    }
)


def test_a_tool_carries_every_hint_its_annotations_set() -> None:
    assert TOOLS["find_parcel"].hints == {"readOnlyHint"}
    assert TOOLS["book_return"].hints == frozenset()


def test_a_retry_rests_on_its_annotations_and_on_its_errors_being_transient() -> None:
    task = read_task("id", {"tracking": {"type": "string"}}, {"type": "string"})
    (link,) = [
        link for link in verify(read_program(RETRIED), TOOLS, task).links if "retried" in link.claim
    ]
    assert link.verdict == "proven"
    assert link.assumed == (
        "the annotations of find_parcel, a hint its server gives",
        "an error of find_parcel is transient within 8 attempts",
    )


def test_a_trusted_server_waives_the_annotations_and_keeps_the_errors(tmp_path: Path) -> None:
    library = tmp_path / "parcels"
    shutil.copytree(EXAMPLE, library)
    listed = json.loads((library / "tools.json").read_text())
    (library / "tools.json").write_text(json.dumps({**listed, "trusted": True}), encoding="utf-8")
    trusted = read_library(library)
    problem = next(p for p in trusted.problems if p.errors)
    found = plan(trusted.tools, problem.task, errors=True)
    assert found.assumed
    assert all("transient" in assumption for assumption in found.assumed)


def test_a_policy_reads_a_hint_on_any_tool_setting_it() -> None:
    links = {
        link.claim: link
        for link in verify(read_program(NOTICED), TOOLS, SETTLE, policies=[AFTER_NOTICE]).links
    }
    held = links["policy nothing idempotent after a notice holds"]
    assert held.verdict == "refuted"
    assert held.assumed == ("the annotation hints policy nothing idempotent after a notice reads",)


def test_a_run_refuses_the_call_a_hinted_transition_forbids() -> None:
    made: list[str] = []

    def answer(name: str, value: object) -> Callable[..., object]:
        def tool(**_: object) -> object:
            made.append(name)
            return value

        return tool

    parcel = {
        "parcel_id": "pcl_1",
        "state": "returned",
        "weight_g": 9,
        "country": "IT",
        "contact": "a@b.c",
    }
    tools = {
        "find_parcel": answer("find_parcel", {"parcel": parcel}),
        "notify": answer("notify", {"notice_id": "ntc_1"}),
        "close_case": answer("close_case", {"ticket_id": "tkt_1"}),
    }
    program = read_program(NOTICED)
    result = run(
        program, TOOLS, SETTLE, {"tracking": "AB123456789IT"}, tools, policies=[AFTER_NOTICE]
    )
    assert result.refused == "nothing idempotent after a notice: close_case reached violated"
    assert made == ["find_parcel", "notify"]
