from __future__ import annotations

from typing import Any

from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import read_program
from valgebra_planning.privilege import allowed, as_policy, read_privileges, read_rules, updates
from valgebra_planning.verify import verify

ALICE = {"type": "string", "pattern": "^alice@"}
SEARCHING: dict[str, Any] = {"search_emails": [[1, 0, {"sender": ALICE}, 0]]}
EXPANDED = SEARCHING | {"read_emails": [[1, 0, {}, 0]], "send_slack_msg": [[1, 0, {}, 0]]}
BOUND = EXPANDED | {"send_slack_msg": [[1, 0, {"to": {"const": "alice"}}, 0]]}


def _changes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, str]:
    return {u.tool: u.change for u in updates(read_privileges(old), read_privileges(new))}


def test_adding_tools_is_an_expansion_shown_by_a_call() -> None:
    found = {u.tool: u for u in updates(read_privileges(SEARCHING), read_privileges(EXPANDED))}
    assert found["search_emails"].change == "narrowing"
    assert found["read_emails"].change == "expansion"
    assert found["send_slack_msg"].witness


def test_binding_an_argument_narrows_and_a_forbidding_rule_does_too() -> None:
    assert _changes(EXPANDED, BOUND)["send_slack_msg"] == "narrowing"
    forbidding = BOUND | {
        "search_emails": [
            [1, 1, {"sender": {"pattern": "^alice@evil"}}, 0],
            *BOUND["search_emails"],
        ]
    }
    assert _changes(BOUND, forbidding)["search_emails"] == "narrowing"
    assert _changes(forbidding, BOUND)["search_emails"] == "expansion"


def test_the_first_rule_that_matches_decides() -> None:
    allow_first = allowed(read_rules([[1, 0, {}, 0], [2, 1, {"to": {"const": "bob"}}, 0]]))
    forbid_first = allowed(read_rules([[1, 1, {"to": {"const": "bob"}}, 0], [1, 0, {}, 0]]))
    assert allow_first.is_valid({"to": "bob"})
    assert not forbid_first.is_valid({"to": "bob"})
    assert forbid_first.is_valid({"to": "ann"})


def test_a_string_restriction_matches_from_the_start_and_a_literal_never() -> None:
    prefixed = allowed(read_rules([[1, 0, {"to": "alice"}, 0]]))
    assert prefixed.is_valid({"to": "alice@x"})
    assert not prefixed.is_valid({"to": "x.alice"})
    assert not prefixed.is_valid({"to": 5})
    assert not allowed(read_rules([[1, 0, {"n": 5}, 0]])).is_valid({"n": 5})


def test_a_program_is_held_to_a_privilege_policy_before_it_runs() -> None:
    tools = {
        "send_slack_msg": read_contract(
            {
                "name": "send_slack_msg",
                "inputSchema": {
                    "type": "object",
                    "properties": {"to": {"type": "string"}},
                    "required": ["to"],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                },
            }
        )
    }
    task = read_task("send", {}, {"type": "boolean"})
    policy = as_policy("least privilege", read_privileges(BOUND), tools)

    def held(to: str) -> str:
        program = read_program(f"c1 = send_slack_msg(to={to!r})\nreturn c1.ok\n")
        links = verify(program, tools, task, policies=[policy]).links
        return next(link.verdict for link in links if link.claim.startswith("policy"))

    assert held("alice") == "proven"
    assert held("bob") == "refuted"
