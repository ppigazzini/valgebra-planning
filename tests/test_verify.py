from __future__ import annotations

import copy
import math
import random
from dataclasses import replace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from valgebra_planning import verify as verifier
from valgebra_planning._valgebra import Validator, compile_schema
from valgebra_planning.codemode import repair
from valgebra_planning.contracts import inside, read_contract, read_task, sides
from valgebra_planning.critic import read_program
from valgebra_planning.program import Block, Branch, Call, Ref, Return, Stop, render, steps
from valgebra_planning.run import run
from valgebra_planning.verify import Link, _smaller, verify

ORDER_ID = {"type": "string", "pattern": "^ord_[0-9]+$"}
SPECS: list[dict[str, object]] = [
    {
        "name": "find_order",
        "inputSchema": {
            "type": "object",
            "properties": {"order_id": ORDER_ID},
            "required": ["order_id"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "order": {
                    "type": "object",
                    "properties": {
                        "status": {"enum": ["open", "shipped", "lost"]},
                        "total": {"type": "integer", "minimum": 0},
                    },
                    "required": ["status", "total"],
                }
            },
            "required": ["order"],
        },
    },
    {
        "name": "refund",
        "inputSchema": {
            "type": "object",
            "properties": {"order_id": ORDER_ID, "status": {"const": "shipped"}},
            "required": ["order_id", "status"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"refund_id": {"type": "string", "pattern": "^re_"}},
            "required": ["refund_id"],
        },
    },
    {
        "name": "pay",
        "inputSchema": {
            "type": "object",
            "properties": {"card": {"type": "string"}, "iban": {"type": "string"}},
            "oneOf": [{"required": ["card"]}, {"required": ["iban"]}],
        },
    },
]
TOOLS = {contract.name: contract for contract in map(read_contract, SPECS)}
TASK = read_task("refund an order", {"order_id": ORDER_ID}, {"type": "string", "pattern": "^re_"})

FIND = Call("find_order", (("order_id", Ref("order_id")),), "c1")
STATUS = Ref("c1", ("order", "status"))
REFUND = Call("refund", (("order_id", Ref("order_id")), ("status", STATUS)), "c2")
GUARDED = Block(
    (FIND,),
    Branch(
        STATUS,
        "refund",
        "status",
        Block((REFUND,), Return(Ref("c2", ("refund_id",)))),
        Block((), Stop(STATUS)),
    ),
)


def test_a_guarded_program_is_proven_link_by_link_and_its_stop_is_a_gap() -> None:
    result = verify(GUARDED, TOOLS, TASK)
    assert result.count("proven") == 4
    assert result.count("refuted") == result.count("undecided") == 0
    assert [(gap.where, gap.ref) for gap in result.gaps] == [("else", "c1.order.status")]
    assert not result.proven


def test_the_naive_chain_is_refuted_at_the_one_binding_that_is_wrong() -> None:
    naive = Block((FIND, REFUND), Return(Ref("c2", ("refund_id",))))
    result = verify(naive, TOOLS, TASK)
    refuted = [link for link in result.links if link.verdict == "refuted"]
    assert [link.claim for link in refuted] == ["c1.order.status <= refund.status"]
    assert refuted[0].counterexample[0] in {"open", "lost"}
    assert result.count("proven") == 3


def test_an_example_given_is_tried_first() -> None:
    naive = Block((FIND, REFUND), Return(Ref("c2", ("refund_id",))))
    result = verify(naive, TOOLS, TASK, examples={"c1.order.status": ["lost"]})
    refuted = next(link for link in result.links if link.verdict == "refuted")
    assert refuted.counterexample == ("lost",)


def test_a_missing_argument_an_unknown_value_and_an_unknown_tool_are_refuted() -> None:
    program = Block(
        (
            FIND,
            Call("refund", (("order_id", Ref("order_id")),), "c2"),
            Call("ship", (), "c3"),
        ),
        Return(Ref("c1", ("order", "note"))),
    )
    claims = {
        link.claim for link in verify(program, TOOLS, TASK).links if link.verdict == "refuted"
    }
    assert claims == {
        "refund.status is bound",
        "ship is a tool",
        "c1.order.note is a value the program holds",
    }


