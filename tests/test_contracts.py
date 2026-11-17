from __future__ import annotations

import pytest

from valgebra_planning._valgebra import Unsupported
from valgebra_planning.contracts import RESULT_DEPTH, read_contract, read_task

LOOKUP = {
    "name": "find_order",
    "inputSchema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "pattern": "^ord_[0-9]+$"},
            "verbose": {"type": "boolean"},
        },
        "required": ["order_id"],
    },
    "outputSchema": {
        "type": "object",
        "properties": {
            "order": {
                "type": "object",
                "properties": {
                    "status": {"enum": ["open", "shipped"]},
                    "total": {"type": "integer", "minimum": 0},
                    "note": {"type": "string"},
                },
                "required": ["status", "total"],
            },
            "trace": {"type": "string"},
        },
        "required": ["order"],
    },
}


def test_each_argument_is_a_slot_and_says_whether_it_is_required() -> None:
    contract = read_contract(LOOKUP)
    order_id = contract.argument("order_id")
    assert order_id.required
    assert order_id.name == "find_order.order_id"
    assert order_id.values.is_valid("ord_12")
    assert not order_id.values.is_valid("12")
    assert not contract.argument("verbose").required
    with pytest.raises(KeyError):
        contract.argument("absent")


def test_a_result_slot_is_each_guaranteed_field_and_the_whole_result() -> None:
    contract = read_contract(LOOKUP)
    paths = {slot.path for slot in contract.results}
    assert paths == {(), ("order",), ("order", "status"), ("order", "total")}
    assert contract.result("order", "status").values.is_valid("open")
    assert not contract.result("order", "total").values.is_valid(-1)
    # An optional field is not guaranteed, so no slot stands for it.
    with pytest.raises(KeyError):
        contract.result("trace")


def test_a_result_is_read_to_a_bounded_depth() -> None:
    def nested(depth: int) -> object:
        if depth == 0:
            return {"type": "integer"}
        return {"type": "object", "properties": {"x": nested(depth - 1)}, "required": ["x"]}

    spec = {"name": "deep", "inputSchema": {"type": "object"}, "outputSchema": nested(6)}
    longest = max(len(slot.path) for slot in read_contract(spec).results)
    assert longest == RESULT_DEPTH


def test_a_tool_with_no_output_schema_guarantees_no_result() -> None:
    spec = {"name": "notify", "inputSchema": {"type": "object", "properties": {}}}
    contract = read_contract(spec)
    assert contract.results == ()
    assert contract.returns is None


def test_a_reference_resolves_where_the_tool_wrote_it() -> None:
    spec = {
        "name": "ref",
        "inputSchema": {
            "$ref": "#/$defs/args",
            "$defs": {
                "args": {
                    "type": "object",
                    "properties": {"id": {"$ref": "#/$defs/id"}},
                    "required": ["id"],
                },
                "id": {"type": "string", "maxLength": 3},
            },
        },
    }
    slot = read_contract(spec).argument("id")
    assert slot.values.is_valid("abc")
    assert not slot.values.is_valid("abcd")


def test_an_older_draft_is_read_as_it_means() -> None:
    spec = {
        "name": "old",
        "inputSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {"pair": {"type": "array", "items": [{"type": "integer"}]}},
        },
    }
    pair = read_contract(spec).argument("pair").values
    assert pair.is_valid([1, "anything after the first"])
    assert not pair.is_valid(["a"])


def test_an_input_schema_relating_arguments_is_not_plain() -> None:
    assert read_contract(LOOKUP).plain
    related = {
        "name": "pay",
        "inputSchema": {
            "type": "object",
            "properties": {"card": {"type": "string"}, "iban": {"type": "string"}},
            "oneOf": [{"required": ["card"]}, {"required": ["iban"]}],
        },
    }
    contract = read_contract(related)
    assert not contract.plain
    assert contract.accepts.is_valid({"card": "x"})
    assert not contract.accepts.is_valid({"card": "x", "iban": "y"})


def test_a_required_name_no_property_declares_still_has_a_slot() -> None:
    spec = {"name": "bare", "inputSchema": {"type": "object", "required": ["anything"]}}
    slot = read_contract(spec).argument("anything")
    assert slot.required
    assert slot.values.is_valid([1, {"a": None}])


def test_a_tool_the_companion_refuses_is_refused() -> None:
    with pytest.raises(Unsupported):
        read_contract({"name": "no_input"})
    with pytest.raises(Unsupported):
        read_contract({"name": "scalar", "inputSchema": {"type": "string"}})


def test_a_task_holds_its_inputs_as_slots_and_its_goal_as_a_set() -> None:
    task = read_task(
        "refund",
        {
            "email": {"type": "string", "format": "email"},
            "order": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
        {"type": "string", "pattern": "^re_"},
    )
    assert {slot.path for slot in task.held["email"]} == {()}
    assert {slot.path for slot in task.held["order"]} == {(), ("id",)}
    assert task.held["email"][0].values.is_valid("a@b")
    assert task.goal.is_valid("re_1")
    assert not task.goal.is_valid("ch_1")
    assert task.goal_schema == {"type": "string", "pattern": "^re_"}


@pytest.mark.parametrize(
    ("output", "member", "outsider"),
    [
        ({"type": "array", "items": {"type": "string"}}, ["a"], {"a": 1}),
        ({"type": "string", "minLength": 1}, "a", ""),
    ],
    ids=["array", "string"],
)
def test_an_output_that_is_not_an_object_is_one_result_slot(
    output: dict[str, object], member: object, outsider: object
) -> None:
    # MCP 2026-07-28 admits any JSON value as a result: the whole value is the one slot.
    contract = read_contract(
        {"name": "t", "inputSchema": {"type": "object", "properties": {}}, "outputSchema": output}
    )
    (slot,) = contract.results
    assert slot.path == ()
    assert slot.values.is_valid(member)
    assert not slot.values.is_valid(outsider)
