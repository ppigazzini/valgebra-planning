from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from valgebra_planning._valgebra import (
    Unsupported,
    ValidationError,
    anything,
    compile_schema,
    complement,
    intersection,
    nothing,
)
from valgebra_planning.contracts import (
    RESULT_DEPTH,
    Contract,
    apart,
    contract_names,
    failures,
    field_slot,
    inside,
    listed,
    meet,
    read_contract,
    read_task,
    record_of,
    sides,
)
from valgebra_planning.critic import critique, read_program
from valgebra_planning.library import asserts_format, read_library, reads_closed
from valgebra_planning.planner import plan
from valgebra_planning.program import render
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
    (tmp_path / "tools.json").write_text(json.dumps({"tools": [LOOKUP]}), encoding="utf-8")
    task = {"name": "posing", "inputs": {"find_order": {"type": "string"}}, "goal": {}}
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
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


#: Field sets holding a record inside, each read otherwise were the records inside closed.
NESTED: list[dict[str, Any]] = [
    {"required": ["a"]},
    {"required": ["a?"]},
    {"not": {"required": ["a"]}},
    {"not": {"properties": {"a": {"type": "integer"}}}},
    {"not": {"maxProperties": 1}},
    {"oneOf": [{"required": ["a"]}, {"required": ["b"]}]},
    {"oneOf": [{"required": ["a"]}, {"properties": {"b": {"type": "integer"}}}]},
    {"if": {"required": ["a"]}, "then": {"const": {"a": 1}}},
    {"dependentRequired": {"a": ["b"]}},
    {"dependentSchemas": {"a": {"required": ["b"]}}},
    {"properties": {"a": {"type": "object", "not": {"required": ["b"]}}}, "required": ["a"]},
]
INNER = st.dictionaries(
    KEYS, st.one_of(st.integers(-1, 1), st.text(max_size=1), st.just({"b": 1})), max_size=3
)


@settings(max_examples=600, deadline=None)
@given(
    schema=st.sampled_from(NESTED),
    value=INNER,
    key=st.sampled_from(["f", "f?"]),
    beside=st.booleans(),
    closed=st.booleans(),
)
def test_a_record_s_field_lies_in_the_set_it_is_given(
    *, schema: dict[str, Any], value: dict[str, Any], key: str, beside: bool, closed: bool
) -> None:
    """A record says which keys it holds, and nothing of the records inside its fields' sets."""
    values = compile_schema({"type": "object", **schema})
    record = record_of({key: values, **({"g": INTEGER} if beside else {})}, closed=closed)
    held = {key: value, **({"g": 1} if beside else {})}
    assert record.is_valid(held) == values.is_valid(value)
    assert record.is_valid({**held, "more": 1}) == (not closed and values.is_valid(value))


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


def test_a_loop_over_items_the_walk_misreads_is_asked_of_the_array() -> None:
    # The walk resolves `T` from the document's root, a string; the array's own resource says
    # an integer. The walk's reading is dropped, and the array is asked: no item is a string.
    output = _embedded({"type": "array", "items": {"$ref": "#/$defs/T"}}, required=False)
    output["$defs"] = {"T": {"type": "string"}}
    output["properties"]["a"]["$defs"] = {"T": {"type": "integer"}}
    links = _bound(output, "r = get()\nfor k in r.a:\n    u = use(k=k)\nreturn r")
    verdict, found = links["k <= use.k"]
    assert verdict == "refuted"
    assert all(isinstance(value, int) for value in found)
    assert links["return r <= goal"] == ("proven", ())


ASKED = {"name": "posing", "inputs": {"id": {"type": "string"}}, "goal": {}}


