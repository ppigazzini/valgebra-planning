from __future__ import annotations

import itertools
from typing import Any

import pytest

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.program import Block, Call, Ref, Return, render
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


def test_an_optional_field_is_no_result_slot() -> None:
    # The planner holds every result slot after a call, so a field that may be absent is none.
    assert "track.parcel.note" not in [slot.name for slot in TOOLS["track"].results]
    assert "track.parcel.id" in [slot.name for slot in TOOLS["track"].results]


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


def _reads(output: object, path: tuple[str, ...]) -> str:
    """Return the verdict on reading ``path`` of a result of ``output``, `held` where it reads."""
    tool = {"name": "get", "inputSchema": {"type": "object"}, "outputSchema": output}
    contracts = {"get": read_contract(tool)}
    program = Block((Call("get", (), "r"),), Return(Ref("r", path)))
    claim = f"r.{'.'.join(path)} is a value the program holds"
    links = verify(program, contracts, read_task("read", {}, True)).links
    return next((link.verdict for link in links if link.claim == claim), "held")


def test_a_field_below_an_optional_one_is_held_under_both_tests() -> None:
    memo = {"type": "object", "properties": {"memo": NOTE}}
    extra = {
        "type": "object",
        "properties": {
            "parcel": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "extra": memo},
                "required": ["id"],
            }
        },
        "required": ["parcel"],
    }
    track = read_contract(
        {
            "name": "track",
            "inputSchema": {"type": "object", "properties": {"tracking": {"type": "string"}}},
            "outputSchema": extra,
        }
    )
    source = (
        "c1 = track(tracking=tracking)\n"
        'if "extra" in c1.parcel:\n'
        '    if "memo" in c1.parcel.extra:\n'
        "        c2 = annotate(note=c1.parcel.extra.memo)\n"
        "        return c2.ticket\n"
        "    stop(c1)\n"
        "stop(c1)\n"
    )
    checked = verify(read_program(source), {**TOOLS, "track": track}, TASK)
    assert {link.claim: link.verdict for link in checked.links} == {
        "tracking <= track.tracking": "proven",
        "c1.parcel.extra.memo <= annotate.note": "proven",
        "return c2.ticket <= goal": "proven",
    }


def test_a_key_test_on_a_task_input_holds_its_field() -> None:
    given = read_task(
        "annotate a given parcel",
        {"parcel": {"type": "object", "properties": {"note": NOTE}}},
        {"type": "string", "pattern": "^t_[0-9]+$"},
    )
    source = (
        'if "note" in parcel:\n    c1 = annotate(note=parcel.note)\n    return c1.ticket\n'
        "stop(parcel)"
    )
    checked = verify(read_program(source), TOOLS, given)
    assert {link.claim: link.verdict for link in checked.links} == {
        "parcel.note <= annotate.note": "proven",
        "return c1.ticket <= goal": "proven",
    }


def test_a_field_another_keyword_requires_is_never_refuted() -> None:
    # The object declares `k` optional; the root's `allOf` requires it of every `a`. A value
    # the object's set admits without `k` is a witness no result carries.
    output = {
        "type": "object",
        "properties": {"a": {"type": "object", "properties": {"k": NOTE}}},
        "required": ["a"],
        "allOf": [{"properties": {"a": {"required": ["k"]}}}],
    }
    assert _reads(output, ("a", "k")) != "refuted"


def test_a_field_valgebra_cannot_prove_present_is_undecided() -> None:
    # Every member holds `k`, the one key its names may take, and valgebra cannot prove it.
    output = {"type": "object", "propertyNames": {"enum": ["k"]}, "minProperties": 1}
    assert _reads(output, ("k",)) == "undecided"


@pytest.mark.parametrize(
    ("root_requires", "object_requires", "all_of_requires", "path"),
    list(itertools.product([False, True], [False, True], [False, True], [("a",), ("a", "k")])),
)
def test_a_field_is_held_only_where_every_member_carries_it_and_refuted_where_one_lacks_it(
    *, root_requires: bool, object_requires: bool, all_of_requires: bool, path: tuple[str, ...]
) -> None:
    # The members' shapes, as far as `a` and `k` go, are these three. Without `allOf` the
    # verdict is exact; with it valgebra may leave presence undecided, never decide it wrong.
    output: dict[str, Any] = {
        "type": "object",
        "properties": {
            "a": {
                "type": "object",
                "properties": {"k": {"type": "integer"}},
                "required": ["k"] if object_requires else [],
            }
        },
        "required": ["a"] if root_requires else [],
    }
    if all_of_requires:
        output["allOf"] = [{"properties": {"a": {"required": ["k"]}}}]
    returns = read_contract(
        {"name": "get", "inputSchema": {"type": "object"}, "outputSchema": output}
    ).returns
    assert returns is not None
    members = [shape for shape in ({}, {"a": {}}, {"a": {"k": 1}}) if returns.is_valid(shape)]
    carried = all(
        path[0] in member and (len(path) == 1 or path[1] in member[path[0]]) for member in members
    )
    verdict = _reads(output, path)
    assert verdict != ("refuted" if carried else "held")
    if not all_of_requires:
        assert verdict == ("held" if carried else "refuted")