def test_a_test_against_a_tool_or_an_argument_the_library_lacks_is_refuted() -> None:
    # A test reading no set walks neither branch, so a program whose every call and return
    # stands behind it would pass with nothing proven: the test is refuted, as a call is.
    for tool, argument, claim in (
        ("ship", "order_id", "ship is a tool"),
        ("refund", "order", "refund.order is an argument"),
    ):
        tested = Block(
            (FIND,),
            Branch(
                Ref("order_id"),
                tool,
                argument,
                Block((Call("refund", (("order_id", Ref("order_id")),), "c2"),), Return(Ref("c2"))),
                Block((), Stop(Ref("order_id"))),
            ),
        )
        links = verify(tested, TOOLS, TASK).links
        assert [(link.claim, link.verdict) for link in links if link.verdict != "proven"] == [
            (claim, "refuted")
        ]
        assert {link.where for link in links} == {""}


def test_a_stop_names_a_value_the_program_holds() -> None:
    # A stop hands back the value it names, so a field the result may lack is refuted there,
    # as at a return, and a test showing the key reads it.
    say = read_contract(
        {
            "name": "say",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {"status": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["status"],
                "additionalProperties": False,
            },
        }
    )
    task = read_task("say", {"t": {"type": "string"}}, True, stops={"say": True})

    def links(source: str) -> list[tuple[str, str]]:
        checked = verify(read_program(source), {"say": say}, task)
        return [(link.claim, link.verdict) for link in checked.links if link.verdict != "proven"]

    assert links("c1 = say(text=t)\nstop(c1.reason)\n") == [
        ("c1.reason is a value the program holds", "refuted")
    ]
    assert links("c1 = say(text=t)\nstop(c1.status)\n") == []
    assert links('c1 = say(text=t)\nif "reason" in c1:\n    stop(c1.reason)\nstop(c1)\n') == []


def test_a_weak_program_reaches_the_return_it_counts() -> None:
    """A return under a test no value meets is proven of no value, and makes no program weak.

    A weak solution reaches the goal on some execution (Cimatti et al. 2003, Def. 2.10): a
    status the result never takes reaches its return on none, so the program only stops.
    """
    say = read_contract(
        {
            "name": "say",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {"status": {"enum": ["spoken", "queued"]}},
                "required": ["status"],
                "additionalProperties": False,
            },
        }
    )
    task = read_task("say", {"t": {"type": "string"}}, {"type": "string"}, stops={"say": True})
    program = 'c1 = say(text=t)\nif c1.status in ("{}",):\n    return c1.status\nstop(c1)\n'
    lost = verify(read_program(program.format("lost")), {"say": say}, task)
    assert (lost.verified, lost.weak, lost.returns) == (True, False, ())
    spoken = verify(read_program(program.format("spoken")), {"say": say}, task)
    assert (spoken.verified, spoken.weak, spoken.returns) == (True, True, ("then",))
    met = repair(lambda _: program.format("lost"), {"say": say}, task, rounds=2, accept="weak")
    assert not met.accepted


def test_a_record_the_input_schema_relates_is_asked_whole() -> None:
    card = read_task("pay", {"card": {"type": "string"}, "iban": {"type": "string"}}, True)
    both = Block(
        (Call("pay", (("card", Ref("card")), ("iban", Ref("iban"))), "c1"),),
        Stop(Ref("card")),
    )
    record = next(link for link in verify(both, TOOLS, card).links if "arguments" in link.claim)
    assert record.verdict == "refuted"
    assert set(record.counterexample[0]) == {"card", "iban"}
    one = Block((Call("pay", (("card", Ref("card")),), "c1"),), Stop(Ref("card")))
    record = next(link for link in verify(one, TOOLS, card).links if "arguments" in link.claim)
    assert record.verdict != "refuted"


def test_an_argument_whose_name_ends_in_a_question_mark_is_read_as_written() -> None:
    # valgebra's dict display reads `x?` as an optional `x`, which the relation never names.
    act = read_contract(
        {
            "name": "act",
            "inputSchema": {
                "type": "object",
                "properties": {"x?": {"type": "integer"}, "y": {"type": "integer"}},
                "dependentRequired": {"x?": ["y"]},
            },
        }
    )
    task = read_task("act", {"n": {"type": "integer"}}, True)
    alone = Block((Call("act", (("x?", Ref("n")),), "c1"),), Stop(Ref("n")))
    checked = verify(alone, {"act": act}, task)
    record = next(link for link in checked.links if "arguments" in link.claim)
    assert record.verdict == "refuted"
    assert set(record.counterexample[0]) == {"x?"}
    broken = run(alone, {"act": act}, task, {"n": 1}, {"act": lambda **_: {}})
    assert broken.broken == ("act refuses its arguments (y: missing_key)",)


