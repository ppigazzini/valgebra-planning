from __future__ import annotations

import itertools
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from valgebra_planning.contracts import item_slots, read_contract, read_task
from valgebra_planning.critic import ProgramError, read_program
from valgebra_planning.flows import read_flow
from valgebra_planning.policy import read_policy
from valgebra_planning.program import Block, Branch, Call, For, Next, Ref, Return, render
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


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.fixed_dictionaries(
            {"name": st.from_regex(r"\A[a-z]{1,6}\Z")}, optional={"id": st.text(max_size=4)}
        ),
        max_size=6,
    )
)
def test_a_loop_program_proven_strong_keeps_every_contract_on_every_array(
    entities: list[dict[str, Any]],
) -> None:
    """Each run of a loop over a finite array is one of the finite plans the loop unrolls to.

    The walk reads the body once for every item, so a program it proves strong is strong on
    each unrolling: every run keeps every contract and returns inside the goal, the body made
    once for each item.
    """
    program = read_program(LOOP)
    checked = verify(program, TOOLS, TASK)
    assert {link.verdict for link in checked.links} == {"proven"}
    assert not checked.gaps
    made: list[str] = []

    def notify(**arguments: Any) -> Any:
        made.append(arguments["name"])
        return {"sent": True}

    tools = {
        "search": lambda **_: {"count": len(entities), "entities": entities},
        "notify": notify,
    }
    result = run(program, TOOLS, TASK, {"query": "q"}, tools)
    assert (result.value, result.stopped, result.broken) == (len(entities), None, ())
    assert made == [entity["name"] for entity in entities]
    assert TASK.goal.is_valid(result.value)


@pytest.mark.parametrize(
    "source",
    [
        "c1 = search(query=query)\nfor e in c1.entities:\n    pass\nelse:\n    pass\nreturn 1\n",
        "c1 = search(query=query)\nfor a, b in c1.entities:\n    pass\nreturn c1.count\n",
    ],
)
def test_a_loop_else_and_two_items_are_refused(source: str) -> None:
    with pytest.raises(ProgramError):
        read_program(source)


FIRST = """\
c1 = search(query=query)
for entity in c1.entities:
    if "id" in entity:
        c2 = notify(name=entity.name)
        return c1.count
return c1.count"""


def test_a_return_in_a_body_ends_the_program_at_the_first_item_reaching_it() -> None:
    program = read_program(FIRST)
    checked = verify(program, TOOLS, TASK)
    assert checked.proven
    assert [link.where for link in checked.links if link.claim == "return c1.count <= goal"] == [
        "for:entity.then",
        "",
    ]
    made: list[str] = []

    def notify(name: str) -> dict[str, Any]:
        made.append(name)
        return {"sent": True}

    entities = [{"name": "a"}, {"name": "b", "id": "x"}, {"name": "c", "id": "y"}]
    tools = {"search": lambda **_: {"count": 3, "entities": entities}, "notify": notify}
    assert run(program, TOOLS, TASK, {"query": "q"}, tools).value == 3
    assert made == ["b"]


def test_an_obligation_at_a_return_in_a_body_is_read_after_any_number_of_iterations() -> None:
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
    owing = (
        "c1 = search(query=query)\n"
        "for entity in c1.entities:\n"
        '    if "id" in entity:\n'
        "        return c1.count\n"
        "    c2 = notify(name=entity.name)\n"
        "return c1.count\n"
    )

    def at_the_return(source: str) -> str:
        checked = verify(read_program(source), TOOLS, TASK, policies=[answered])
        return next(
            link.verdict
            for link in checked.links
            if link.claim == claim and link.where == "for:entity.then"
        )

    # The first item reaches the return with no notice owed; a later one, after a notice.
    assert at_the_return(owing) == "undecided"
    settled = owing.replace(
        "    c2 = notify(name=entity.name)\n",
        "    c2 = notify(name=entity.name)\n    c3 = alert(message=query)\n",
    )
    assert at_the_return(settled) == "proven"


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