def test_a_field_a_test_on_the_way_may_have_kept_is_never_refuted() -> None:
    # The test keeps the `a` whose only key is `k`, which valgebra cannot prove holds `k`; the
    # root's `a` may lack it, but the test excluded every such `a`.
    keyed = {"type": "object", "propertyNames": {"enum": ["k"]}, "minProperties": 1}
    tools = {
        contract.name: contract
        for contract in map(
            read_contract,
            [
                {
                    "name": "get",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {
                        "type": "object",
                        "properties": {"a": {"type": "object"}},
                        "required": ["a"],
                    },
                },
                {
                    "name": "keep",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"a": keyed},
                        "required": ["a"],
                    },
                },
            ],
        )
    }
    source = "r = get()\nif r.a in keep.a:\n    return r.a.k\nstop(r)"
    claims = {link.claim: link.verdict for link in verify(read_program(source), tools, TASK).links}
    assert claims["r.a.k is a value the program holds"] == "undecided"


TAG = {"enum": ["a", "b"]}


def _bound(output: object, source: str) -> list[tuple[str, str, tuple[object, ...]]]:
    """Return the links not proven of ``source``, over `get` returning ``output`` and `use`."""
    tools = {
        contract.name: contract
        for contract in map(
            read_contract,
            [
                {"name": "get", "inputSchema": {"type": "object"}, "outputSchema": output},
                {
                    "name": "use",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"tag": TAG},
                        "required": ["tag"],
                    },
                },
            ],
        )
    }
    links = verify(read_program(source), tools, read_task("use", {}, True)).links
    return [(ln.claim, ln.verdict, ln.counterexample) for ln in links if ln.verdict != "proven"]


def test_a_field_its_record_narrows_is_proven_by_the_record() -> None:
    # The field declares any string; the record's `allOf` keeps it inside the argument.
    output = {
        "type": "object",
        "properties": {"tag": {"type": "string"}},
        "required": ["tag"],
        "allOf": [{"properties": {"tag": TAG}}],
    }
    assert _bound(output, "r = get()\nu = use(tag=r.tag)\nstop(r)") == []


def test_a_value_no_member_carries_is_never_a_witness() -> None:
    # `"zz"` lies outside the argument and every result may carry it; a string drawn for the
    # declared field is a witness only where the record admits it too.
    narrowed = {"enum": ["a", "b", "zz"]}
    output = {
        "type": "object",
        "properties": {"tag": {"type": "string"}},
        "required": ["tag"],
        "allOf": [{"properties": {"tag": narrowed}}],
    }
    [(claim, verdict, found)] = _bound(output, "r = get()\nu = use(tag=r.tag)\nstop(r)")
    assert (claim, verdict != "proven") == ("r.tag <= use.tag", True)
    assert found in ((), ("zz",))


def test_a_record_s_refutation_a_test_on_the_way_may_have_excluded_is_undecided() -> None:
    # The test keeps the `a` whose only key is `k`, inside the argument though valgebra cannot
    # prove it; the record refutes the binding on an `a` without `k`, which the test excluded.
    keyed = {"type": "object", "propertyNames": {"enum": ["k"]}, "minProperties": 1}
    tools = {
        contract.name: contract
        for contract in map(
            read_contract,
            [
                {
                    "name": "get",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {
                        "type": "object",
                        "properties": {"a": {"type": "object"}},
                        "required": ["a"],
                    },
                },
                {
                    "name": "keep",
                    "inputSchema": {"type": "object", "properties": {"a": keyed}},
                },
                {
                    "name": "use",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"a": {"type": "object", "required": ["k"]}},
                        "required": ["a"],
                    },
                },
            ],
        )
    }
    source = "r = get()\nif r.a in keep.a:\n    u = use(a=r.a)\n    stop(r)\nstop(r)"
    links = verify(read_program(source), tools, read_task("use", {}, True)).links
    assert {link.claim: link.verdict for link in links}["r.a <= use.a"] == "undecided"


def test_a_witness_drawn_for_a_field_is_reported_where_a_member_carries_it() -> None:
    worker = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "pattern": "^[a-z]{3}-[0-9]{4}$"},
            "key": {"type": "string", "pattern": "^[0-9a-f]{8}$"},
            "arch": {"enum": ["a", "unknown"]},
        },
        "required": ["name", "key", "arch"],
        "additionalProperties": False,
    }
    output = {"type": "object", "properties": {"w": worker}, "required": ["w"]}
    [(claim, verdict, found)] = _bound(output, "r = get()\nu = use(tag=r.w.arch)\nstop(r)")
    assert (claim, verdict, found) == ("r.w.arch <= use.tag", "refuted", ("unknown",))


@pytest.mark.parametrize(
    ("declared", "narrowed"),
    list(
        itertools.product(
            [["a", "b"], ["a", "b", "zz"], ["a", "zz"]], [None, ["a", "b"], ["a", "zz"], ["zz"]]
        )
    ),
)
def test_a_field_link_is_proven_only_where_every_member_s_field_lies_inside(
    *, declared: list[str], narrowed: list[str] | None
) -> None:
    # The members' tags are the declared ones the `allOf` keeps; a witness is one of them.
    output: dict[str, Any] = {
        "type": "object",
        "properties": {"tag": {"enum": declared}},
        "required": ["tag"],
    }
    if narrowed is not None:
        output["allOf"] = [{"properties": {"tag": {"enum": narrowed}}}]
    tags = {tag for tag in declared if narrowed is None or tag in narrowed}
    [*links] = _bound(output, "r = get()\nu = use(tag=r.tag)\nstop(r)")
    if tags <= {"a", "b"}:
        assert links == []
    else:
        [(_, verdict, found)] = links
        assert verdict != "proven"
        assert set(found) <= tags - {"a", "b"}
