"""Only the boundary module imports valgebra or valgebra-jsonschema."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).parent.parent / "valgebra_planning"
UPSTREAM = ("valgebra", "valgebra_jsonschema")


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_only_the_boundary_imports_upstream() -> None:
    modules = sorted(PACKAGE.glob("*.py"))
    assert len(modules) >= 2, "the scan found almost no modules"
    crossing = [
        path.name
        for path in modules
        if path.name != "_valgebra.py" and _imports(path) & set(UPSTREAM)
    ]
    assert not crossing, f"modules importing upstream past the boundary: {crossing}"


def test_the_boundary_imports_both() -> None:
    assert _imports(PACKAGE / "_valgebra.py") >= set(UPSTREAM)