@pytest.mark.parametrize(
    ("files", "refused", "match"),
    [
        ({"tasks.json": {"tasks": [{**ASKED, "dept": 9}]}}, ValueError, "'dept' is no key"),
        ({"tasks.json": {"tasks": [{**ASKED, "depth": 0}]}}, ValueError, "'depth' is a positive"),
        ({"tasks.json": {"tasks": [{**ASKED, "depth": "9"}]}}, ValueError, "'depth' is a positive"),
        ({"tasks.json": {"task": [ASKED]}}, ValueError, "tasks.json: 'task' is no key"),
        ({"tasks.json": {"tasks": [{**ASKED, "errors": "yes"}]}}, TypeError, "'errors' is true"),
        ({"tools.json": {"tools": [LOOKUP], "trusted": "true"}}, TypeError, "'trusted' is true"),
        ({"policies.json": {"policy": []}}, ValueError, "policies.json: 'policy' is no key"),
        ({"tasks.json": {"tasks": [{**ASKED, "goals": [{}]}]}}, ValueError, "one of the two"),
        (
            {"tasks.json": {"tasks": [{"name": "p", "inputs": {}, "goals": []}]}},
            TypeError,
            "`goals` is a list",
        ),
    ],
    ids=[
        "task-key",
        "depth-0",
        "depth-str",
        "file-key",
        "errors",
        "trusted",
        "policies-file-key",
        "goal-and-goals",
        "no-goals",
    ],
)
def test_a_library_file_holding_a_key_no_reader_reads_is_refused_by_name(
    tmp_path: Path, files: dict[str, object], refused: type[Exception], match: str
) -> None:
    written = {"tools.json": {"tools": [LOOKUP]}, "tasks.json": {"tasks": [ASKED]}} | files
    for name, document in written.items():
        (tmp_path / name).write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(refused, match=match):
        read_library(tmp_path)


def test_a_task_s_goals_are_read_in_the_order_it_prefers_them(tmp_path: Path) -> None:
    first, second = {"type": "integer"}, {"type": "string"}
    ordered = {"name": "p", "inputs": {}, "goals": [first, second]}
    (tmp_path / "tools.json").write_text(json.dumps({"tools": [LOOKUP]}), encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [ordered]}), encoding="utf-8")
    (problem,) = read_library(tmp_path).problems
    assert [schema for _, schema in problem.task.preferred] == [first, second]
    assert problem.task.goal.is_valid(1)
    assert problem.task.goal.is_valid("a")
    assert not problem.task.goal.is_valid(None)


def test_a_task_s_depth_is_read_with_the_task(tmp_path: Path) -> None:
    (tmp_path / "tools.json").write_text(json.dumps({"tools": [LOOKUP]}), encoding="utf-8")
    (tmp_path / "tasks.json").write_text(
        json.dumps({"tasks": [{**ASKED, "depth": 9}, ASKED]}), encoding="utf-8"
    )
    first, second = read_library(tmp_path).problems
    assert (first.depth, second.depth) == (9, None)


UUID = {"type": "string", "format": "uuid"}
GRASP_BY_ID = {
    "name": "grasp",
    "inputSchema": {"type": "object", "properties": {"object_id": UUID}, "required": ["object_id"]},
    "outputSchema": {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "held": {"type": "object", "properties": {"id": UUID}},
        },
        "required": ["ok", "held"],
    },
}
HEARD = {
    "name": "grasp what was heard",
    "inputs": {"heard": {"type": "string"}},
    "goal": {"type": "boolean"},
}


def test_a_format_is_read_as_an_assertion_in_every_set_a_contract_holds_where_asked() -> None:
    annotated, asserted = read_contract(GRASP_BY_ID), read_contract(GRASP_BY_ID, assert_format=True)
    spoken = {"object_id": "the red cup please"}
    assert annotated.accepts.is_valid(spoken)
    assert not asserted.accepts.is_valid(spoken)
    assert not asserted.argument("object_id").values.is_valid("the red cup please")
    # A field read from a result's slot later is read alike.
    held = next(slot for slot in asserted.results if slot.path == ("held",))
    assert held.asserted
    assert not field_slot(held, "id").values.is_valid("the red cup please")
    assert field_slot(
        next(s for s in annotated.results if s.path == ("held",)), "id"
    ).values.is_valid("the red cup please")


def test_a_library_asserting_format_tests_free_text_before_it_fills_an_id(tmp_path: Path) -> None:
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [HEARD]}), encoding="utf-8")
    (tmp_path / "tools.json").write_text(json.dumps({"tools": [GRASP_BY_ID]}), encoding="utf-8")
    assert not asserts_format(tmp_path)
    loose = read_library(tmp_path)
    unguarded = plan(loose.tools, loose.problems[0].task)
    assert unguarded.strong
    assert unguarded.program is not None
    assert render(unguarded.program) == "c1 = grasp(object_id=heard)\nreturn c1.ok"
    (tmp_path / "tools.json").write_text(
        json.dumps({"tools": [GRASP_BY_ID], "assertFormat": True}), encoding="utf-8"
    )
    assert asserts_format(tmp_path)
    strict = read_library(tmp_path)
    guarded = plan(strict.tools, strict.problems[0].task)
    assert not guarded.strong
    assert guarded.program is not None
    assert render(guarded.program).startswith("if heard in grasp.object_id:\n")