def test_a_program_reads_as_python() -> None:
    assert render(GUARDED) == (
        "c1 = find_order(order_id=order_id)\n"
        "if c1.order.status in refund.status:\n"
        "    c2 = refund(order_id=order_id, status=c1.order.status)\n"
        "    return c2.refund_id\n"
        "else:\n"
        "    stop(c1.order.status)"
    )
    assert steps(GUARDED) == 3


#: Arguments `x` and `y`, with `y` the same as `x` where `x` is 1 or 2.
PAIRED = {
    "type": "object",
    "required": ["x", "y"],
    "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
    "if": {"properties": {"x": {"const": 1}}},
    "then": {"properties": {"y": {"const": 1}}},
    "else": {"properties": {"y": {"const": 2}}},
}


def _paired(output: object) -> tuple[Link, Validator]:
    """Return the argument record's link of `pair(x=r.a, y=r.b)`, and `get`'s output set."""
    get = read_contract({"name": "get", "inputSchema": {"type": "object"}, "outputSchema": output})
    pair = read_contract({"name": "pair", "inputSchema": PAIRED})
    source = read_program("r = get()\nu = pair(x=r.a, y=r.b)\nreturn r")
    links = verify(source, {"get": get, "pair": pair}, read_task("pair", {}, True)).links
    assert get.returns is not None
    return next(link for link in links if "arguments" in link.claim), get.returns


def test_a_record_no_call_makes_is_never_a_witness() -> None:
    # `a` and `b` are equal on every result, so every call makes a record the input set holds.
    equal = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "allOf": [
            {
                "oneOf": [
                    {"properties": {"a": {"const": 1}, "b": {"const": 1}}},
                    {"properties": {"a": {"const": 2}, "b": {"const": 2}}},
                ]
            }
        ],
    }
    record, _ = _paired(equal)
    assert (record.verdict, record.counterexample) == ("undecided", ())


def test_a_record_a_call_makes_refutes_two_fields_of_one_value() -> None:
    apart = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    }
    record, returns = _paired(apart)
    assert record.verdict == "refuted"
    [found] = record.counterexample
    assert returns.is_valid({"a": found["x"], "b": found["y"]})


@pytest.mark.parametrize(
    ("held", "taken", "witness"),
    [
        ({"type": "string"}, {"type": "string", "minLength": 1}, ""),
        ({"type": "string", "minLength": 201}, {"type": "string", "maxLength": 200}, "x" * 201),
        ({"type": "integer", "minimum": -1000}, {"type": "integer", "minimum": 1}, 0),
        ({"const": 1e400}, {"const": 0}, math.inf),
        ({"enum": [-1e400]}, {"const": 0}, -math.inf),
        ({"const": 10**400}, {"const": 0}, 10**400),
    ],
    ids=["empty", "one-past-the-bound", "zero", "infinity", "less-infinity", "past-a-float"],
)
def test_a_witness_is_shrunk_before_it_is_shown(
    held: object, taken: object, witness: object
) -> None:
    """A drawn witness is as long as the draw: it is shrunk while it stays one the program may
    hold and outside the set it refutes, length first, so the reader sees the smallest."""
    reader = read_contract(
        {
            "name": "read",
            "inputSchema": {"type": "object", "properties": {}},
            "outputSchema": {"type": "object", "properties": {"v": held}, "required": ["v"]},
        }
    )
    taker = read_contract(
        {
            "name": "take",
            "inputSchema": {"type": "object", "properties": {"v": taken}, "required": ["v"]},
        }
    )
    program = read_program("c1 = read()\nc2 = take(v=c1.v)\nreturn c2\n")
    links = verify(program, {"read": reader, "take": taker}, read_task("t", {}, True)).links
    (refuted,) = [link for link in links if link.claim == "c1.v <= take.v"]
    assert refuted.verdict == "refuted"
    (found,) = refuted.counterexample
    if isinstance(witness, str) and witness:
        assert isinstance(found, str)
        assert len(found) == len(witness)
    else:
        assert found == witness


