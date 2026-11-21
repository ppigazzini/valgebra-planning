from __future__ import annotations

from typing import Any

import pytest

from valgebra_planning.contracts import item_slots, read_contract, read_task
from valgebra_planning.critic import ProgramError, read_program
from valgebra_planning.flows import read_flow
from valgebra_planning.policy import read_policy
from valgebra_planning.program import render
from valgebra_planning.run import run
from valgebra_planning.verify import verify

NAME = {"type": "string", "pattern": "^[a-z]+$"}
TOOLS = {
    contract.name: contract
    for contract in map(
        read_contract,
        [
            {
                "name": "search",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer", "minimum": 0},
                        "entities": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"name": NAME, "id": {"type": "string"}},
                                "required": ["name"],
                            },
                        },
                    },
                    "required": ["count", "entities"],
                },
            },
            {
                "name": "notify",
                "inputSchema": {
                    "type": "object",
                    "properties": {"name": NAME},
                    "required": ["name"],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {"sent": {"type": "boolean"}},
                    "required": ["sent"],
                },
            },
            {
                "name": "alert",
                "inputSchema": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            },
        ],
    )
}
TASK = read_task("count", {"query": {"type": "string"}}, {"type": "integer"})
LOOP = """\
c1 = search(query=query)
for entity in c1.entities:
    c2 = notify(name=entity.name)
return c1.count"""


def _claims(source: str, **rules: Any) -> dict[str, str]:
    checked = verify(read_program(source), TOOLS, TASK, **rules)
    return {link.claim: link.verdict for link in checked.links}


def test_an_item_of_an_array_is_read_with_the_fields_it_guarantees() -> None:
    entities = TOOLS["search"].result("entities")
    assert [slot.path for slot in item_slots(entities)] == [
        ("entities", "[]"),
        ("entities", "[]", "name"),
    ]


def test_a_loop_reads_back_and_its_body_is_proven_for_every_item() -> None:
    assert render(read_program(LOOP)) == LOOP
    claims = _claims(LOOP)
    assert claims["entity.name <= notify.name"] == "proven"
    assert set(claims.values()) == {"proven"}


def test_a_binding_in_the_body_outside_its_argument_is_refuted() -> None:
    claims = _claims(LOOP.replace("name=entity.name", "name=entity.id"))
    assert claims["entity.id is a value the program holds"] == "refuted"


def test_what_the_body_calls_is_scoped_to_its_iteration() -> None:
    claims = _claims(LOOP.replace("return c1.count", "return c2.sent"))
    assert claims["c2.sent is a value the program holds"] == "refuted"


def test_a_policy_holds_over_any_number_of_iterations() -> None:
    once = read_policy(
        {
            "name": "notify at most once",
            "initial": "start",
            "bad": ["twice"],
            "transitions": [
                {"from": "start", "tool": "notify", "to": "once"},
                {"from": "once", "tool": "notify", "to": "twice"},
            ],
        }
    )
    single = "c1 = search(query=query)\nc2 = notify(name='a')\nreturn c1.count\n"
    assert _claims(single, policies=[once])["policy notify at most once holds"] == "proven"
    assert _claims(LOOP, policies=[once])["policy notify at most once holds"] == "undecided"


def test_an_obligation_a_loop_may_leave_open_is_undecided_and_one_it_settles_proven() -> None:
    answered = read_policy(
        {
            "name": "every notice is followed by an alert",
            "initial": "clear",
            "bad": [],
            "pending": ["owed"],
            "transitions": [
                {"from": "clear", "tool": "notify", "to": "owed"},
                {"from": "owed", "tool": "alert", "to": "clear"},
            ],
        }
    )
    claim = "policy every notice is followed by an alert holds"
    assert _claims(LOOP, policies=[answered])[claim] == "undecided"
    settled = LOOP.replace(
        "    c2 = notify(name=entity.name)\n",
        "    c2 = notify(name=entity.name)\n    c3 = alert(message=query)\n",
    )
    assert _claims(settled, policies=[answered])[claim] == "proven"
    after = "c1 = search(query=query)\nc2 = notify(name='a')\nreturn c1.count\n"
    assert _claims(after, policies=[answered])[claim] == "refuted"


