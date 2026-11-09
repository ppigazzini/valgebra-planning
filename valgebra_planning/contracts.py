"""A tool's contract read as slots, and a task read as inputs and a goal.

An agent fills each argument of a call from a value it holds, so a tool is read as its
argument slots, one per property of its input schema, and its result slots, one per field its
output schema guarantees. Each slot is a set valgebra decides over, compiled by
valgebra-jsonschema against the tool's own schema as root, so a ``$ref`` inside a property
resolves where the tool wrote it. Nothing here reads a keyword: the companion owns that.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from valgebra_planning._valgebra import (
    ANNOTATIONS,
    Compiler,
    Unsupported,
    Validator,
    compile_schema,
    older,
    read_tool,
    upgrade,
)

#: How deep a result field is read for the fields it guarantees in turn. A value three
#: objects down is still one an agent can pass on by its path.
RESULT_DEPTH = 3

#: Root keywords an input schema may carry and still constrain each argument on its own,
#: so that every argument proven inside its slot makes the whole record a member.
_PLAIN = frozenset({"type", "properties", "required", "additionalProperties"}) | ANNOTATIONS

#: Keywords that name or hold subschemas without asserting anything about the instance.
_HOLDERS = frozenset({"$defs", "definitions", "$id", "$anchor"})


@dataclass(frozen=True, slots=True)
class Slot:
    """One value a tool takes or returns: where it sits, and the set it lies in.

    ``path`` is the argument's name for an argument, and the field names leading to it for a
    result, the empty path being the whole result. ``schema`` is the subschema as written,
    which a sampler draws members from; ``values`` is the set it compiles to.
    """

    tool: str
    path: tuple[str, ...]
    schema: object
    values: Validator[Any]
    required: bool

    @property
    def name(self) -> str:
        """Return the slot as a program writes it: ``tool.path``."""
        return ".".join((self.tool, *self.path)) if self.path else self.tool


@dataclass(frozen=True, slots=True)
class Contract:
    """A tool's slots, with its whole input set and whether its arguments are independent.

    ``plain`` holds when the input schema constrains each argument on its own: each one
    proven inside its slot, with the required ones present, makes the record a member.
    Otherwise a call is accepted only once the record itself is proven inside ``accepts``.
    """

    name: str
    arguments: tuple[Slot, ...]
    results: tuple[Slot, ...]
    accepts: Validator[Any]
    returns: Validator[Any] | None
    plain: bool

    def argument(self, name: str) -> Slot:
        """Return the argument slot called ``name``, or raise `KeyError`."""
        for slot in self.arguments:
            if slot.path == (name,):
                return slot
        raise KeyError(name)

    def result(self, *path: str) -> Slot:
        """Return the result slot at ``path``, or raise `KeyError`."""
        for slot in self.results:
            if slot.path == path:
                return slot
        raise KeyError(".".join(path))


@dataclass(frozen=True, slots=True)
class Task:
    """What an agent holds and what it must return.

    ``inputs`` maps each variable the agent starts with to the schema its value lies in, and
    ``goal`` is the schema the returned value must lie in.
    """

    name: str
    inputs: Mapping[str, Validator[Any]]
    goal: Validator[Any]
    input_schemas: Mapping[str, object]
    goal_schema: object


def _root(schema: object) -> object:
    draft = older(schema)
    return upgrade(schema, draft) if draft is not None else schema


def _object_node(compiler: Compiler, schema: object) -> dict[str, Any] | None:
    """Return the object schema a root names, following bare references, or None."""
    here = schema
    for _ in range(8):
        if not isinstance(here, dict):
            return None
        if "$ref" in here and not set(here) - ANNOTATIONS - _HOLDERS - {"$ref"}:
            here = compiler.resolve(here["$ref"])
            continue
        return here
    return None


def _properties(node: dict[str, Any]) -> tuple[dict[str, Any], frozenset[str]]:
    properties = node.get("properties", {})
    required = node.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        return {}, frozenset()
    return properties, frozenset(name for name in required if isinstance(name, str))


def _plain(node: dict[str, Any]) -> bool:
    return set(node) - _HOLDERS <= _PLAIN


def _result_slots(
    tool: str, compiler: Compiler, schema: object, path: tuple[str, ...]
) -> list[Slot]:
    """Return the slot of ``schema`` at ``path`` and of every field it guarantees below."""
    slots = [Slot(tool, path, schema, compiler.compile(schema), required=True)]
    if len(path) >= RESULT_DEPTH:
        return slots
    node = _object_node(compiler, schema)
    if node is None:
        return slots
    properties, required = _properties(node)
    for name in sorted(required & set(properties)):
        slots.extend(_result_slots(tool, compiler, properties[name], (*path, name)))
    return slots


def read_contract(spec: Mapping[str, Any]) -> Contract:
    """Read one entry of a server's tool list into its contract.

    Raises `Unsupported` for what valgebra-jsonschema refuses to read, and for an input
    schema whose properties it cannot list.
    """
    tool = read_tool(spec)
    root = _root(spec["inputSchema"])
    compiler = Compiler(root)
    node = _object_node(compiler, root)
    if node is None:
        msg = f"tool {tool.name!r} has an inputSchema whose properties cannot be listed"
        raise Unsupported(msg)
    properties, required = _properties(node)
    # A required name no property declares takes any JSON value, and still has to be filled.
    declared = {**properties, **dict.fromkeys(sorted(required - set(properties)), True)}
    arguments = tuple(
        Slot(
            tool.name,
            (name,),
            subschema,
            compiler.compile(subschema),
            required=name in required,
        )
        for name, subschema in declared.items()
    )
    output = spec.get("outputSchema")
    results: tuple[Slot, ...] = ()
    if output is not None:
        out_root = _root(output)
        results = tuple(_result_slots(tool.name, Compiler(out_root), out_root, ()))
    return Contract(
        name=tool.name,
        arguments=arguments,
        results=results,
        accepts=tool.arguments,
        returns=tool.result,
        plain=_plain(node),
    )


def read_task(name: str, inputs: Mapping[str, object], goal: object) -> Task:
    """Read a task: the schema of each value the agent starts with, and of the goal."""
    return Task(
        name=name,
        inputs={var: compile_schema(schema) for var, schema in inputs.items()},
        goal=compile_schema(goal),
        input_schemas=dict(inputs),
        goal_schema=goal,
    )
