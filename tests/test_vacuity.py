from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from valgebra_planning.library import read_library
from valgebra_planning.vacuity import vacuities

PARCELS = Path(__file__).parent.parent / "examples" / "parcels"


def _planted(tmp_path: Path, name: str, change: Any) -> Path:
    """Copy the parcel library and apply ``change`` to the JSON of one of its files."""
    library = tmp_path / "parcels"
    shutil.copytree(PARCELS, library)
    path = library / name
    path.write_text(
        json.dumps(change(json.loads(path.read_text(encoding="utf-8")))), encoding="utf-8"
    )
    return library


def _found(library: Path) -> set[tuple[str, str, str]]:
    return {(f.where, f.finding, f.certainty) for f in vacuities(read_library(library))}


def test_the_parcel_library_holds_no_vacuous_specification() -> None:
    assert _found(PARCELS) == set()


def test_an_empty_goal_and_a_universal_goal_are_found(tmp_path: Path) -> None:
    def goals(tasks: Any) -> Any:
        inputs = tasks["tasks"][0]["inputs"]
        tasks["tasks"] += [
            {"name": "never", "inputs": inputs, "goal": {"not": {}}},
            {"name": "always", "inputs": inputs, "goal": {}},
        ]
        return tasks

    assert _found(_planted(tmp_path, "tasks.json", goals)) == {
        ("task never", "no value meets the goal", "proven"),
        ("task always", "every JSON value meets the goal", "proven"),
    }


def test_a_guard_no_record_meets_leaves_the_policy_unbreakable(tmp_path: Path) -> None:
    def dead(policies: Any) -> Any:
        for transition in policies["policies"][0]["transitions"]:
            if transition["to"] == "violated":
                transition["arguments"] = {
                    "properties": {"state": {"const": "lost"}},
                    "required": ["state"],
                }
        return policies

    found = _found(_planted(tmp_path, "policies.json", dead))
    assert (
        "policy freight approved first",
        "no transition that can fire reaches a bad or pending state",
        "proven",
    ) in found
    assert any(
        "meets its guard" in finding and certainty == "proven" for _, finding, certainty in found
    )


def test_an_unused_rule_is_found_and_one_naming_no_tool_is_refused(tmp_path: Path) -> None:
    # A rule no task is held to is a finding; a rule naming a tool the library lacks reads no
    # call at all, and the library is refused naming it, before any check runs.
    def unused(tool: str) -> Any:
        def extra(policies: Any) -> Any:
            rule = json.loads(json.dumps(policies["policies"][0]))
            rule["name"] = "unused"
            rule["transitions"][0]["tool"] = tool
            policies["policies"].append(rule)
            return policies

        return extra

    policies = json.loads((PARCELS / "policies.json").read_text(encoding="utf-8"))
    listed = policies["policies"][0]["transitions"][0]["tool"]
    found = _found(_planted(tmp_path / "a", "policies.json", unused(listed)))
    assert ("policy unused", "no task is held to it", "proven") in found
    with pytest.raises(ValueError, match="'no_such_tool', and no tool listed is it"):
        _found(_planted(tmp_path / "b", "policies.json", unused("no_such_tool")))


def test_an_endorsed_set_that_admits_nothing_or_everything_is_found(tmp_path: Path) -> None:
    def endorsed(value: Any) -> Any:
        def change(flows: Any) -> Any:
            flows["flows"][0]["endorsed"] = value
            return flows

        return change

    nothing = _found(_planted(tmp_path / "a", "flows.json", endorsed({"const": 42})))
    everything = _found(_planted(tmp_path / "b", "flows.json", endorsed({})))
    sink = "notify.email"
    assert (
        "flow notices go to verified addresses",
        f"the endorsed set meets nothing {sink} takes",
        "proven",
    ) in nothing
    assert (
        "flow notices go to verified addresses",
        f"the endorsed set holds everything {sink} takes",
        "proven",
    ) in everything


