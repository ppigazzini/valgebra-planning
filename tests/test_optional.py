from __future__ import annotations

import itertools
from typing import Any

import pytest

from valgebra_planning.codemode import repair
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import critique, read_program
from valgebra_planning.program import Block, Call, Ref, Return, render
from valgebra_planning.run import blame, run
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


def test_a_run_reading_a_field_the_value_lacks_ends_named() -> None:
    untested = (
        "c1 = track(tracking=tracking)\nc2 = annotate(note=c1.parcel.note)\nreturn c2.ticket\n"
    )
    tools = {
        "track": lambda **_: {"parcel": {"id": "a"}},
        "annotate": lambda **_: {"ticket": "t_1"},
    }
    for enforce in (True, False):
        ended = run(read_program(untested), TOOLS, TASK, {"tracking": "x"}, tools, enforce=enforce)
        assert ended.broken == ("c1.parcel.note is no value the run holds",)
        assert ended.why is not None
        assert ended.why.check == "read"
    assert blame(ended, TOOLS, proven=False) == (
        "a value the program reads no link of the program proves broke"
    )


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


def test_a_field_another_keyword_requires_is_held() -> None:
    # The object declares `k` optional; the root's `allOf` requires it of every `a`.
    output = {
        "type": "object",
        "properties": {"a": {"type": "object", "properties": {"k": NOTE}}},
        "required": ["a"],
        "allOf": [{"properties": {"a": {"required": ["k"]}}}],
    }
    assert _reads(output, ("a", "k")) == "held"


#: The key `k` alone, as a `const` beside another keyword, which valgebra-jsonschema reads as a
#: predicate; a `const` alone it reads as the closed record of its key.
KEY_K = {"const": "k", "maxLength": 1}


def test_a_field_only_a_predicate_holds_present_is_undecided() -> None:
    # Every member holds `k`, the one key its names may take. valgebra-jsonschema reads a
    # `propertyNames` holding a keyword beside its `const` as a predicate, inside which no
    # relation looks.
    output = {"type": "object", "propertyNames": KEY_K, "minProperties": 1}
    assert _reads(output, ("k",)) == "undecided"


@pytest.mark.parametrize(
    ("root_requires", "object_requires", "all_of_requires", "path"),
    list(itertools.product([False, True], [False, True], [False, True], [("a",), ("a", "k")])),
)
def test_a_field_is_held_only_where_every_member_carries_it_and_refuted_where_one_lacks_it(
    *, root_requires: bool, object_requires: bool, all_of_requires: bool, path: tuple[str, ...]
) -> None:
    # The members' shapes, as far as `a` and `k` go, are these three.
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
    assert _reads(output, path) == ("held" if carried else "refuted")


def test_a_field_a_test_on_the_way_may_have_kept_is_never_refuted() -> None:
    # The test keeps the `a` whose only key is `k`, a key set valgebra-jsonschema reads as a
    # predicate, so nothing proves it holds `k`; the root's `a` may lack it, but the test
    # excluded every such `a`.
    keyed = {"type": "object", "propertyNames": KEY_K, "minProperties": 1}
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
    assert (claim, verdict) == ("r.tag <= use.tag", "refuted")
    assert found in ((), ("zz",))


def test_a_record_s_refutation_a_test_on_the_way_may_have_excluded_is_undecided() -> None:
    # The test keeps the `a` whose only key is `k`, inside the argument though nothing proves it:
    # valgebra-jsonschema reads the key set as a predicate. The record refutes the binding on an
    # `a` without `k`, which the test excluded.
    keyed = {"type": "object", "propertyNames": KEY_K, "minProperties": 1}
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


#: A result whose `f` lies inside the argument, and whose `g` holds `k`, where its `n` is 1.
SPLIT = {
    "anyOf": [
        {
            "type": "object",
            "properties": {
                "n": {"const": 1},
                "f": TAG,
                "g": {"type": "object", "properties": {"k": TAG}, "required": ["k"]},
            },
            "required": ["n", "f", "g"],
        },
        {
            "type": "object",
            "properties": {"n": {"const": 2}, "f": {"const": "zz"}},
            "required": ["n", "f"],
        },
    ]
}
#: The two shapes of `SPLIT`'s members.
SPLIT_MEMBERS = ({"n": 1, "f": "a", "g": {"k": "b"}}, {"n": 2, "f": "zz"})


