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
    for contract in map(
        read_contract, json.loads((EXAMPLE / "tools.json").read_text(encoding="utf-8"))["tools"]
    )
}
SETTLE = next(
    read_task(spec["name"], spec["inputs"], spec["goal"])
    for spec in json.loads((EXAMPLE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
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


def test_a_tool_carries_every_hint_its_annotations_give_with_mcp_s_defaults() -> None:
    assert TOOLS["find_parcel"].hints == {"readOnlyHint", "openWorldHint"}
    # `idempotentHint: false`: destructive by default, as MCP reads a tool not read-only.
    assert TOOLS["book_return"].hints == {"destructiveHint", "openWorldHint"}


def _hinted(annotations: object) -> frozenset[str]:
    spec = {"name": "t", "inputSchema": {"type": "object"}}
    if annotations is not None:
        spec["annotations"] = annotations
    return read_contract(spec).hints


def test_a_tool_setting_no_annotation_is_destructive_and_open_world() -> None:
    assert _hinted(None) == {"destructiveHint", "openWorldHint"}
    assert _hinted({}) == {"destructiveHint", "openWorldHint"}
    assert _hinted({"readOnlyHint": True}) == {"readOnlyHint", "openWorldHint"}
    assert _hinted({"destructiveHint": False, "openWorldHint": False}) == frozenset()
    # A read-only tool is never destructive, whatever it says of it.
    assert _hinted({"readOnlyHint": True, "destructiveHint": True}) == {
        "readOnlyHint",
        "openWorldHint",
    }
    # A value that is no boolean is read as absent: the default holds.
    assert _hinted({"readOnlyHint": "true", "destructiveHint": "false"}) == {
        "destructiveHint",
        "openWorldHint",
    }
    assert _hinted({"idempotentHint": True, "x-vendor": True}) == {
        "destructiveHint",
        "idempotentHint",
        "openWorldHint",
        "x-vendor",
    }


CONFIRMED = read_policy(
    {
        "name": "every destructive call confirmed",
        "initial": "start",
        "bad": ["violated"],
        "transitions": [
            {"from": "start", "tool": "confirm", "to": "ok"},
            {"from": "start", "hint": "destructiveHint", "to": "violated"},
        ],
    }
)


def test_an_unannotated_motion_is_held_by_a_policy_on_destructive_calls() -> None:
    walking = {
        contract.name: contract
        for contract in map(
            read_contract,
            [
                {"name": "walk_to", "inputSchema": {"type": "object"}},
                {
                    "name": "look",
                    "inputSchema": {"type": "object"},
                    "annotations": {"readOnlyHint": True},
                },
                {
                    "name": "confirm",
                    "inputSchema": {"type": "object"},
                    "annotations": {"readOnlyHint": True},
                },
            ],
        )
    }
    task = read_task("walk", {}, True)
    unconfirmed = verify(
        read_program("c1 = walk_to()\nstop(c1)\n"), walking, task, policies=[CONFIRMED]
    )
    confirmed = verify(
        read_program("c1 = confirm()\nc2 = walk_to()\nstop(c2)\n"),
        walking,
        task,
        policies=[CONFIRMED],
    )
    looked = verify(read_program("c1 = look()\nstop(c1)\n"), walking, task, policies=[CONFIRMED])
    policy = "policy every destructive call confirmed holds"
    assert {link.claim: link.verdict for link in unconfirmed.links}[policy] == "refuted"
    assert {link.claim: link.verdict for link in confirmed.links}[policy] == "proven"
    assert {link.claim: link.verdict for link in looked.links}[policy] == "proven"


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
    listed = json.loads((library / "tools.json").read_text(encoding="utf-8"))
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