def test_a_live_server_s_tools_are_trusted_where_the_library_says_so(tmp_path: Path) -> None:
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [ASKED]}), encoding="utf-8")
    live = {"find_order": read_contract(LOOKUP)}
    assert not read_library(tmp_path, live).tools["find_order"].trusted
    (tmp_path / "tools.json").write_text(json.dumps({"trusted": True}), encoding="utf-8")
    assert read_library(tmp_path, live).tools["find_order"].trusted


DETECTED_ID = {"type": "string", "pattern": "^obj_[0-9]+$"}
GRASPED_ID = {"type": "string", "pattern": "^grs_[0-9]+$"}
METERS = {
    "type": "object",
    "properties": {
        "unit": {"const": "m"},
        "value": {"type": "number", "minimum": 0, "maximum": 20},
    },
    "required": ["unit", "value"],
}


def _listed(name: str, arguments: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "inputSchema": {"type": "object", "properties": arguments, "required": [*arguments]},
        "outputSchema": {"type": "object", "properties": result, "required": [*result]},
        "annotations": {"readOnlyHint": True},
    }


DETECTED = _listed(
    "detect_object",
    {"name": {"type": "string"}},
    {"object_id": DETECTED_ID, "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
)
POSE_SPEC = _listed("estimate_pose", {"object_id": DETECTED_ID}, {"distance": METERS})
REACHED = {
    **METERS,
    "properties": {**METERS["properties"], "value": {"minimum": 0.2, "maximum": 0.6}},
}
PICKED = _listed("grasp", {"object_id": DETECTED_ID, "distance": REACHED}, {"grasp_id": GRASPED_ID})
FOUND = {"object_id": "obj_1", "confidence": 0.5}


def test_a_result_read_closed_holds_no_key_its_schema_does_not_declare() -> None:
    shut = read_contract(DETECTED, closed_results=True)
    assert shut.returns is not None
    assert shut.declared is not None
    assert shut.returns.is_valid(FOUND)
    assert not shut.returns.is_valid({**FOUND, "unit": "m"})
    assert shut.declared.is_valid({**FOUND, "unit": "m"})
    assert shut.result().values.is_valid(FOUND)
    assert not shut.result().values.is_valid({**FOUND, "unit": "m"})
    # A field keeps the set its record declares.
    assert shut.result("confidence").schema == read_contract(DETECTED).result("confidence").schema
    assert read_contract(DETECTED).declared is None


def test_an_inherited_result_read_closed_admits_the_server_s_result() -> None:
    base = {"type": "object", "properties": {"object_id": DETECTED_ID}, "required": ["object_id"]}
    inherited = {
        "$defs": {"Base": base},
        "allOf": [{"$ref": "#/$defs/Base"}],
        "properties": {"confidence": {"type": "number"}},
        "required": ["confidence"],
    }
    shut = read_contract({**DETECTED, "outputSchema": inherited}, closed_results=True)
    assert shut.returns is not None
    assert shut.returns.is_valid(FOUND)
    assert not shut.returns.is_valid({**FOUND, "unit": "m"})
    assert shut.returns.relation_to(nothing) == "not_subset"
    # `additionalProperties` sees no property the `allOf` declares, and empties the result.
    adjacent = compile_schema({**inherited, "additionalProperties": False})
    assert adjacent.relation_to(nothing) == "subset"


def test_a_whole_result_read_closed_fills_no_argument_its_declared_keys_miss() -> None:
    task = read_task("pick", {"name": {"type": "string"}}, GRASPED_ID)
    listing = [DETECTED, POSE_SPEC, PICKED]
    loose = {c.name: c for c in map(read_contract, listing)}
    found = plan(loose, task)
    assert found.program is not None
    assert "distance=c1)" in render(found.program)
    shut = {c.name: c for c in (read_contract(t, closed_results=True) for t in listing)}
    found = plan(shut, task)
    assert found.program is not None
    assert render(found.program) == (
        "c1 = detect_object(name=name)\n"
        "c2 = estimate_pose(object_id=c1.object_id)\n"
        "if c2.distance in grasp.distance:\n"
        "    c3 = grasp(object_id=c1.object_id, distance=c2.distance)\n"
        "    return c3.grasp_id\n"
        "else:\n"
        "    stop(c2.distance)"
    )
    assert "c2.distance -> grasp.distance, from the output of estimate_pose read closed" in (
        found.assumed
    )
    links = verify(found.program, shut, task).links
    assert all(link.verdict == "proven" for link in links)
    bound = [link for link in links if link.claim.startswith("c2.distance <=")]
    assert bound
    assert all(link.assumed == ("the output of estimate_pose read closed",) for link in bound)


def test_a_library_reads_results_closed_where_it_says_so(tmp_path: Path) -> None:
    (tmp_path / "tools.json").write_text(json.dumps({"tools": [DETECTED]}), encoding="utf-8")
    assert not reads_closed(tmp_path)
    assert read_library(tmp_path).tools["detect_object"].declared is None
    (tmp_path / "tools.json").write_text(
        json.dumps({"tools": [DETECTED], "closedResults": True}), encoding="utf-8"
    )
    assert reads_closed(tmp_path)
    assert read_library(tmp_path).tools["detect_object"].declared is not None
    shut = {"detect_object": read_contract(DETECTED, closed_results=True)}
    assert read_library(tmp_path, shut).tools["detect_object"].declared is not None
    with pytest.raises(ValueError, match="these tools were read open: detect_object"):
        read_library(tmp_path, {"detect_object": read_contract(DETECTED)})


def test_a_result_the_companion_cannot_close_is_refused_naming_its_tool() -> None:
    wide = {
        "anyOf": [
            {"type": "object", "properties": {f"k{i}": {"type": "integer"}}, "required": [f"k{i}"]}
            for i in range(5)
        ]
    }
    read_contract({**DETECTED, "outputSchema": wide})
    with pytest.raises(Unsupported, match="tool 'detect_object': its output schema read closed"):
        read_contract({**DETECTED, "outputSchema": wide}, closed_results=True)


def test_an_argument_record_over_a_result_read_closed_names_the_assumption() -> None:
    related = {
        **PICKED,
        "inputSchema": {**PICKED["inputSchema"], "dependentRequired": {"distance": ["object_id"]}},
    }
    task = read_task("pick", {"name": {"type": "string"}}, GRASPED_ID)
    source = (
        "c1 = detect_object(name=name)\n"
        "c2 = estimate_pose(object_id=c1.object_id)\n"
        "if c2.distance in grasp.distance:\n"
        "    c3 = grasp(object_id=c1.object_id, distance=c2.distance)\n"
        "    return c3.grasp_id\n"
        "stop(c2.distance)\n"
    )
    for closing, assumed in ((False, ()), (True, ("the output of detect_object read closed",))):
        listed = [read_contract(t, closed_results=closing) for t in (DETECTED, POSE_SPEC, related)]
        links = verify(read_program(source), {c.name: c for c in listed}, task).links
        (record,) = [link for link in links if link.claim.startswith("the arguments of grasp")]
        assert record.verdict == "proven"
        assert record.assumed[:1] == assumed


def test_a_required_argument_only_additional_properties_bounds_takes_its_set() -> None:
    """A required name no property declares lies in `additionalProperties` (Core, 10.3.2.3).

    It took any value, so a speed over the cap was proven to fill it; read from
    `additionalProperties`, it is refuted. Beside `patternProperties` the name may match a
    pattern, and the contract is no plain one, so the record link holds the whole.
    """
    capped = {"type": "number", "maximum": 2}
    root = {
        "type": "object",
        "properties": {"joint": {"type": "string"}},
        "required": ["joint", "speed"],
        "additionalProperties": capped,
    }
    done = {"type": "object", "properties": {"done": {"type": "boolean"}}, "required": ["done"]}
    move = read_contract({"name": "move_joint", "inputSchema": root, "outputSchema": done})
    assert move.argument("speed").schema == capped
    speed = read_contract(
        {
            "name": "read_speed",
            "inputSchema": {"type": "object"},
            "outputSchema": {
                "type": "object",
                "properties": {"v": {"type": "number"}},
                "required": ["v"],
            },
        }
    )
    checked = verify(
        read_program("c0 = read_speed()\nc1 = move_joint(joint=j, speed=c0.v)\nreturn c1.done\n"),
        {"move_joint": move, "read_speed": speed},
        read_task("t", {"j": {"type": "string"}}, {"type": "boolean"}),
    )
    verdicts = {link.claim: link.verdict for link in checked.links}
    assert verdicts["c0.v <= move_joint.speed"] == "refuted"
    patterned = {**root, "patternProperties": {"^s": {"type": "number"}}}
    loose = read_contract({"name": "move_joint", "inputSchema": patterned, "outputSchema": done})
    assert loose.argument("speed").schema is True
    assert not loose.plain


def test_a_task_fixing_an_argument_it_reads_fresh_is_refused() -> None:
    # A constant is written into the program and read at no time, so it is never fresh.
    with pytest.raises(ValueError, match=r"grasp\.distance is fixed to a constant and read fresh"):
        read_task(
            "t",
            {},
            True,
            constants={"grasp.distance": 0.3},
            fresh=["grasp.distance"],
        )
    read_task("t", {}, True, constants={"grasp.distance": 0.3}, fresh=["grasp.force"])


#: A tool taking rows that each hold a name and a number in bounds, and no other key.
SEARCH = read_contract(
    {
        "name": "search",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "maxLength": 8},
                            "elo": {"type": "number", "minimum": -1000, "maximum": 1000},
                        },
                        "required": ["name", "elo"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["rows"],
            "additionalProperties": False,
        },
    }
)