def _listing(items: dict[str, Any], **narrowed: Any) -> dict[str, Any]:
    """Return the contracts of `get`, listing `rs` of ``items`` beside `n`, and of `use`."""
    output: dict[str, Any] = {
        "type": "object",
        "properties": {"n": {"enum": [1, 2]}, "rs": {"type": "array", "items": items}},
        "required": ["n", "rs"],
    }
    if narrowed:
        output["allOf"] = [{"properties": {"rs": {"items": narrowed}}}]
    return {
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


LISTED_ANY = read_task("listed", {}, True)
#: An item declaring `k` any string and `o` optional, and the array's narrowing of both.
WIDE = {"type": "object", "properties": {"k": {"type": "string"}, "o": TAG}, "required": ["k"]}
NARROW = {"properties": {"k": TAG}, "required": ["o"]}


@pytest.mark.parametrize(
    ("items", "source"),
    [
        ({"type": "string"}, "r = get()\nfor x in r.rs:\n    u = use(tag=x)\nstop(r)"),
        (WIDE, "r = get()\nfor x in r.rs:\n    u = use(tag=x.k)\nstop(r)"),
        (WIDE, "r = get()\nfor x in r.rs:\n    u = use(tag=x.o)\nstop(r)"),
        (
            WIDE,
            "r = get()\nfor x in r.rs:\n    if r.n in (1, 2):\n        u = use(tag=x.k)\nstop(r)",
        ),
    ],
    ids=["an item", "its field", "its optional field", "its field under a test on the holder"],
)
def test_an_item_its_array_narrows_is_proven_by_the_narrowing(
    items: dict[str, Any], source: str
) -> None:
    # The array's `allOf` keeps every item inside the argument, and every item's `o` present;
    # what `items` declares admits strings outside it, and items lacking `o`.
    narrowed = {"enum": ["a", "b"]} if items["type"] == "string" else NARROW
    tools = _listing(items, **narrowed)
    checked = verify(read_program(source), tools, LISTED_ANY)
    assert [link for link in checked.links if link.verdict != "proven"] == []
    held = "a" if items["type"] == "string" else {"k": "a", "o": "b"}
    calls = {"get": lambda **_: {"n": 1, "rs": [held]}, "use": lambda **_: {}}
    assert run(read_program(source), tools, LISTED_ANY, {}, calls).why is None


@pytest.mark.parametrize(
    ("source", "read"),
    [
        ("r = get()\nfor x in r.rs:\n    u = use(tag=x.k)\nstop(r)", "x.k"),
        ("r = get()\nfor r in r.rs:\n    u = use(tag=r.k)\nstop(r)", "r.k"),
        (
            "r = get()\nfor x in r.rs:\n    if r.n in (1,):\n        u = use(tag=x.k)\nstop(r)",
            "x.k",
        ),
    ],
    ids=["an item", "an item named as its holder", "an item under a test on its holder"],
)
def test_an_item_s_witness_is_one_an_array_of_some_result_holds(source: str, read: str) -> None:
    # Every item's `k` is `a`, `b` or `zz`: `zz` alone is a witness, and a run on it breaks.
    tools = _listing(WIDE, properties={"k": {"enum": ["a", "b", "zz"]}})
    [link] = [
        link
        for link in verify(read_program(source), tools, LISTED_ANY).links
        if link.verdict != "proven"
    ]
    assert link.counterexample in ((), ("zz",))
    # `get`'s `allOf` leaves valgebra-jsonschema's sampler no value, so one is given.
    given = {read: ["1fH._Z", "zz"], "r": [{"n": 1, "rs": []}]}
    [link] = [
        link
        for link in verify(read_program(source), tools, LISTED_ANY, examples=given).links
        if link.verdict != "proven"
    ]
    assert (link.verdict, link.counterexample) == ("refuted", ("zz",))
    calls = {"get": lambda **_: {"n": 1, "rs": [{"k": "zz"}]}, "use": lambda **_: {}}
    ran = run(read_program(source), tools, LISTED_ANY, {}, calls)
    assert ran.why is not None
    assert ran.why.check == "arguments"


@pytest.mark.parametrize(
    ("item_requires", "array_requires", "tested"),
    list(itertools.product([False, True], [False, True], [False, True])),
)
def test_an_item_s_field_is_held_only_where_every_item_carries_it(
    *, item_requires: bool, array_requires: bool, tested: bool
) -> None:
    # Every item holds `k` where `items` or the array's `allOf` requires it, or a test shows it.
    item = {"type": "object", "properties": {"k": TAG}, "required": ["k"] if item_requires else []}
    tools = _listing(item, **({"required": ["k"]} if array_requires else {}))
    body = '    if "k" in x:\n        u = use(tag=x.k)\n' if tested else "    u = use(tag=x.k)\n"
    source = f"r = get()\nfor x in r.rs:\n{body}stop(r)"
    links = verify(read_program(source), tools, LISTED_ANY).links
    verdicts = {link.verdict for link in links if link.claim == "x.k is a value the program holds"}
    carried = item_requires or array_requires or tested
    assert verdicts == (set() if carried else {"refuted"})


#: Four ways to declare an array's items the walk does not read: through an `allOf`, through
#: a `$ref` beside another keyword, as a tuple of `items`, and not at all.
def _unread(items: object) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        ({"type": "array", "allOf": [{"items": items}]}, {}),
        ({"$ref": "#/$defs/L", "minItems": 0}, {"L": {"type": "array", "items": items}}),
        ({"type": "array", "prefixItems": [items, items], "items": False}, {}),
        ({"type": "array"}, {}),
    ]


