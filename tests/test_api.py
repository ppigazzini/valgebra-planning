"""The public names: each public module's `__all__`, as `docs/02-api.md` lists it."""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path
from types import ModuleType

import pytest

import valgebra_planning

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "valgebra_planning"
PAGE = ROOT / "docs" / "02-api.md"
INTERNAL = frozenset({"_valgebra", "cli"})
ROW = re.compile(r"^\| `valgebra_planning\.(\w+)` \| (.*?) \|(.*?)\|$")


def _rows() -> dict[str, tuple[frozenset[str], bool]]:
    rows = {}
    for line in PAGE.read_text(encoding="utf-8").splitlines():
        if found := ROW.match(line):
            names = frozenset(re.findall(r"`(\w+)`", found.group(2)))
            rows[found.group(1)] = (names, "mcp" in found.group(3))
    return rows


ROWS = _rows()


def _tree(stem: str) -> ast.Module:
    return ast.parse((PACKAGE / f"{stem}.py").read_text(encoding="utf-8"))


def _defined(stem: str) -> frozenset[str]:
    names = set()
    for node in _tree(stem).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


def _imported(stem: str) -> dict[str, str]:
    """Map each name ``stem`` imports from a sibling module to that module's stem."""
    found = {}
    for node in _tree(stem).body:
        if isinstance(node, ast.ImportFrom) and node.module:
            prefix, _, sibling = node.module.partition(".")
            if prefix == "valgebra_planning":
                found.update({alias.asname or alias.name: sibling for alias in node.names})
    return found


def _module(stem: str) -> ModuleType:
    return importlib.import_module(f"valgebra_planning.{stem}")


def test_the_package_exports_nothing() -> None:
    assert valgebra_planning.__all__ == []


def test_the_rows_are_the_public_modules() -> None:
    stems = {path.stem for path in PACKAGE.glob("*.py")} - {"__init__"}
    assert set(ROWS) == stems - INTERNAL


@pytest.mark.parametrize("stem", sorted(ROWS))
def test_each_module_lists_its_row(stem: str) -> None:
    assert set(_module(stem).__all__) == ROWS[stem][0]


@pytest.mark.parametrize("stem", sorted(ROWS))
def test_each_listed_name_is_defined_in_its_module(stem: str) -> None:
    assert ROWS[stem][0] <= _defined(stem)


def _annotations(value: object) -> list[str]:
    """Return the annotation text of a function, or of a class's fields and public methods."""
    if inspect.isclass(value):
        texts = list(getattr(value, "__annotations__", {}).values())
        for name, member in vars(value).items():
            if not name.startswith("_") and inspect.isfunction(member):
                texts += member.__annotations__.values()
        return [str(text) for text in texts]
    if inspect.isfunction(value):
        return [str(text) for text in value.__annotations__.values()]
    return []


@pytest.mark.parametrize("stem", sorted(ROWS))
def test_a_public_signature_names_public_types(stem: str) -> None:
    module, defined, imported = _module(stem), _defined(stem), _imported(stem)
    hidden = set()
    for name in module.__all__:
        for text in _annotations(getattr(module, name)):
            for word in re.findall(r"[A-Za-z_]\w*", text):
                owner = stem if word in defined else imported.get(word)
                if owner is None:
                    continue
                if owner == "_valgebra":
                    # Take a type valgebra or valgebra-jsonschema defines, through the boundary.
                    if getattr(_module(owner), word).__module__.startswith("valgebra_planning"):
                        hidden.add(f"{name}: {owner}.{word}")
                elif owner in INTERNAL or word not in _module(owner).__all__:
                    hidden.add(f"{name}: {owner}.{word}")
    assert not hidden


def test_the_needs_column_names_the_modules_importing_mcp() -> None:
    importing = set()
    for stem in ROWS:
        for node in _tree(stem).body:
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(name == "mcp" or name.startswith("mcp.") for name in modules):
                importing.add(stem)
    assert importing == {stem for stem, (_, needs) in ROWS.items() if needs}
