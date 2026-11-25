from __future__ import annotations

import math
import random
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from valgebra_planning import cells
from valgebra_planning._valgebra import compile_schema
from valgebra_planning.cells import (
    EDGES,
    KINDS,
    Abstraction,
    Predicate,
    _pool,
    _Relations,
    abstract,
    gather,
)

SCHEMAS: dict[str, object] = {
    "order_id": {"type": "string", "pattern": "^ord_[0-9]+$"},
    "any_id": {"type": "string", "pattern": "^[a-z]+_[0-9]+$"},
    "status": {"enum": ["open", "shipped", "lost"]},
    "shipped": {"const": "shipped"},
    "total": {"type": "integer", "minimum": 0, "maximum": 1000},
    "high": {"type": "integer", "minimum": 500},
    "ratio": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    "flag": {"type": "boolean"},
}


def _predicates() -> list[Predicate]:
    return gather((name, compile_schema(s), s, s) for name, s in SCHEMAS.items())


ABSTRACTION = abstract(_predicates())


def _kind(value: Any) -> str:
    return next(kind for kind, values in KINDS.items() if values.is_valid(value))


def _cell_of(abstraction: Abstraction, value: Any) -> int | None:
    kind = _kind(value)
    inside = frozenset(i for i, p in enumerate(abstraction.predicates) if p.values.is_valid(value))
    for i, cell in enumerate(abstraction.cells):
        if cell.kind == kind and cell.inside == inside:
            return i
    return None


JSON_VALUES = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(-2000, 2000)
    | st.floats(allow_nan=True, allow_infinity=True)
    | st.text(alphabet="abcdeforsldp_0123456789", max_size=12)
    | st.sampled_from(["open", "shipped", "lost", "ord_1", "ord_12", "x_9"]),
    lambda inner: (
        st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=3), inner, max_size=3)
    ),
    max_leaves=8,
)


def _holds_nan(value: Any) -> bool:
    if isinstance(value, float):
        return math.isnan(value)
    if isinstance(value, list):
        return any(map(_holds_nan, value))
    if isinstance(value, dict):
        return any(map(_holds_nan, value.values()))
    return False


def _of_no_kind_and_no_predicate(value: Any) -> bool:
    return not any(values.is_valid(value) for values in KINDS.values()) and not any(
        predicate.values.is_valid(value) for predicate in ABSTRACTION.predicates
    )


@settings(max_examples=400, deadline=None)
@given(JSON_VALUES)
def test_every_value_lies_in_a_cell_the_abstraction_kept(value: Any) -> None:
    if _holds_nan(value):
        assert _of_no_kind_and_no_predicate(value), value
    else:
        assert _cell_of(ABSTRACTION, value) is not None, value


def test_a_value_holding_nan_is_of_no_kind_and_in_no_predicate() -> None:
    # JSON has no NaN (RFC 8259 §6), at the top or below a key or an index.
    nan = float("nan")
    for value in (nan, [nan], {"k": nan}, [1, {"a": ["x", nan]}]):
        assert _of_no_kind_and_no_predicate(value), value


def test_every_edge_and_listed_value_lies_in_a_kept_cell() -> None:
    for value in (*EDGES, "open", "shipped", "lost", 0, 1000, 500, 0.25):
        assert _cell_of(ABSTRACTION, value) is not None, value


def test_the_kinds_partition_json_values() -> None:
    for value in (*EDGES, 2.0, -0.0, math.pi, [1, "a"], {"k": [None]}):
        assert sum(values.is_valid(value) for values in KINDS.values()) == 1, value


def test_a_predicate_is_exactly_the_union_of_its_cells() -> None:
    status = ABSTRACTION.index("status")
    shipped = ABSTRACTION.index("shipped")
    # `shipped` is below `status`, so every cell inside it is inside `status` too.
    assert ABSTRACTION.cells_of(shipped) <= ABSTRACTION.cells_of(status)
    total = ABSTRACTION.index("total")
    high = ABSTRACTION.index("high")
    both = ABSTRACTION.cells_of(total) & ABSTRACTION.cells_of(high)
    assert both, "500 to 1000 is a cell inside both ranges"
    for cell in both:
        assert ABSTRACTION.cells[cell].values.is_valid(750)