def test_a_refusal_of_a_value_failing_everywhere_lists_three_failures() -> None:
    rows = [{"name": f"t{n}", "elo": 5000} for n in range(1800)]
    assert failures(SEARCH.accepts, {"rows": rows}, contract_names(SEARCH)) == (
        "rows[0].elo: union_error; rows[1].elo: union_error; rows[2].elo: union_error"
    )
    two = [*rows[:2], {"name": "t", "elo": 0}]
    assert failures(SEARCH.accepts, {"rows": two}, contract_names(SEARCH)) == (
        "rows[0].elo: union_error; rows[1].elo: union_error"
    )


@given(
    rows=st.lists(
        st.dictionaries(
            st.sampled_from(["name", "elo", "secret"]),
            st.integers(-2000, 2000) | st.text(max_size=12) | st.none(),
        )
        | st.integers(),
        max_size=6,
    )
)
@settings(deadline=None)
def test_a_refusal_lists_the_first_three_failures_the_full_report_holds(rows: list[Any]) -> None:
    arguments = {"rows": rows}
    try:
        SEARCH.accepts.validate(arguments)
    except ValidationError as error:
        full = error.errors
    else:
        full = []
    written = [
        "".join(
            f"[{step}]" if isinstance(step, int) else f".{'*' if step == 'secret' else step}"
            for step in cast("tuple[str | int, ...]", failure["path"])
        ).removeprefix(".")
        + f": {failure['code']}"
        for failure in full
    ]
    expected = "; ".join(written[:3]) if full else "no failure valgebra names"
    assert failures(SEARCH.accepts, arguments, contract_names(SEARCH)) == expected


