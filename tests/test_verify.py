from __future__ import annotations

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.program import Block, Branch, Call, Ref, Return, Stop, render, steps
from valgebra_planning.verify import verify

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
