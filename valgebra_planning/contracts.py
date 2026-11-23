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
from dataclasses import dataclass, replace
from typing import Any

from valgebra_planning._valgebra import (
    ANNOTATIONS,
    Compiler,
    Unsupported,
    ValidationError,
    Validator,
    anything,
    closed,
    compile_schema,
    intersection,
    older,
    read_tool,
    union,
    upgrade,
)

__all__ = [
    "Contract",
    "Postcondition",
    "Slot",
    "Task",
    "read_contract",
    "read_postcondition",
    "read_task",
]

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
    #: Whether ``format`` in the schema is read as an assertion, as the library asks: every set
    #: read from the slot's schema later is read alike, so a proof and a run hold one set.
    asserted: bool = False

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
    #: The output set the server declares, where the whole result is read closed: ``returns``
    #: then holds the narrower set, an assumption every link and binding resting on a result
    #: names. None where the result is read as declared.
    declared: Validator[Any] | None = None

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
    lie in, and ``goal_schema`` the schema it compiles from. ``stops`` names, for a tool or an
    input, the set its whole value may lie in where a path stops on it: an outcome the task
    accepts as an end, as an object not found. ``preferred`` holds the goals a task names in
    the order it prefers them, each set with its schema, where it names more than one: ``goal``
    is then their union, the set every return is verified in, and ``goal_schema`` the first's.
    ``constants`` holds, for an argument of a tool, the JSON value the task's intent fixes it to,
    as ``(tool, argument, value)``: the planner writes it there, and the verifier holds every
    call to the tool to it. ``fresh`` holds the arguments, as ``(tool, argument)``, whose value
    is read after the last call before it that may act, as a distance a walk changes.
    """

    name: str
    held: Mapping[str, tuple[Slot, ...]]
    goal: Validator[Any]
    goal_schema: object
    stops: tuple[tuple[str, Validator[Any]], ...] = ()
    preferred: tuple[tuple[Validator[Any], object], ...] = ()
    constants: tuple[tuple[str, str, Any], ...] = ()
    fresh: tuple[tuple[str, str], ...] = ()


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
    slots = [
        Slot(
            tool,
            path,
            schema,
            compiler.compile(schema),
            required=True,
            root=compiler.root,
            asserted=compiler.assert_format,
        )
    ]
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
    compiler = Compiler(slot.root, assert_format=slot.asserted)
    node = _object_node(compiler, slot.schema)
    if node is None or "prefixItems" in node or not isinstance(node.get("items"), (dict, bool)):
        return ()
    return tuple(_result_slots(slot.tool, compiler, node["items"], (*slot.path, "[]")))


def field_slot(slot: Slot, key: str) -> Slot:
    """Return the slot of the field ``key`` of the object ``slot`` holds, present or not.

    A field selected lies in the projection of its record's type on the key (Castagna 2023,
    §4.2), which valgebra does not compute: the field is read from the ``properties`` of the
    record's schema against its root, a belief holding every value the field takes (Bertoli et
    al. 2006, §3) that other keywords of the record may narrow further. A key no property
    declares takes any JSON value. Whether the field is present is not the slot's to say.
    """
    compiler = Compiler(slot.root, assert_format=slot.asserted)
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
        asserted=slot.asserted,
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


def read_contract(
    spec: Mapping[str, Any], *, assert_format: bool = False, closed_results: bool = False
) -> Contract:
    """Read one entry of a server's tool list into its contract.

    With ``assert_format``, ``format`` is read as an assertion, as valgebra-jsonschema reads it
    on request, in every set the contract holds and every set read from its slots later.
    With ``closed_results``, the whole result is read closed, as valgebra-jsonschema's `closed`
    reads it: an open record takes any value at a key no field names, and a closed one none
    (Castagna 2023, §3.2). `unevaluatedProperties: false` at the root refuses a key exactly where
    no keyword evaluated it, through a `$ref` as beside it (Attouche et al. 2024, §2.2), where
    `additionalProperties` reads only the properties beside it (Attouche et al. 2024, §3.2). A
    server's structured result conforms to its output schema as published (MCP 2026-07-28,
    Output Schema), the open set, so the closed set assumes the server writes no key its schema
    does not declare, and `Contract.declared` keeps the published one. A field keeps the set its
    record declares, which a closing at the root leaves as written. Raises `Unsupported` for
    what valgebra-jsonschema refuses to read, a closed result among them, and for an input
    schema whose properties it cannot list.
    """
    tool = read_tool(spec, assert_format=assert_format)
    root = _root(spec["inputSchema"])
    compiler = Compiler(root, assert_format=assert_format)
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
            asserted=assert_format,
        )
        for name, subschema in declared.items()
    )
    output = spec.get("outputSchema")
    results: tuple[Slot, ...] = ()
    returns, declared = tool.result, None
    if output is not None and tool.result is not None:
        out_root = _root(output)
        whole = tool.result
        results = tuple(
            slot
            for slot in _result_slots(
                tool.name, Compiler(out_root, assert_format=assert_format), out_root, ()
            )
            if not slot.path or whole.relation_to(selecting(slot.path, slot.values)) == "subset"
        )
        if closed_results:
            shut = closed(output)
            try:
                returns = Compiler(shut, assert_format=assert_format).compile(shut)
            except Unsupported as error:
                msg = f"tool {tool.name!r}: its output schema read closed: {error}"
                raise Unsupported(msg) from error
            declared = whole
            results = (replace(results[0], schema=shut, values=returns, root=shut), *results[1:])
    return Contract(
        name=tool.name,
        arguments=arguments,
        results=results,
        accepts=tool.arguments,
        returns=returns,
        plain=_plain(node),
        idempotent=_idempotent(spec.get("annotations")),
        hints=_hints(spec.get("annotations")),
        declared=declared,
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
    for tool, argument, value in task.constants:
        if tool not in contracts:
            # A tool the caller leaves out, as the planner leaves out one no program needs,
            # takes no call; `fixes` reads the constant against every tool listed.
            continue
        slots = [s for s in getattr(contracts[tool], "arguments", ()) if s.path == (argument,)]
        if not slots:
            msg = f"task {task.name!r} fixes {tool}.{argument}, which is no argument of a tool"
            raise ValueError(msg)
        if not slots[0].values.is_valid(value):
            msg = f"task {task.name!r} fixes {tool}.{argument} to a value outside its set"
            raise ValueError(msg)


def fixes(task: Task, contracts: Mapping[str, object]) -> None:
    """Raise `ValueError` where ``task`` fixes an argument of a tool ``contracts`` does not list.

    `unclashed` reads each constant against the tools it is given, which may be some of them.
    """
    if missing := sorted({tool for tool, _, _ in task.constants} - set(contracts)):
        msg = f"task {task.name!r} fixes an argument of a tool not listed: {', '.join(missing)}"
        raise ValueError(msg)
    for tool, argument in task.fresh:
        slots = getattr(contracts.get(tool), "arguments", ())
        if not any(slot.path == (argument,) for slot in slots):
            msg = (
                f"task {task.name!r} reads {tool}.{argument} fresh, which is no argument of a tool"
            )
            raise ValueError(msg)
    unclashed(task, contracts)


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


def read_task(
    name: str,
    inputs: Mapping[str, object],
    goal: object,
    *,
    stops: Mapping[str, object] | None = None,
    fallbacks: Sequence[object] = (),
    constants: Mapping[str, Any] | None = None,
    fresh: Sequence[str] = (),
    assert_format: bool = False,
) -> Task:
    """Read a task: the schema of each value the agent starts with, and of the goal.

    ``stops`` maps a tool or an input to the JSON Schema of the whole value a path may stop on
    there. ``fallbacks`` are goals the task accepts where ``goal`` is not reached, in the order
    it prefers them: the task's goal is then the union of all, and the planner returns in the
    first where it can. ``constants`` maps ``tool.argument`` to the JSON value the task's intent
    fixes it to, as ``{"confirm_with_user.action": "grasp"}``. ``fresh`` names each
    ``tool.argument`` whose value is read after the last call before it that may act. With
    ``assert_format``, ``format`` is read as an assertion, as `read_contract` reads it.
    """
    held = {}
    for var, schema in inputs.items():
        root = _root(schema)
        compiler = Compiler(root, assert_format=assert_format)
        held[var] = tuple(_result_slots(var, compiler, root, ()))
    goals = tuple(
        (compile_schema(schema, assert_format=assert_format), schema)
        for schema in (goal, *fallbacks)
    )
    tolerated = tuple(
        (origin, compile_schema(schema, assert_format=assert_format))
        for origin, schema in (stops or {}).items()
    )
    fixed = [(*_sink(name, sink, "a constant"), value) for sink, value in (constants or {}).items()]
    return Task(
        name=name,
        held=held,
        goal=goals[0][0] if len(goals) == 1 else union(*(values for values, _ in goals)),
        goal_schema=goal,
        stops=tolerated,
        preferred=goals if len(goals) > 1 else (),
        constants=tuple(fixed),
        fresh=tuple(_sink(name, sink, "a fresh argument") for sink in fresh),
    )


def _sink(task: str, sink: str, what: str) -> tuple[str, str]:
    """Return the tool and the argument ``tool.argument`` names, or raise `ValueError`."""
    tool, dot, argument = sink.rpartition(".")
    if not dot or not tool or not argument:
        msg = f"task {task!r}: {what} names `tool.argument`, not {sink!r}"
        raise ValueError(msg)
    return tool, argument


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
        slot.tool,
        slot.path,
        schema=True,
        values=anything,
        required=slot.required,
        root=slot.root,
        asserted=slot.asserted,
    )


#: The keywords of a schema document whose keys or items name properties: the names a refusal
#: may print, since the document publishes them.
_NAMING = frozenset({"properties", "dependentRequired", "dependentSchemas"})

#: How many failures a refusal lists before it counts the rest.
_FAILURES_SHOWN = 3


#: The keys a postcondition reads.
_POSTCONDITION_KEYS = frozenset({"after", "when", "read", "with", "shows"})


@dataclass(frozen=True, slots=True)
class Postcondition:
    """What a library declares a call that returned leaves a later read showing.

    Whenever ``after`` returns a result in ``when``, a call to ``read`` made after it, before
    any other call that may act, on the values ``after`` was given at each pair of ``given``,
    returns a value in ``shows``. It is the library's claim about the world, which no
    contract states and none implies (Mansoor et al. 2026, §4.3): the verifier reads it as an
    assumption, and the runner checks it on each read it covers.
    """

    after: str
    when: Validator[Any]
    read: str
    #: Each argument of ``read`` and the argument of ``after`` whose value it takes.
    given: tuple[tuple[str, str], ...]
    shows: Validator[Any]
    #: ``when`` and ``shows`` as written, the documents a refusal reads names from.
    schemas: tuple[object, object]
    #: Whether ``format`` in them is read as an assertion, as the library asks.
    asserted: bool = False

    def field(self, path: tuple[str, ...]) -> Validator[Any]:
        """Return the set the field at ``path`` of a read this covers lies in.

        A value inside ``shows`` carries each field inside the set the ``properties`` of
        ``shows`` give it, read as `field_slot` reads a record's field; below an array's items
        it takes any value.
        """
        slot = Slot(
            self.read,
            (),
            self.schemas[1],
            self.shows,
            required=True,
            root=self.schemas[1],
            asserted=self.asserted,
        )
        for key in path:
            if key == "[]":
                return anything
            slot = field_slot(slot, key)
        return slot.values

    @property
    def name(self) -> str:
        """Return the postcondition as a link names what it rests on."""
        return f"the postcondition of {self.after} on {self.read}"


def _tool_of(
    spec: Mapping[str, Any], key: str, contracts: Mapping[str, Contract], what: str
) -> str:
    """Return the tool ``spec`` names at ``key`` where ``contracts`` lists it; else raise."""
    tool = spec.get(key)
    if not isinstance(tool, str) or tool not in contracts:
        msg = f"{what}: {tool!r} is no tool listed"
        raise ValueError(msg)
    return tool


def _argument_of(contracts: Mapping[str, Contract], tool: str, name: object, what: str) -> str:
    """Return ``name`` where it is an argument of ``tool``; raise `ValueError` naming it."""
    if not isinstance(name, str) or not any(
        slot.path == (name,) for slot in contracts[tool].arguments
    ):
        msg = f"{what}: {name!r} is no argument of {tool}"
        raise ValueError(msg)
    return name


def read_postcondition(
    spec: Mapping[str, Any], contracts: Mapping[str, Contract], *, assert_format: bool = False
) -> Postcondition:
    """Read one postcondition of a library over the tools ``contracts`` lists.

    ``spec`` holds ``after`` and ``read``, two tools, ``with``, a non-empty object naming
    each argument of ``read`` the claim fixes and the argument of ``after`` whose value it
    takes, ``shows``, a JSON Schema over ``read``'s result, and optionally ``when``, a JSON
    Schema over ``after``'s result, every result where absent. A key it does not read, a tool
    not listed and a ``with`` naming no argument are a `ValueError`, and a ``when`` or
    ``shows`` that is no schema a `TypeError`.
    """
    what = f"postcondition of {spec.get('after')!r} on {spec.get('read')!r}"
    keys_read(spec, _POSTCONDITION_KEYS, what)
    after, read = _tool_of(spec, "after", contracts, what), _tool_of(spec, "read", contracts, what)
    pairs = spec.get("with")
    if not isinstance(pairs, dict) or not pairs:
        msg = f"{what}: 'with' names each argument of {read} and the argument of {after} it takes"
        raise ValueError(msg)
    given = tuple(
        (_argument_of(contracts, read, mine, what), _argument_of(contracts, after, theirs, what))
        for mine, theirs in pairs.items()
    )
    when, shows = spec.get("when", True), spec.get("shows")
    for key, schema in (("when", when), ("shows", shows)):
        if not isinstance(schema, dict | bool):
            msg = f"{what}: {key!r} is a JSON Schema, not {type(schema).__name__}"
            raise TypeError(msg)
    return Postcondition(
        after,
        compile_schema(when, assert_format=assert_format),
        read,
        given,
        compile_schema(shows, assert_format=assert_format),
        (when, shows),
        assert_format,
    )


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