@pytest.mark.parametrize("schema", [{"const": 1e400}, {"enum": [1e400]}])
def test_a_constant_past_the_float_range_is_proven_inside_no_other(schema: dict[str, Any]) -> None:
    # `json.loads` reads 1e400 as an infinity. Read as no value, the constant would lie inside
    # every argument, and a link passing it on would be proven.
    read: dict[str, Any] = {
        "name": "read_level",
        "inputSchema": {"type": "object"},
        "outputSchema": {"type": "object", "properties": {"v": schema}, "required": ["v"]},
    }
    write: dict[str, Any] = {
        "name": "write_level",
        "inputSchema": {"type": "object", "properties": {"v": {"const": 0}}, "required": ["v"]},
        "outputSchema": {
            "type": "object",
            "properties": {"done": {"type": "boolean"}},
            "required": ["done"],
        },
    }
    tools = {tool["name"]: read_contract(tool) for tool in (read, write)}
    program = read_program("c0 = read_level()\nc1 = write_level(v=c0.v)\nreturn c1.done\n")
    checked = verify(program, tools, read_task("t", {}, {"type": "boolean"}))
    verdicts = {link.claim: link.verdict for link in checked.links}
    assert verdicts["c0.v <= write_level.v"] == "refuted"


def _returning(schema: dict[str, Any]) -> Contract:
    """Return a tool whose result is ``schema``."""
    return read_contract(
        {"name": "look", "inputSchema": {"type": "object"}, "outputSchema": schema}
    )


def _record_schema(**properties: object) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": sorted(properties)}


STATUS = {"enum": ["open", "lost"]}
TOTAL = {"type": "integer", "minimum": 0, "maximum": 9}
PLAIN_FIELDS = _record_schema(status=STATUS, total=TOTAL, note={"type": "string", "pattern": "^n"})