def _holding(rs: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {
        "type": "object",
        "properties": {"n": {"type": "integer"}, "rs": rs},
        "required": ["n", "rs"],
    }
    if defs:
        output["$defs"] = defs
    return {
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


LOOPED = "r = get()\nfor x in r.rs:\n    u = use(tag=x)\nreturn r"
SHAPES = ["an allOf", "a reference beside a keyword", "a tuple", "no items keyword"]


@pytest.mark.parametrize(("rs", "defs"), _unread(TAG)[:3], ids=SHAPES[:3])
def test_a_loop_over_items_the_walk_cannot_read_is_proven_where_each_is_inside(
    rs: dict[str, Any], defs: dict[str, Any]
) -> None:
    tools = _holding(rs, defs)
    checked = verify(read_program(LOOPED), tools, LISTED_ANY)
    assert all(link.verdict == "proven" for link in checked.links), checked.links
    calls = {"get": lambda **_: {"n": 1, "rs": ["a", "b"]}, "use": lambda **_: {}}
    assert run(read_program(LOOPED), tools, LISTED_ANY, {}, calls).why is None


@pytest.mark.parametrize(("rs", "defs"), _unread({"type": "string"}), ids=SHAPES)
def test_a_loop_over_items_the_walk_cannot_read_is_never_proven_where_one_is_outside(
    rs: dict[str, Any], defs: dict[str, Any]
) -> None:
    tools = _holding(rs, defs)
    [link] = [
        link for link in verify(read_program(LOOPED), tools, LISTED_ANY).links if "x" in link.claim
    ]
    assert link.verdict != "proven"
    assert all(value not in ("a", "b") for value in link.counterexample)
    calls = {"get": lambda **_: {"n": 1, "rs": ["zz", "zz"]}, "use": lambda **_: {}}
    ran = run(read_program(LOOPED), tools, LISTED_ANY, {}, calls)
    assert ran.why is not None
    assert ran.why.check == "arguments"


def test_what_follows_a_loop_over_items_the_walk_cannot_read_is_verified() -> None:
    # The loop's body and every step after it are walked, so a wrong return is reported.
    tools = _holding(*_unread(TAG)[0])
    task = read_task("listed", {}, TAG)
    checked = verify(
        read_program("r = get()\nfor x in r.rs:\n    u = use(tag=x)\nreturn r.n"), tools, task
    )
    assert {link.claim: link.verdict for link in checked.links}["return r.n <= goal"] == "refuted"


ALERTS = read_flow(
    {"name": "alerts", "sources": ["search"], "sink": "alert.message", "implicit": True}
)
A_OR_B = 'if entity.name in ("a", "b"):'


def test_a_sink_after_a_loop_its_body_may_stop_runs_under_the_body_s_tests() -> None:
    stopping = f"""\
c1 = search(query=query)
for entity in c1.entities:
    {A_OR_B}
        c2 = notify(name=entity.name)
    else:
        stop(entity.name)
c3 = alert(message=query)
return c1.count"""
    claims = _claims(stopping, flows=[ALERTS])
    assert claims["flow alerts: alert runs under a test on search"] == "refuted"


def test_an_early_return_from_a_loop_is_a_test_on_its_array() -> None:
    # The critic's reader refuses a return in a body; the IR holds one, as the runner runs it.
    body = Block(
        (),
        Branch(
            Ref("entity", ("name",)),
            "",
            "",
            Block((), Return(Ref("c1", ("count",)))),
            Block((), Next()),
            ("a",),
        ),
    )
    program = Block(
        (Call("search", (("query", Ref("query")),), "c1"),),
        For(
            Ref("c1", ("entities",)),
            "entity",
            body,
            Block(
                (Call("alert", (("message", Ref("query")),), "c3"),), Return(Ref("c1", ("count",)))
            ),
        ),
    )
    checked = verify(program, TOOLS, TASK, flows=[ALERTS])
    claims = {link.claim: link.verdict for link in checked.links}
    assert claims["flow alerts: alert runs under a test on search"] == "refuted"
    assert not checked.proven
    # Whether the sink is called follows the value the body tested.
    for name, alerted in (("a", []), ("z", ["alert"])):
        made: list[str] = []
        tools = {
            "search": lambda name=name, **_: {"count": 1, "entities": [{"name": name}]},
            "alert": lambda made=made, **_: made.append("alert"),
        }
        run(program, TOOLS, TASK, {"query": "q"}, tools, enforce=False)
        assert made == alerted


def test_a_sink_after_a_loop_no_iteration_of_which_ends_the_run_keeps_its_links() -> None:
    after = LOOP.replace("return c1.count", "c3 = alert(message=query)\nreturn c1.count")
    claims = _claims(after, flows=[ALERTS])
    assert not any(claim.startswith("flow alerts") for claim in claims)
