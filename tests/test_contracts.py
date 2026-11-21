from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from valgebra_planning._valgebra import Unsupported, anything, compile_schema
from valgebra_planning.contracts import RESULT_DEPTH, read_contract, read_task, record_of
from valgebra_planning.critic import critique, read_program
from valgebra_planning.library import read_library
from valgebra_planning.planner import plan
from valgebra_planning.run import run
from valgebra_planning.verify import verify

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


#: A task whose input bears a tool's name: its value would read as that tool's result.
POSING = read_task("posing", {"find_order": {"type": "string"}}, {"type": "string"})
POSED = {"find_order": read_contract(LOOKUP)}


@pytest.mark.parametrize(
    "meet",
    [
        lambda: verify(read_program("return find_order\n"), POSED, POSING),
        lambda: critique("return find_order\n", POSED, POSING),
        lambda: plan(POSED, POSING),
        lambda: run(read_program("return find_order\n"), POSED, POSING, {"find_order": "x"}, {}),
    ],
    ids=["verify", "critique", "plan", "run"],
)
def test_a_task_naming_an_input_as_a_tool_is_refused_wherever_it_meets_the_tools(
    meet: Callable[[], object],
) -> None:
    with pytest.raises(ValueError, match="names an input as a tool: find_order"):
        meet()


def test_a_library_whose_task_names_an_input_as_a_tool_is_refused(tmp_path: Path) -> None:
    (tmp_path / "tools.json").write_text(json.dumps({"tools": [LOOKUP]}))
    task = {"name": "posing", "inputs": {"find_order": {"type": "string"}}, "goal": {}}
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [task]}))
    with pytest.raises(ValueError, match="names an input as a tool: find_order"):
        read_library(tmp_path)


#: Key names a record may hold, a trailing `?` among them, as a JSON key may carry one.
KEYS = st.text(alphabet="ab?", min_size=1, max_size=3)
INTEGER = compile_schema({"type": "integer"})


@settings(max_examples=300, deadline=None)
@given(
    typed=st.dictionaries(KEYS, st.booleans(), max_size=3),
    value=st.dictionaries(KEYS, st.one_of(st.integers(-2, 2), st.text(max_size=1)), max_size=4),
    closed=st.booleans(),
)
def test_a_record_holds_the_objects_carrying_each_key_as_written(
    *, typed: dict[str, bool], value: dict[str, object], closed: bool
) -> None:
    # Each field takes integers or anything; the record is read against its definition.
    fields = {key: INTEGER if integral else anything for key, integral in typed.items()}
    inside = all(
        key in value and (not integral or isinstance(value[key], int))
        for key, integral in typed.items()
    ) and (not closed or set(value) <= set(typed))
    assert record_of(fields, closed=closed).is_valid(value) == inside


def _embedded(inner: dict[str, object], *, required: bool) -> dict[str, Any]:
    """Return an output whose `a` is a resource of its own, its `T` apart from the root's."""
    key: dict[str, object] = {"required": ["k"]} if required else {}
    return {
        "$id": "https://example.com/root",
        "type": "object",
        "$defs": {"T": {"type": "object", "properties": {"k": {"type": "string"}}, **key}},
        "properties": {
            "a": {
                "$id": "https://example.com/inner",
                "$defs": {"T": {"type": "object", "properties": {"k": {"type": "integer"}}, **key}},
                **inner,
            }
        },
        "required": ["a"],
    }


def _bound(output: object, source: str) -> dict[str, tuple[str, tuple[object, ...]]]:
    """Return each link of ``source``, binding `get`'s result to `use`, with its verdict."""
    tools = {
        "get": read_contract(
            {"name": "get", "inputSchema": {"type": "object"}, "outputSchema": output}
        ),
        "use": read_contract(
            {
                "name": "use",
                "inputSchema": {
                    "type": "object",
                    "properties": {"k": {"type": "string"}},
                    "required": ["k"],
                },
            }
        ),
    }
    links = verify(read_program(source), tools, read_task("use", {}, True)).links
    return {link.claim: (link.verdict, link.counterexample) for link in links}


def test_a_result_slot_the_whole_schema_refuses_is_no_slot() -> None:
    # The walk resolves `#/$defs/T` from the root, a string `k`; the resource's own is integer.
    output = _embedded({"$ref": "#/$defs/T"}, required=True)
    get = read_contract({"name": "get", "inputSchema": {"type": "object"}, "outputSchema": output})
    assert ("a", "k") not in [slot.path for slot in get.results]
    verdict, found = _bound(output, "r = get()\nu = use(k=r.a.k)\nreturn r")["r.a.k <= use.k"]
    assert verdict == "refuted"
    assert all(isinstance(value, int) for value in found)


def test_a_field_read_its_record_refuses_takes_any_value() -> None:
    output = _embedded({"$ref": "#/$defs/T"}, required=False)
    source = 'r = get()\nif "k" in r.a:\n    u = use(k=r.a.k)\n    return r\nreturn r'
    verdict, found = _bound(output, source)["r.a.k <= use.k"]
    assert verdict != "proven"
    assert all(isinstance(value, int) for value in found)


def test_a_loop_over_items_the_array_refuses_is_undecided() -> None:
    output = _embedded({"type": "array", "items": {"$ref": "#/$defs/T"}}, required=False)
    output["$defs"] = {"T": {"type": "string"}}
    output["properties"]["a"]["$defs"] = {"T": {"type": "integer"}}
    links = _bound(output, "r = get()\nfor k in r.a:\n    u = use(k=k)\nreturn r")
    assert links["r.a's items lie in its item schema"] == ("undecided", ())