def test_a_contract_shows_a_value_of_its_fields_members() -> None:
    """The record of the fields it guarantees comes first, then it with each key it may hold."""
    look = _returning(_record_schema(order=PLAIN_FIELDS, count=TOTAL))
    (shown,) = look.shown()
    assert shown["count"] == 0
    assert shown["order"]["status"] == "open"
    assert shown["order"]["note"].startswith("n")
    assert look.result().values.is_valid(shown)
    wider = _record_schema(status=STATUS)
    wider["properties"]["more"] = TOTAL
    assert list(_returning(wider).shown()) == [{"status": "open"}, {"status": "open", "more": 0}]
    assert list(_returning({"type": "string", "pattern": "^n$"}).shown()) == ["n"]
    assert list(_returning({"type": "object"}).shown()) == [{}]


def test_a_contract_shows_nothing_of_a_field_with_no_member_known() -> None:
    empty = {"type": "integer", "minimum": 5, "maximum": 4}
    assert list(_returning(_record_schema(status=STATUS, total=empty)).shown()) == []


@pytest.mark.parametrize(
    ("schema", "members"),
    [
        ({"enum": ["lost", 2, None]}, ("lost", 2, None)),
        ({"const": 0.5}, (0.5,)),
        ({"type": "integer", "minimum": 3, "maximum": 9}, (3, 9, 6)),
        ({"type": "number", "exclusiveMinimum": 0.5, "maximum": 4}, (4, 2)),
        ({"type": "string", "minLength": 4, "maxLength": 4}, None),
        ({"type": "array", "minItems": 3, "items": TOTAL}, None),
    ],
    ids=["enum", "const", "range", "open", "length", "array"],
)
def test_a_slot_s_members_are_those_its_schema_names(
    schema: dict[str, Any], members: tuple[Any, ...] | None
) -> None:
    """Where it names none, one is drawn, and each lies in the slot's set."""
    slot = _returning(_record_schema(at=schema)).result("at")
    found = slot.members()
    assert found
    assert all(map(slot.values.is_valid, found))
    assert members is None or found[: len(members)] == members
    assert slot.members() is found


def test_a_listed_value_its_asserted_format_refuses_is_no_member() -> None:
    """The schema lists it, and the set read with formats asserted holds it not."""
    dated = {"enum": ["soon", "2026-11-25"], "format": "date"}
    spec = {"name": "look", "inputSchema": {"type": "object"}}
    look = read_contract({**spec, "outputSchema": _record_schema(on=dated)}, assert_format=True)
    assert look.result("on").listed() == ("soon", "2026-11-25")
    assert look.result("on").members() == ("2026-11-25",)


def test_a_declared_field_s_slot_is_read_once_and_an_undeclared_one_is_not_kept() -> None:
    """The schema's properties bound what a slot keeps, whatever keys programs read."""
    whole = _returning(PLAIN_FIELDS).result()
    assert field_slot(whole, "total") is field_slot(whole, "total")
    assert field_slot(whole, "total").path == ("total",)
    assert field_slot(whole, "other") is not field_slot(whole, "other")
    assert replace(whole) == whole
    assert field_slot(replace(whole), "total") is not field_slot(whole, "total")


@pytest.mark.parametrize(
    "values",
    [[1], [True], [1.5], ["a", None], [[1, {"a": 1}]], [float("inf")], [10**400], [], ["\ud83d"]],
    ids=["one", "true", "fraction", "text", "nested", "infinite", "large", "none", "surrogate"],
)
def test_a_listed_set_is_the_enum_s_whether_its_text_was_read_or_not(values: list[Any]) -> None:
    """Equal texts list one set: `1` and `true` are two."""
    assert listed(values) == compile_schema({"enum": values})
    assert listed(tuple(values)) is listed(values)
    assert listed([1]) != listed([True])


def test_a_test_s_sides_and_an_inclusion_are_those_asked_afresh() -> None:
    weight = compile_schema({"type": "integer", "minimum": 1, "maximum": 30})
    light = compile_schema({"type": "integer", "minimum": 1, "maximum": 10})
    kept, left = sides(weight, light)
    assert kept == intersection(weight, light)
    assert left == intersection(weight, complement(light))
    assert sides(weight, light) is sides(weight, light)
    for values, target in itertools.product((weight, light, kept, left), repeat=2):
        assert inside(values, target) == values.relation_to(target)
        assert meet(values, target) == intersection(values, target).relation_to(nothing)
        assert apart(values, target) == (meet(values, target) == "subset")