def _split() -> tuple[dict[str, Any], Any]:
    tools = {
        contract.name: contract
        for contract in map(
            read_contract,
            [
                {"name": "get", "inputSchema": {"type": "object"}, "outputSchema": SPLIT},
                {
                    "name": "use",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"tag": TAG},
                        "required": ["tag"],
                    },
                },
                {
                    "name": "pair",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"x": {"type": "object"}, "y": TAG},
                        "required": ["x", "y"],
                        "if": {"properties": {"x": {"properties": {"n": {"const": 2}}}}},
                        "then": {"properties": {"y": {"const": "b"}}},
                    },
                },
            ],
        )
    }
    return tools, read_task("split", {"y": TAG}, True)


@pytest.mark.parametrize(
    ("bound", "claim"),
    [
        ("u = use(tag=r.f)", "r.f <= use.tag"),
        ("u = use(tag=r.g.k)", "r.g.k is a value the program holds"),
        ("p = pair(x=r, y=y)", "the arguments of pair <= its input schema"),
    ],
)
def test_a_test_on_another_field_of_the_value_narrows_what_a_refutation_reads(
    bound: str, claim: str
) -> None:
    # Under `n == 1` every member's `f` lies inside the argument and its `g` holds `k`, and
    # `pair` takes any `y`; a member with `n` 2, which the test excluded, breaks all three.
    source = f"r = get()\nif r.n in (1,):\n    {bound}\n    stop(r)\n{bound}\nstop(r)"
    tools, task = _split()
    links = {
        link.where: link
        for link in verify(read_program(source), tools, task).links
        if link.claim == claim
    }
    assert links["then"].verdict != "refuted"
    assert links["else"].verdict != "proven"
    for witness in links["else"].counterexample:
        # Each is what a member with `n` 2 holds there: its `f`, itself, or a record of it.
        assert witness in ("zz", SPLIT_MEMBERS[1]) or witness["x"]["n"] == 2
    for member, y in itertools.product(SPLIT_MEMBERS, ("a", "b")):
        calls = {
            "get": lambda member=member, **_: member,
            "use": lambda **_: {},
            "pair": lambda **_: {},
        }
        ran = run(read_program(source), tools, task, {"y": y}, calls)
        assert (ran.why is None) == (member["n"] == 1 or (bound.startswith("p") and y == "b"))


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


UNTESTED = "c1 = track(tracking=tracking)\nc2 = annotate(note=c1.parcel.note)\nreturn c2.ticket\n"


def test_the_critique_of_an_untested_optional_field_names_its_key_test() -> None:
    [note] = critique(UNTESTED, TOOLS, TASK).notes
    assert note.startswith("refuted: c1.parcel.note is a value the program holds; ")
    assert note.endswith(
        '; test `if "note" in c1.parcel:` and read `c1.parcel.note` in that branch'
    )
    [unbound] = critique(UNTESTED.replace("note=c1", "note=c9"), TOOLS, TASK).notes
    assert unbound == "refuted: c9.parcel.note is a value the program holds"


def test_a_model_handed_the_key_test_writes_a_program_the_critic_accepts() -> None:
    def propose(told: str | None) -> str:
        # A model that writes what the critique names: the key test, the read in its branch.
        if told is None or 'test `if "note" in c1.parcel:`' not in told:
            return UNTESTED
        return (
            "c1 = track(tracking=tracking)\n"
            'if "note" in c1.parcel:\n'
            "    c2 = annotate(note=c1.parcel.note)\n"
            "    return c2.ticket\n"
            'c3 = annotate(note="n_none")\n'
            "return c3.ticket\n"
        )

    repaired = repair(propose, TOOLS, TASK)
    assert repaired.accepted
    assert len(repaired.rounds) == 2