def test_every_call_on_an_item_the_user_listed_is_a_flow_rule_with_only() -> None:
    # "Every cancellation is of an order the user listed" needs no value carried by a policy.
    listed = read_task(
        "notify the listed",
        {"query": {"type": "string"}, "names": {"type": "array", "items": NAME}},
        {"type": "integer"},
    )
    rule = read_flow(
        {
            "name": "notices go to listed names",
            "sources": ["names"],
            "sink": "notify.name",
            "only": True,
        }
    )
    given = (
        "c1 = search(query=query)\nfor name in names:\n    c2 = notify(name=name)\nreturn c1.count"
    )
    checked = verify(read_program(given), TOOLS, listed, flows=[rule])
    assert checked.count("proven") == len(checked.links)
    found = verify(read_program(LOOP), TOOLS, listed, flows=[rule])
    claim = "flow notices go to listed names: entity.name from search reaches notify.name"
    assert {link.claim: link.verdict for link in found.links}[claim] == "refuted"


def test_a_call_in_a_loop_over_an_untrusted_array_is_an_implicit_flow() -> None:
    rule = read_flow(
        {"name": "alerts", "sources": ["search"], "sink": "alert.message", "implicit": True}
    )
    looped = LOOP.replace("c2 = notify(name=entity.name)", "c2 = alert(message=query)")
    claims = _claims(looped, flows=[rule])
    assert claims["flow alerts: alert runs under a test on search"] == "refuted"


def test_a_run_calls_the_body_once_for_each_item() -> None:
    made: list[str] = []

    def notify(**arguments: Any) -> Any:
        made.append(arguments["name"])
        return {"sent": True}

    def tools(entities: list[Any]) -> dict[str, Any]:
        return {
            "search": lambda **_: {"count": len(entities), "entities": entities},
            "notify": notify,
        }

    program = read_program(LOOP)
    result = run(program, TOOLS, TASK, {"query": "q"}, tools([{"name": "a"}, {"name": "b"}]))
    assert (result.value, made) == (2, ["a", "b"])
    assert run(program, TOOLS, TASK, {"query": "q"}, tools([])).value == 0


@pytest.mark.parametrize(
    "source",
    [
        "c1 = search(query=query)\nfor e in c1.entities:\n    return c1.count\nreturn c1.count\n",
        "c1 = search(query=query)\nfor e in c1.entities:\n    pass\nelse:\n    pass\nreturn 1\n",
        "c1 = search(query=query)\nfor a, b in c1.entities:\n    pass\nreturn c1.count\n",
    ],
)
def test_a_return_in_a_body_a_loop_else_and_two_items_are_refused(source: str) -> None:
    with pytest.raises(ProgramError):
        read_program(source)


TAG = {"enum": ["a", "b"]}
RUN = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "tags": {"type": "array", "items": TAG},
        "meta": {"type": "object", "properties": {"tag": TAG}, "required": ["tag"]},
    },
    "required": ["id", "meta"],
}
RUNS = {
    contract.name: contract
    for contract in map(
        read_contract,
        [
            {
                "name": "list_runs",
                "inputSchema": {"type": "object", "properties": {}},
                "outputSchema": {
                    "type": "object",
                    "properties": {"runs": {"type": "array", "items": RUN}},
                    "required": ["runs"],
                },
            },
            {
                "name": "find_by_tag",
                "inputSchema": {
                    "type": "object",
                    "properties": {"tag": TAG},
                    "required": ["tag"],
                },
                "outputSchema": {"type": "object"},
            },
        ],
    )
}
LISTED = read_task("tagged runs", {}, {"type": "object"})
TAGGED = """\
found = list_runs()
for run in found.runs:
    if "tags" in run:
        for tag in run.tags:
            hits = find_by_tag(tag=tag)
return found"""