def test_a_tool_that_refuses_arguments_it_admits_is_never_retried() -> None:
    """A retry of a tool whose contract `refuses` is refuted, its refusal being no transient
    error, and a call to it with no error block leaves that error uncovered."""
    plain = {
        "get_params": read_contract(
            {
                "name": "get_params",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {"run_id": {"type": "string"}},
                    "required": ["run_id"],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {"params": {"type": "array", "items": {"type": "number"}}},
                    "required": ["params"],
                },
            }
        )
    }
    tools = {"get_params": replace(plain["get_params"], refuses=True)}
    task = read_task(
        "params", {"run_id": {"type": "string"}}, {"type": "array", "items": {"type": "number"}}
    )
    retried = verify(
        read_program("c1 = retry(get_params(run_id=run_id))\nreturn c1.params\n"),
        tools,
        task,
    )
    assert [(link.claim, link.verdict) for link in retried.links if "retried" in link.claim] == [
        ("get_params may be retried", "refuted")
    ]
    bare = verify(
        read_program("c1 = get_params(run_id=run_id)\nreturn c1.params\n"),
        tools,
        task,
    )
    assert not bare.proven
    assert [gap.ref for gap in bare.gaps] == ["an error of get_params"]
    assert verify(
        read_program("c1 = get_params(run_id=run_id)\nreturn c1.params\n"), plain, task
    ).proven


def test_a_record_lacking_a_field_is_shrunk_before_it_is_shown() -> None:
    """A record a program reads a field it lacks from is shrunk as a link's witness is: while
    it stays one the output's declared set holds and the call may return, and lacks the field,
    so the reader sees the smallest record that refutes the reference."""
    reader = read_contract(
        {
            "name": "read",
            "inputSchema": {"type": "object", "properties": {}},
            "outputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string", "maxLength": 4000}},
                "required": ["name"],
                "additionalProperties": False,
            },
        }
    )
    program = read_program("c1 = read()\nreturn c1.other\n")
    links = verify(program, {"read": reader}, read_task("t", {}, True)).links
    (refuted,) = [link for link in links if link.claim == "c1.other is a value the program holds"]
    assert refuted.verdict == "refuted"
    assert refuted.counterexample == ({"name": ""},)


@given(
    st.recursive(
        st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(),
        lambda items: st.lists(items) | st.dictionaries(st.text(), items),
        max_leaves=6,
    )
)
def test_every_json_value_shrinks_to_values_of_its_kind_nearer_the_smallest(value: Any) -> None:
    for candidate in _smaller(value):
        assert type(candidate) is type(value)
        assert candidate != value
        if isinstance(value, int | float):
            assert abs(candidate) < abs(value)
            assert candidate == 0 or (candidate > 0) == (value > 0)


@pytest.mark.parametrize(
    ("held", "taken"),
    [
        ({"type": "integer", "minimum": 5}, {"type": "integer", "minimum": 1000}),
        ({"type": "integer", "minimum": -1000}, {"type": "integer", "minimum": -3, "maximum": 3}),
    ],
    ids=["zero-is-held-by-none", "zero-is-taken"],
)
def test_a_shrunk_witness_stays_held_and_outside(
    held: dict[str, Any], taken: dict[str, Any]
) -> None:
    # Zero is no value the program holds in the first, and one the argument takes in the second:
    # the witness shrinks toward it and stops short, a value that still refutes.
    reader = read_contract(
        {
            "name": "read",
            "inputSchema": {"type": "object", "properties": {}},
            "outputSchema": {"type": "object", "properties": {"v": held}, "required": ["v"]},
        }
    )
    taker = read_contract(
        {
            "name": "take",
            "inputSchema": {"type": "object", "properties": {"v": taken}, "required": ["v"]},
        }
    )
    program = read_program("c1 = read()\nc2 = take(v=c1.v)\nreturn c2\n")
    links = verify(program, {"read": reader, "take": taker}, read_task("t", {}, True)).links
    (refuted,) = [link for link in links if link.claim == "c1.v <= take.v"]
    (found,) = refuted.counterexample
    assert found != 0
    assert compile_schema(held).is_valid(found)
    assert not compile_schema(taken).is_valid(found)


