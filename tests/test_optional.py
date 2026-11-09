from __future__ import annotations

from typing import Any

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.program import render
from valgebra_planning.run import run
from valgebra_planning.verify import verify

NOTE = {"type": "string", "pattern": "^n_[a-z]+$"}
TOOLS = {
    contract.name: contract
    for contract in map(
        read_contract,
        [
            {
                "name": "track",
                "inputSchema": {
                    "type": "object",
                    "properties": {"tracking": {"type": "string"}},
                    "required": ["tracking"],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "parcel": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}, "note": NOTE},
                            "required": ["id"],
                        }
                    },
                    "required": ["parcel"],
                },
            },
            {
                "name": "annotate",
                "inputSchema": {
                    "type": "object",
                    "properties": {"note": NOTE},
                    "required": ["note"],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {"ticket": {"type": "string", "pattern": "^t_[0-9]+$"}},
                    "required": ["ticket"],
                },
            },
        ],
    )
}
TASK = read_task(
    "annotate", {"tracking": {"type": "string"}}, {"type": "string", "pattern": "^t_[0-9]+$"}
)
TESTED = """\
c1 = track(tracking=tracking)
if "note" in c1.parcel:
    c2 = annotate(note=c1.parcel.note)
    return c2.ticket
else:
    stop(c1.parcel)"""


def test_an_optional_field_is_a_slot_held_apart_from_the_guaranteed_ones() -> None:
    track = TOOLS["track"]
    assert [slot.name for slot in track.optional] == ["track.parcel.note"]
    assert "track.parcel.note" not in [slot.name for slot in track.results]


def test_a_test_for_the_key_makes_the_field_a_value_the_program_holds() -> None:
    program = read_program(TESTED)
    assert render(program) == TESTED
    checked = verify(program, TOOLS, TASK)
    assert {link.claim for link in checked.links if link.verdict != "proven"} == set()
    assert "c1.parcel.note <= annotate.note" in {link.claim for link in checked.links}
    assert [gap.ref for gap in checked.gaps] == ["c1.parcel"]


def test_reading_the_field_without_the_test_is_refuted_by_a_record_lacking_it() -> None:
    untested = (
        "c1 = track(tracking=tracking)\nc2 = annotate(note=c1.parcel.note)\nreturn c2.ticket\n"
    )
    checked = verify(read_program(untested), TOOLS, TASK)
    refuted = [link for link in checked.links if link.verdict == "refuted"]
    assert [link.claim for link in refuted] == ["c1.parcel.note is a value the program holds"]
    (lacking,) = refuted[0].counterexample
    assert "note" not in lacking
    assert TOOLS["track"].result("parcel").values.is_valid(lacking)


def test_a_run_takes_the_branch_the_key_shows() -> None:
    def tools(parcel: dict[str, Any]) -> dict[str, Any]:
        return {
            "track": lambda **_: {"parcel": parcel},
            "annotate": lambda **_: {"ticket": "t_1"},
        }

    program = read_program(TESTED)
    noted = run(program, TOOLS, TASK, {"tracking": "x"}, tools({"id": "a", "note": "n_b"}))
    bare = run(program, TOOLS, TASK, {"tracking": "x"}, tools({"id": "a"}))
    assert noted.value == "t_1"
    assert noted.stopped is None
    assert bare.stopped == "c1.parcel"