def test_a_key_test_on_an_item_makes_its_field_a_value_the_program_holds() -> None:
    program = read_program(TAGGED)
    checked = verify(program, RUNS, LISTED)
    assert [(link.claim, link.verdict) for link in checked.links] == [
        ("tag <= find_by_tag.tag", "proven"),
        ("return found <= goal", "proven"),
    ]
    asked: list[str] = []
    runs = [
        {"id": "x", "meta": {"tag": "a"}, "tags": ["a", "b"]},
        {"id": "y", "meta": {"tag": "b"}},
    ]
    tools = {
        "list_runs": lambda **_: {"runs": runs},
        "find_by_tag": lambda tag: asked.append(tag) or {},
    }
    ran = run(program, RUNS, LISTED, {}, tools)
    assert (ran.broken, asked) == ((), ["a", "b"])


def test_a_field_two_objects_down_an_item_is_read_as_a_result_s_is() -> None:
    source = (
        "found = list_runs()\nfor run in found.runs:\n"
        "    hits = find_by_tag(tag=run.meta.tag)\nreturn found"
    )
    checked = verify(read_program(source), RUNS, LISTED)
    assert {link.claim: link.verdict for link in checked.links} == {
        "run.meta.tag <= find_by_tag.tag": "proven",
        "return found <= goal": "proven",
    }


def test_a_flow_rule_reads_a_field_the_verifier_reads_below_an_item() -> None:
    # No environment holds `run.meta.tag`: the rule reads it as the verifier does.
    rule = read_flow(
        {"name": "tags are trusted", "sources": ["list_runs"], "sink": "find_by_tag.tag"}
    )
    source = TAGGED.replace(
        "for tag in run.tags:\n            hits = find_by_tag(tag=tag)",
        "hits = find_by_tag(tag=run.meta.tag)",
    )
    checked = verify(read_program(source), RUNS, LISTED, flows=[rule])
    claim = "flow tags are trusted: run.meta.tag from list_runs reaches find_by_tag.tag"
    assert {link.claim: link.verdict for link in checked.links}[claim] == "refuted"


ONE = {"type": "object", "properties": {"tag": TAG}, "required": ["tag"]}


@pytest.mark.parametrize(
    ("given", "first"), [({}, "x = one()\n"), ({"x": ONE}, "")], ids=["a result", "a task input"]
)
@pytest.mark.parametrize(
    "item",
    [{"properties": {"tag": {"type": "string"}}, "required": ["tag"]}, {}],
    ids=["declaring the field wider", "declaring no field"],
)
def test_a_loop_item_reads_no_field_of_a_value_its_name_held(
    given: dict[str, Any], first: str, item: dict[str, Any]
) -> None:
    tools = {
        contract.name: contract
        for contract in map(
            read_contract,
            [
                {"name": "one", "inputSchema": {"type": "object"}, "outputSchema": ONE},
                {
                    "name": "many",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {
                        "type": "object",
                        "properties": {
                            "rs": {"type": "array", "items": {"type": "object", **item}}
                        },
                        "required": ["rs"],
                    },
                },
                {
                    "name": "find_by_tag",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"tag": TAG},
                        "required": ["tag"],
                    },
                    "outputSchema": {"type": "object"},
                },
            ],
        )
    }
    task = read_task("shadowed", given, {"type": "object"})
    source = f"{first}m = many()\nfor x in m.rs:\n    hits = find_by_tag(tag=x.tag)\nreturn m"
    program = read_program(source)
    checked = verify(program, tools, task)
    assert [link.verdict for link in checked.links if link.claim.startswith("x.tag")] == ["refuted"]
    calls = {
        "one": lambda **_: {"tag": "a"},
        "many": lambda **_: {"rs": [{"tag": "zz"}]},
        "find_by_tag": lambda **_: {},
    }
    ran = run(program, tools, task, {"x": {"tag": "a"}} if given else {}, calls)
    assert ran.why is not None
    assert ran.why.check == "arguments"
