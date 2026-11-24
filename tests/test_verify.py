from __future__ import annotations

from valgebra_planning._valgebra import Validator
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.program import Block, Branch, Call, Ref, Return, Stop, render, steps
from valgebra_planning.run import run
from valgebra_planning.verify import Link, verify

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
