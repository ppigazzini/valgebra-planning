"""A tool's contract read as slots, and a task read as inputs and a goal.

An agent fills each argument of a call from a value it holds, so a tool is read as its
argument slots, one per property of its input schema, and its result slots, one per field its
output schema guarantees. Each slot is a set valgebra decides over, compiled by
valgebra-jsonschema against the tool's own schema as root. To list the slots, the walk here
reads five keywords itself, ``properties``, ``required``, ``items``, ``prefixItems`` and a bare
``$ref``, which it resolves from the document's root; what a keyword means is the companion's,
so a slot a reading yields is kept only where valgebra proves the whole schema's set inside it.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from valgebra_planning._valgebra import (
    ANNOTATIONS,
    Compiler,
    Unsupported,
    ValidationError,
    Validator,
    anything,
    compile_schema,
    intersection,
    older,
    read_tool,
    upgrade,
)

__all__ = ["Contract", "Slot", "Task", "read_contract", "read_task"]

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
    which a sampler draws members from against ``root``, the document it sits in; ``values``
    is the set it compiles to.
    """

    tool: str
    path: tuple[str, ...]
    schema: object
    values: Validator[Any]
    required: bool
    root: object

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
    #: Whether calling twice acts as calling once: the tool's annotations say it is
    #: read-only or idempotent. A hint the server gives; a retry trusts it.
    idempotent: bool = False
    #: Every annotation hint the tool gives, MCP's defaults where it sets none: a tool setting
    #: none is destructive and open-world.
    hints: frozenset[str] = frozenset()
    #: Whether the server's annotations are trusted, waiving the assumption a retry makes.
    trusted: bool = False
    #: Whether the output schema was shaped from observed results, the server declaring none:
    #: an assumption every link and binding resting on it names.
    inferred: bool = False

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

    ``held`` maps each variable the agent starts with to its slots, read as a result's are:
    the whole value and each field it guarantees. ``goal`` is the set the returned value must
    lie in, and ``goal_schema`` the schema it compiles from.
    """

    name: str
    held: Mapping[str, tuple[Slot, ...]]
    goal: Validator[Any]
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
    """Return the slot of ``schema`` at ``path`` and of every field it guarantees below.

    A field an object declares and does not guarantee is no slot: the planner holds every slot
    after a call, and the verifier reads such a field where valgebra proves it present.
    """
    slots = [Slot(tool, path, schema, compiler.compile(schema), required=True, root=compiler.root)]
    if len(path) >= RESULT_DEPTH:
        return slots
    node = _object_node(compiler, schema)
    if node is None:
        return slots
    properties, required = _properties(node)
    for name in sorted(properties):
        if name not in required:
            continue
        try:
            slots.extend(_result_slots(tool, compiler, properties[name], (*path, name)))
        except Unsupported:
            # The walk compiles a field apart from the resource it sits in, which the
            # companion may refuse though it reads the whole schema: such a field is no slot.
            continue
    return slots


def item_slots(slot: Slot) -> tuple[Slot, ...]:
    """Return the slots of an item of the array ``slot`` holds: the item and what it guarantees.

    The item is read from ``items`` against the slot's own root, its path the array's and
    ``[]``; an array naming no single ``items`` schema, or ``prefixItems``, has none.
    """
    compiler = Compiler(slot.root)
    node = _object_node(compiler, slot.schema)
    if node is None or "prefixItems" in node or not isinstance(node.get("items"), (dict, bool)):
        return ()
    return tuple(_result_slots(slot.tool, compiler, node["items"], (*slot.path, "[]")))


def field_slot(slot: Slot, key: str) -> Slot:
    """Return the slot of the field ``key`` of the object ``slot`` holds, present or not.

    A field selected lies in the projection of its record's type on the key (Castagna 2023,
    §4.2), which valgebra does not compute: the field is read from the ``properties`` of the
    record's schema against its root, a belief holding every value the field takes (Bertoli et
    al. 2006, Def. 3.4) that other keywords of the record may narrow further. A key no property
    declares takes any JSON value. Whether the field is present is not the slot's to say.
    """
    compiler = Compiler(slot.root)
    node = _object_node(compiler, slot.schema)
    properties, _ = _properties(node) if node is not None else ({}, frozenset())
    schema = properties.get(key, True)
    return Slot(
        slot.tool,
        (*slot.path, key),
        schema,
        compiler.compile(schema),
        required=True,
        root=slot.root,
    )


#: MCP's tool annotation hints, each read as the protocol reads a tool that sets none.
_MCP_HINTS = frozenset({"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"})


def _hints(annotations: object) -> frozenset[str]:
    """Return the hints a tool's annotations give, MCP's defaults where it sets none.

    A tool is read-only only where it says so, and destructive and open-world unless it says
    otherwise; destructive is read where the tool is not read-only (MCP 2026-07-28,
    ``ToolAnnotations``). A policy keyed on ``destructiveHint`` reads a motion that sets no
    annotation, and a value that is no boolean is read as absent. Any other hint a tool sets
    true is given as it is.
    """
    given: Mapping[str, Any] = annotations if isinstance(annotations, Mapping) else {}
    hints = {name for name, value in given.items() if value is True and name not in _MCP_HINTS}
    read_only = given.get("readOnlyHint") is True
    if read_only:
        hints.add("readOnlyHint")
    elif given.get("destructiveHint") is not False:
        hints.add("destructiveHint")
    if given.get("idempotentHint") is True:
        hints.add("idempotentHint")
    if given.get("openWorldHint") is not False:
        hints.add("openWorldHint")
    return frozenset(hints)


def _idempotent(annotations: object) -> bool:
    if not isinstance(annotations, Mapping):
        return False
    return annotations.get("readOnlyHint") is True or annotations.get("idempotentHint") is True


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
            root=root,
        )
        for name, subschema in declared.items()
    )
    output = spec.get("outputSchema")
    results: tuple[Slot, ...] = ()
    if output is not None and tool.result is not None:
        out_root = _root(output)
        whole = tool.result
        results = tuple(
            slot
            for slot in _result_slots(tool.name, Compiler(out_root), out_root, ())
            if not slot.path or whole.relation_to(selecting(slot.path, slot.values)) == "subset"
        )
    return Contract(
        name=tool.name,
        arguments=arguments,
        results=results,
        accepts=tool.arguments,
        returns=tool.result,
        plain=_plain(node),
        idempotent=_idempotent(spec.get("annotations")),
        hints=_hints(spec.get("annotations")),
    )


def unclashed(task: Task, contracts: Mapping[str, object]) -> None:
    """Raise `ValueError` where ``task`` names a value it starts with as one of ``contracts``.

    A value's source is read by its name: an input's slots carry the input's name where a
    result's carry its tool, and a flow rule names its sources by input or by tool alike. An
    input named as a tool would read as that tool's result.
    """
    clash = sorted(set(task.held) & set(contracts))
    if clash:
        msg = f"task {task.name!r} names an input as a tool: {', '.join(clash)}"
        raise ValueError(msg)


def keys_read(spec: Mapping[str, Any], keys: frozenset[str], what: str) -> None:
    """Refuse a key ``spec`` holds and its reader does not read, naming it.

    A rule means what its keys say: a key read past in silence, a misspelled ``only`` or
    ``arguments``, would leave a rule weaker than the one its author wrote.
    """
    unknown = sorted(set(spec) - keys)
    if unknown:
        named = ", ".join(map(repr, unknown))
        message = f"{what}: {named} is no key it reads; it reads {', '.join(sorted(keys))}"
        raise ValueError(message)


def flag(spec: Mapping[str, Any], key: str, what: str) -> bool:
    """Return the boolean ``spec`` holds at ``key``, false where absent; refuse any other value."""
    value = spec.get(key, False)
    if not isinstance(value, bool):
        message = f"{what}: {key!r} is true or false, not {type(value).__name__}"
        raise TypeError(message)
    return value


def read_task(name: str, inputs: Mapping[str, object], goal: object) -> Task:
    """Read a task: the schema of each value the agent starts with, and of the goal."""
    held = {}
    for var, schema in inputs.items():
        root = _root(schema)
        held[var] = tuple(_result_slots(var, Compiler(root), root, ()))
    return Task(name=name, held=held, goal=compile_schema(goal), goal_schema=goal)


def listed(values: Sequence[Any]) -> Validator[Any]:
    """Return the set of JSON values ``values`` lists, as an ``enum`` reads them."""
    return compile_schema({"enum": list(values)})


def record_of(fields: Mapping[str, Validator[Any]], *, closed: bool) -> Validator[Any]:
    """Return the objects holding every key of ``fields`` as written, each value in its set.

    valgebra's dict display reads a trailing ``?`` as an optional field and strips it, so a
    key ending in one is required through the companion's ``required``, which reads a key as
    written, and spelled optional with a ``?`` appended, which the display strips once. Any
    other key keeps the display valgebra decides most often. ``closed`` admits no other key;
    otherwise a clause admits every other key with any value. valgebra's ``open`` would admit
    them too, but it opens every record of the schema, those inside the fields' sets as well.
    """
    if any(key.endswith("?") for key in fields):
        present = compile_schema({"type": "object", "required": sorted(fields)})
        spelled = {f"{key}?": values for key, values in fields.items()}
        if closed:
            return intersection(present, Validator(spelled).close())
        return intersection(present, Validator({**spelled, str: anything}))
    if closed:
        return Validator(dict(fields)).close()
    return Validator({**fields, str: anything})


@functools.cache
def having(key: str) -> Validator[Any]:
    """Return the objects holding ``key``, whatever else they hold."""
    return record_of({key: anything}, closed=False)


def selecting(path: tuple[str, ...], values: Validator[Any]) -> Validator[Any]:
    """Return the objects whose field at ``path`` lies in ``values``, whatever else they hold."""
    for key in reversed(path):
        values = record_of({key: values}, closed=False)
    return values


def within(path: tuple[str, ...], values: Validator[Any]) -> Validator[Any]:
    """Return the values whose field at ``path``, wherever each key on it is, lies in ``values``.

    Each key is spelled optional with a ``?`` appended, which valgebra's display strips once,
    so a key is read as written; a clause admits every other key with any value.
    """
    for key in reversed(path):
        values = Validator({f"{key}?": values, str: anything})
    return values


def unread(slot: Slot) -> Slot:
    """Return ``slot`` taking any JSON value: what a reading valgebra did not prove leaves."""
    return Slot(
        slot.tool, slot.path, schema=True, values=anything, required=slot.required, root=slot.root
    )


#: The keywords of a schema document whose keys or items name properties: the names a refusal
#: may print, since the document publishes them.
_NAMING = frozenset({"properties", "dependentRequired", "dependentSchemas"})

#: How many failures a refusal lists before it counts the rest.
_FAILURES_SHOWN = 3


def declared(*roots: object) -> frozenset[str]:
    """Return every property name the schema documents ``roots`` declare."""
    names: set[str] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                naming = key in _NAMING and isinstance(value, dict)
                if naming or (key == "required" and isinstance(value, list)):
                    names.update(name for name in value if isinstance(name, str))
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return frozenset(names)


def contract_names(contract: Contract) -> frozenset[str]:
    """Return the property names a contract's input and output schemas declare."""
    roots = {id(slot.root): slot.root for slot in (*contract.arguments, *contract.results)}
    return declared(*roots.values())


def failures(values: Validator[Any], value: object, names: frozenset[str]) -> str:
    """Return where ``value`` lies outside ``values``: each failure's path and code.

    The value is never written, nor a key ``names`` does not hold, which is written ``*``:
    a refusal reaches whoever made the call, and a value outside its contract, or a key it
    chose, would reach them through it.
    """
    try:
        values.validate(value)
    except ValidationError as error:
        listed = [
            f"{_where(failure['path'], names)}{failure['code']}"
            for failure in error.errors[:_FAILURES_SHOWN]
        ]
        if len(error.errors) > _FAILURES_SHOWN:
            listed.append(f"{len(error.errors) - _FAILURES_SHOWN} more")
        return "; ".join(listed)
    return "no failure valgebra names"


def _where(path: object, names: frozenset[str]) -> str:
    """Return a failure's ``path`` written for a refusal, a colon after it where it is not empty."""
    written = ""
    for step in path if isinstance(path, tuple) else ():
        if isinstance(step, int):
            written += f"[{step}]"
        else:
            key = step if isinstance(step, str) and step in names else "*"
            written = f"{written}.{key}" if written else key
    return f"{written}: " if written else ""