def test_a_guard_valgebra_cannot_part_is_undecided_and_the_policy_kept_live(
    tmp_path: Path,
) -> None:
    def bounded(policies: Any) -> Any:
        for transition in policies["policies"][0]["transitions"]:
            if transition["to"] == "violated":
                transition["arguments"] = {
                    "properties": {"weight_g": {"maximum": 0}},
                    "required": ["weight_g"],
                }
        return policies

    found = _found(_planted(tmp_path, "policies.json", bounded))
    assert found == {
        (
            "policy freight approved first, transition 1 (start -> violated)",
            "no argument record of book_freight_return meets its guard",
            "undecided",
        )
    }


def test_a_policy_holding_only_pending_states_is_live(tmp_path: Path) -> None:
    def owed(policies: Any) -> Any:
        policies["policies"][0]["pending"] = policies["policies"][0]["bad"]
        policies["policies"][0]["bad"] = []
        return policies

    assert _found(_planted(tmp_path, "policies.json", owed)) == set()


def test_a_result_the_closed_reading_empties_is_found(tmp_path: Path) -> None:
    unnamed = {
        "name": "lookup",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "outputSchema": {"type": "object", "required": ["id"]},
    }
    (tmp_path / "tools.json").write_text(json.dumps({"tools": [unnamed]}), encoding="utf-8")
    assert _found(tmp_path) == set()
    (tmp_path / "tools.json").write_text(
        json.dumps({"tools": [unnamed], "closedResults": True}), encoding="utf-8"
    )
    said = "no result is returned read closed, though its output schema admits one"
    assert _found(tmp_path) == {("tool lookup", said, "proven")}


def test_the_parcel_library_read_closed_holds_no_vacuous_specification(tmp_path: Path) -> None:
    library = _planted(tmp_path, "tools.json", lambda tools: {**tools, "closedResults": True})
    assert all(c.declared is not None for c in read_library(library).tools.values())
    assert _found(library) == set()


def _first(transition: dict[str, Any]) -> Any:
    """Put ``transition`` first among the parcel policy's transitions."""

    def change(policies: Any) -> Any:
        policies["policies"][0]["transitions"].insert(0, transition)
        return policies

    return change


def test_a_transition_the_ones_before_it_take_every_record_of_never_fires(tmp_path: Path) -> None:
    every = {"from": "start", "tool": "book_freight_return", "to": "booked"}
    found = _found(_planted(tmp_path, "policies.json", _first(every)))
    assert any(
        where.endswith("(start -> violated)")
        and finding == "the transitions before it from start take every argument record it reads"
        and certainty == "proven"
        for where, finding, certainty in found
    )
    heavy = {**every, "arguments": {"properties": {"weight_g": {"minimum": 30000}}}}
    narrower = _found(_planted(tmp_path / "narrower", "policies.json", _first(heavy)))
    assert not any("take every" in finding for _, finding, _ in narrower)


def test_a_flow_source_names_a_tool_or_a_task_input_at_load(tmp_path: Path) -> None:
    # A source is a tool's name or a task input's; one naming neither reads no value.
    def sourced(source: str) -> Any:
        def change(flows: Any) -> Any:
            flows["flows"][0]["sources"] = [source]
            return flows

        return change

    read_library(_planted(tmp_path / "a", "flows.json", sourced("tracking")))
    with pytest.raises(ValueError, match="'trackin' names no tool listed and no task's input"):
        read_library(_planted(tmp_path / "b", "flows.json", sourced("trackin")))


def test_two_policies_of_one_name_are_refused_at_load(tmp_path: Path) -> None:
    # A task names its policies by name, so two of one name held it to the last alone.
    def twice(policies: Any) -> Any:
        policies["policies"].append(json.loads(json.dumps(policies["policies"][0])))
        return policies

    name = json.loads((PARCELS / "policies.json").read_text(encoding="utf-8"))["policies"][0]
    with pytest.raises(ValueError, match=f"two policies are named {name['name']!r}"):
        read_library(_planted(tmp_path, "policies.json", twice))