def test_cells_are_decided_and_the_relations_asked_once_each() -> None:
    assert ABSTRACTION.undecided(range(len(ABSTRACTION.cells))) == 0
    pairs = len(ABSTRACTION.predicates) ** 2
    assert ABSTRACTION.asked <= 2 * pairs


def test_one_schema_named_twice_is_one_predicate() -> None:
    order_id = SCHEMAS["order_id"]
    merged = gather(
        [
            ("find.order_id", compile_schema(order_id), order_id, order_id),
            ("refund.order_id", compile_schema(order_id), order_id, order_id),
        ]
    )
    assert len(merged) == 1
    assert merged[0].names == ("find.order_id", "refund.order_id")


def test_a_relation_read_off_the_candidates_is_the_one_valgebra_answers() -> None:
    """A candidate refutes, and valgebra proves: the two readings of a pair are one."""
    predicates = _predicates()
    read = _Relations(predicates, _pool(predicates, random.Random(0)))
    asked = _Relations(predicates, [])
    pairs = [(i, j) for i in range(len(predicates)) for j in range(len(predicates))]
    for i, j in pairs:
        assert read.is_below(i, j) == asked.is_below(i, j)
        assert read.are_apart(i, j) == asked.are_apart(i, j)
    apart = len(predicates) * (len(predicates) + 1) // 2
    assert read.asked == asked.asked == len(pairs) + apart
    assert any(asked.below.values())
    assert any(asked.apart.values())


def test_a_candidate_in_both_of_two_sets_asks_valgebra_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refused(*_: object) -> None:
        raise AssertionError

    monkeypatch.setattr(cells, "apart", refused)
    predicates = gather(
        (name, compile_schema(SCHEMAS[name]), SCHEMAS[name], SCHEMAS[name])
        for name in ("status", "shipped")
    )
    read = _Relations(predicates, ["open", "shipped", 1])
    assert not read.are_apart(0, 1)
    assert not read.is_below(0, 1)
    assert read.is_below(1, 0)
    with pytest.raises(AssertionError):
        _Relations(predicates, ["open", 1]).are_apart(0, 1)


def test_a_candidate_in_one_set_alone_asks_valgebra_nothing() -> None:
    """A candidate refutes an inclusion, and one that tells nothing leaves it to valgebra."""

    class Listed:
        def __init__(self, *held: str) -> None:
            self.held = held

        def is_valid(self, value: object) -> bool:
            return value in self.held

        def relation_to(self, _: object) -> str:
            return "subset"

    sets = [Predicate((name,), cast("Any", Listed(*name)), {}, {}) for name in ("ab", "b")]
    read = _Relations(sets, ["a", "b", "c"])
    assert not read.is_below(0, 1)
    assert read.is_below(1, 0)
    assert _Relations(sets, ["b", "c"]).is_below(0, 1)


def test_a_name_two_predicates_bear_stands_for_the_first() -> None:
    both = [
        Predicate(("a", "b"), compile_schema(SCHEMAS["status"]), {}, {}),
        Predicate(("b", "c"), compile_schema(SCHEMAS["total"]), {}, {}),
    ]
    named = Abstraction(tuple(both), ())
    assert [named.index(name) for name in ("a", "b", "c")] == [0, 0, 1]
    with pytest.raises(KeyError):
        named.index("d")


def test_a_cell_s_witness_is_the_first_candidate_in_it() -> None:
    pool = _pool(_predicates(), random.Random(0))
    for cell in ABSTRACTION.cells:
        inside = [v for v in pool if cell.values.is_valid(v)]
        assert cell.witness == tuple(inside[:1])