def _looked(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the tools of a program that looks at ``schema`` and tests it."""
    look = {"name": "look", "inputSchema": {"type": "object"}, "outputSchema": schema}
    return {"look": read_contract(look)}


_LOOKED: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"enum": ["open", "shipped", "lost"]},
        "at": {
            "type": "object",
            "properties": {"total": {"type": "integer", "minimum": 0, "maximum": 9}},
            "required": ["total"],
        },
        "note": {"type": "string", "pattern": "^n"},
    },
    "required": ["status", "at", "note"],
}
_TESTED = (
    "c1 = look()\n"
    'if c1.status in ("open", "shipped"):\n'
    "    if c1.at.total in (3, 9):\n"
    "        return c1\n"
    "    return c1\n"
    'if c1.status in ("open",):\n'
    "    return c1\n"
    "if c1.at.total in (10,):\n"
    "    return c1\n"
    "return c1\n"
)


@pytest.mark.parametrize(
    "more",
    [
        {},
        {"allOf": [{"properties": {"status": {"const": "lost"}}}]},
        {"not": {"properties": {"status": {"const": "lost"}}}},
        {"additionalProperties": False},
        {"dependentRequired": {"status": ["more"]}},
        {"oneOf": [{"properties": {"status": {"const": "lost"}}}, {"required": ["more"]}]},
        {"minProperties": 9, "additionalProperties": False},
    ],
    ids=["plain", "allOf", "not", "closed", "dependent", "oneOf", "empty"],
)
def test_the_returns_reached_are_the_ones_the_whole_belief_shows(
    more: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A return shown field by field is one the belief asked whole reaches, and no other is."""
    tools, task = _looked({**_LOOKED, **more}), read_task("look", {}, True)
    program = read_program(_TESTED)
    shown = verify(program, tools, task)
    monkeypatch.setattr(verifier._Walk, "holds", lambda *_: False)
    assert verify(program, tools, task) == shown


def test_a_return_its_fields_show_asks_no_whole_belief(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole belief of a result of many fields is dear, and its tested fields are not."""
    asked = []
    belief = verifier._Walk.belief
    monkeypatch.setattr(
        verifier._Walk, "belief", lambda self, *a: asked.append(a[1]) or belief(self, *a)
    )
    task = read_task("look", {}, True)
    program = read_program('c1 = look()\nif c1.status in ("open",):\n    return c1\nreturn c1\n')
    assert verify(program, _looked(_LOOKED), task).returns == ("then", "else")
    assert asked == []
    tied = {**_LOOKED, "allOf": [{"properties": {"status": {"const": "lost"}}}]}
    assert verify(program, _looked(tied), task).returns == ("else",)
    assert asked == ["c1", "c1"]


def test_a_value_tested_whole_is_asked_whole() -> None:
    """A test on the result itself narrows every field, so no field shows a return reached."""
    tools, task = _looked(_LOOKED), read_task("look", {}, True)
    inner = '    if c1.status in ("lost",):\n        return c1\n'
    source = f'c1 = look()\nif "gone" in c1:\n{inner}return c1\n'
    assert verify(read_program(source), tools, task).returns == ("then.then", "then.else", "else")
    shut = _looked({**_LOOKED, "additionalProperties": False})
    assert verify(read_program(source), shut, task).returns == ("else",)


@pytest.mark.parametrize(("first", "second"), [("look", "tied"), ("tied", "look")])
def test_a_return_is_reached_where_every_value_tested_is(first: str, second: str) -> None:
    """One value shown to meet its tests says nothing of another, whichever is read first."""
    tied = {
        "name": "tied",
        "inputSchema": {"type": "object"},
        "outputSchema": {**_LOOKED, "allOf": [{"properties": {"status": {"const": "lost"}}}]},
    }
    tools = {**_looked(_LOOKED), "tied": read_contract(tied)}
    program = read_program(
        f'c1 = {first}()\nc2 = {second}()\nif c1.status in ("open",):\n'
        '    if c2.status in ("open",):\n        return c1\nreturn c1\n'
    )
    reached = verify(program, tools, read_task("look", {}, True)).returns
    assert "then.then" not in reached
    assert "else" in reached


def test_a_record_tested_for_a_key_is_asked_whole() -> None:
    """A record a test narrows is met by the value tried there, and no member stands for it."""
    task = read_task("look", {}, True)
    program = read_program('c1 = look()\nif "gone" in c1.at:\n    return c1\nreturn c1\n')
    assert verify(program, _looked(_LOOKED), task).returns == ("then", "else")
    at = {**_LOOKED["properties"]["at"], "additionalProperties": False}
    shut = _looked({**_LOOKED, "properties": {**_LOOKED["properties"], "at": at}})
    assert list(shut["look"].shown())
    assert verify(program, shut, task).returns == ("else",)


def test_a_program_no_rule_reads_builds_no_record_and_draws_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A policy alone reads a plain call's record, and a witness alone needs a drawn value."""

    def refused(*_: object, **__: object) -> None:
        raise AssertionError

    task = read_task("look", {}, True)
    monkeypatch.setattr(verifier, "record_of", refused)
    monkeypatch.setattr(verifier.random, "Random", refused)
    assert verify(read_program("c1 = look()\nreturn c1\n"), _looked(_LOOKED), task).proven


def _passing(meta: dict[str, Any], **related: object) -> dict[str, Any]:
    """Return a tool reading a ``meta`` and one taking it beside a text, as ``related`` relates."""
    read: dict[str, Any] = {
        "name": "read",
        "inputSchema": {"type": "object"},
        "outputSchema": {
            "type": "object",
            "properties": {"meta": {"type": "object", **meta}},
            "required": ["meta"],
        },
    }
    send: dict[str, Any] = {
        "name": "send",
        "inputSchema": {
            "type": "object",
            "properties": {"meta": {"type": "object"}, "text": {"type": "string"}},
            "required": ["meta", "text"],
            **related,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    }
    return {spec["name"]: read_contract(spec) for spec in (read, send)}


def _flat(*keys: str) -> dict[str, Any]:
    """Return what relates the two: the text is ``go``, or ``meta`` holds no key but ``keys``."""
    shut = {"properties": dict.fromkeys(keys, True), "additionalProperties": False}
    return {"anyOf": [{"properties": {"text": {"const": "go"}}}, {"properties": {"meta": shut}}]}


ONE_OF = {"oneOf": [{"required": ["id"]}, {"required": ["sku"]}]}


@pytest.mark.parametrize(
    ("meta", "keys", "value"),
    [
        (ONE_OF, ["id", "sku"], {"id": 1, "more": 1}),
        ({"not": {"required": ["old"]}}, ["id"], {"more": 1}),
        ({"required": ["id?"]}, ["id?"], {"id?": 1, "more": 1}),
        ({"anyOf": ONE_OF["oneOf"]}, ["id", "sku"], {"id": 1, "more": 1}),
        ({}, ["id"], {"more": 1}),
    ],
    ids=["oneOf", "not", "key", "anyOf", "plain"],
)
def test_a_call_s_record_holds_each_argument_s_whole_set(
    meta: dict[str, Any], keys: list[str], value: dict[str, Any]
) -> None:
    """A value the argument may hold and the input schema refuses beside another is never proven."""
    tools = _passing(meta, **_flat(*keys))
    task = read_task("send", {"x": {"type": "string"}}, {"type": "boolean"})
    assert tools["read"].result("meta").values.is_valid(value)
    assert not tools["send"].accepts.is_valid({"meta": value, "text": "t"})
    program = "c1 = read()\nc2 = send(meta=c1.meta, text={})\nreturn c2.ok\n"
    assert not verify(read_program(program.format("x")), tools, task).proven
    assert verify(read_program(program.format('"go"')), tools, task).proven


@pytest.mark.parametrize(
    "meta",
    [ONE_OF, {"not": {"required": ["old"]}}, {"dependentRequired": {"id": ["sku"]}}],
    ids=["oneOf", "not", "dependent"],
)
def test_a_call_s_record_inside_its_input_schema_is_proven(meta: dict[str, Any]) -> None:
    """The input schema asks of the argument, beside the text, what the argument's own set holds."""
    asked = {"anyOf": [{"properties": {"text": {"const": "go"}}}, {"properties": {"meta": meta}}]}
    task = read_task("send", {"x": {"type": "string"}}, {"type": "boolean"})
    program = read_program("c1 = read()\nc2 = send(meta=c1.meta, text=x)\nreturn c2.ok\n")
    assert verify(program, _passing(meta, **asked), task).proven


def test_a_program_verified_again_asks_no_difference_again() -> None:
    """What a test leaves in a set and out of it, and each inclusion, is asked of its sets once."""
    goal = {"enum": ["open", "shipped", "lost"]}
    task = read_task("look", {}, goal)
    program = read_program(
        'c1 = look()\nif c1.status in ("open",):\n    return c1.status\n'
        'if c1.status in ("lost",):\n    return c1.status\nreturn c1.status\n'
    )
    first = verify(program, _looked(_LOOKED), task)
    assert first.proven
    asked = [kept.cache_info() for kept in (sides, inside)]
    assert verify(program, _looked(_LOOKED), task) == first
    again = [kept.cache_info() for kept in (sides, inside)]
    assert [info.misses for info in again] == [info.misses for info in asked]
    assert [new.hits - old.hits for new, old in zip(again, asked, strict=True)] == [2, 3]


@pytest.mark.parametrize(
    "beside",
    [
        {"type": "integer", "multipleOf": 2**60 + 1},
        {"type": "array", "uniqueItems": True},
        {"type": "integer"},
    ],
    ids=["multiple", "unique", "plain"],
)
def test_a_value_found_refutes_a_link_on_a_field_beside_one_a_predicate_reads(
    beside: dict[str, Any],
) -> None:
    """A whole value holding the field's outside its target is a proof, whatever reads the rest."""
    fields = {"a": beside, "b": {"type": "integer", "minimum": 0}}
    look = {"type": "object", "properties": fields, "required": ["a", "b"]}
    task = read_task("look", {}, {"type": "integer", "maximum": 5})
    (link,) = verify(read_program("c1 = look()\nreturn c1.b\n"), _looked(look), task).links
    assert (link.verdict, link.counterexample) == ("refuted", (6,))


def test_a_member_is_shown_by_a_value_found_and_refused_by_its_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The belief is asked whole of neither: a witness is many of them, each dear."""
    asked = []
    belief = verifier._Walk.belief
    monkeypatch.setattr(
        verifier._Walk, "belief", lambda self, *a: asked.append(a[1]) or belief(self, *a)
    )
    task = read_task("look", {}, {"const": "open"})
    source = (
        "c1 = look()\n"
        "if c1.at.total in (3, 9):\n    return c1.status\n"
        'if c1.status in ("lost",):\n    return c1.at.total\n'
        "return c1.note\n"
    )
    links = verify(read_program(source), _looked(_LOOKED), task).links
    assert [link.verdict for link in links] == ["refuted"] * 3
    assert all(link.counterexample for link in links)
    assert asked == []


def test_a_value_found_keeps_each_field_the_program_is_shown_with() -> None:
    """Two fields a schema ties hold their values together or not at all."""
    tied = {
        "type": "object",
        "properties": {"a": {"enum": [1, 2]}, "b": {"enum": [1, 2]}},
        "required": ["a", "b"],
        "oneOf": [{"properties": {"a": {"const": n}, "b": {"const": n}}} for n in (1, 2)],
    }
    pay = {
        "name": "pay",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"enum": [1, 2]}, "b": {"enum": [1, 2]}},
            "required": ["a", "b"],
            "not": {"properties": {"a": {"const": 1}, "b": {"const": 2}}, "required": ["a", "b"]},
        },
        "outputSchema": {"type": "object"},
    }
    tools = {**_looked(tied), "pay": read_contract(pay)}
    program = read_program("c1 = look()\nc2 = pay(a=c1.a, b=c1.b)\nreturn c2\n")
    (record,) = [
        link
        for link in verify(program, tools, read_task("pay", {}, True)).links
        if link.claim.startswith("the arguments")
    ]
    assert record.counterexample == ()
    assert record.verdict == "undecided"


def test_a_slot_keeps_the_values_a_seed_draws_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every verification of a contract draws them first, so they are drawn once a seed."""
    draws = []
    draw = verifier.Sampler.draw

    def counted(self: verifier.Sampler, schema: Any, tries: int) -> list[Any]:
        if tries == verifier.DRAWS:
            draws.append(schema)
        return draw(self, schema, tries)

    monkeypatch.setattr(verifier.Sampler, "draw", counted)
    task = read_task("look", {}, {"type": "boolean"})
    program = read_program(
        "c1 = look()\nif c1.at.total in (3, 9):\n    return c1.status\nreturn c1.note\n"
    )
    tools = _looked(_LOOKED)
    once = verify(program, tools, task)
    assert [len(link.counterexample) for link in once.links] == [1, 1]
    assert len(draws) == 2
    assert verify(program, tools, task).links == once.links
    assert len(draws) == 3
    verify(program, tools, task, seed=7)
    assert verify(program, tools, task).links == once.links
    assert verify(program, _looked(_LOOKED), task).links == once.links


@pytest.mark.parametrize(
    ("source", "inside"),
    [
        ("c1 = look()\nreturn c1.at\n", "witness"),
        ("c1 = look()\nreturn c1.more\n", "lacking"),
        ("c1 = look()\nc2 = put(at=c1.at, like=c1.like)\nreturn c2\n", "record"),
    ],
    ids=["value", "lacking", "record"],
)
def test_a_witness_a_caller_writes_is_not_the_slots(source: str, inside: str) -> None:
    """A link's witness is its own value: writing one leaves the next verification's as it was."""
    at = {"type": "object", "properties": {"n": {"const": 1}}, "required": ["n"]}
    looked = {
        "type": "object",
        "properties": {"at": at, "like": at, "more": {"type": "integer"}},
        "required": ["at", "like"],
    }
    put = {
        "name": "put",
        "inputSchema": {
            "type": "object",
            "properties": {"at": at, "like": at},
            "required": ["at", "like"],
            "not": {"required": ["at"], "properties": {"at": {"required": ["n"]}}},
        },
        "outputSchema": {"type": "string"},
    }
    tools = {**_looked(looked), "put": read_contract(put)}
    program, task = read_program(source), read_task("look", {}, {"type": "string"})

    def witness() -> Any:
        (link,) = [link for link in verify(program, tools, task).links if link.counterexample]
        return link.counterexample[0]

    first = witness()
    kept = copy.deepcopy(first)
    written = first if inside == "witness" else first["at"]
    written["extra"] = "written by the caller"
    assert witness() == kept


def test_a_slot_s_kept_draws_are_those_a_seeded_stream_draws() -> None:
    """By any seed and count in turn, and what the stream draws next is what it drew next."""
    slot = _looked(_LOOKED)["look"].result("note")
    for seed, tries in ((0, 4), (0, 9), (7, 9), (0, 4), (0, 4)):
        rng = random.Random(seed)
        values, state = slot.stream(seed, tries)
        assert list(values) == verifier.Sampler(slot.root, rng).draw(slot.schema, tries)
        assert state == rng.getstate()


def test_a_witness_is_the_one_a_walk_s_stream_draws(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first draw of a walk is kept and the next goes on from where it left the stream."""
    monkeypatch.setattr(verifier._Walk, "shrunk", lambda *a: a[-1])
    code = {"type": "string", "pattern": "^[a-z]{8}$"}
    fields = {"code": code, "ok": {"type": "boolean"}}
    tools = _looked({"type": "object", "properties": fields, "required": ["code", "ok"]})
    program = read_program(
        "c1 = look()\nif c1.ok in (True,):\n    return c1.code\nreturn c1.code\n"
    )
    rng = random.Random(0)
    drawn = [verifier.Sampler(code, rng).draw(code, verifier.DRAWS)[0] for _ in range(2)]
    assert drawn[0] != drawn[1]
    for _ in range(2):
        links = verify(program, tools, read_task("look", {}, {"type": "boolean"})).links
        assert [link.counterexample for link in links] == [(value,) for value in drawn]


def test_a_value_drawn_for_an_object_is_no_witness_where_a_test_inside_it_refuses_it() -> None:
    """Moving the tested field into its set makes another object, so it shows nothing of this."""
    program = read_program("c1 = look()\nif c1.at.total in (3, 9):\n    return c1.at\nstop(c1)\n")
    tools = _looked(_LOOKED)

    def goal(total: dict[str, Any]) -> Link:
        output = {"type": "object", "properties": {"total": total}}
        return verify(program, tools, read_task("look", {}, output)).links[0]

    held, narrower = goal({"enum": [3, 9]}), goal({"const": 3})
    assert (held.verdict, held.counterexample) == ("proven", ())
    assert (narrower.verdict, narrower.counterexample) == ("refuted", ({"total": 9},))
