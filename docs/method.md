---
description: Contracts, the cells, the planner, the verifier, the rules a program is held to, the critic, and the limits.
---

# Method

What each part holds a program to, in full: the contracts a program is read against, the
verifier, the cells and the planner, the rules beside the types, the critic, a run, and the
limits. [How it works](how-it-works.md) covers the same ground in brief, with a diagram for
each step.

## Contracts as slots

An agent fills each argument of a call from a value it holds, so a tool is read as slots
(`valgebra_planning/contracts.py`): one per property of its MCP `inputSchema`, a required name
no property declares included, in the set `additionalProperties` gives it (Core, section
10.3.2.3), and one per field its `outputSchema` guarantees, nested to three
levels, beside the whole result. valgebra-jsonschema compiles each slot against the tool's own
schema as root, and an older draft reads as it means. The walk listing the slots reads
`properties`, `required`, `items` and a bare `$ref` itself, resolving a reference from the
document's root, so a slot it yields is kept only where valgebra proves the whole schema's set
inside it; a field it reads otherwise takes any value, and so does an item of an array it
reads otherwise or not at all, a question on the item then asked of the array. The verifier reads any field below a value the program holds, at any depth, where
valgebra proves every value held there an object holding its key (Castagna 2023): the record's
schema requires it, or a test showing the key, `if "note" in c1.parcel:`, narrows the record to
the values holding it, or the schema of the record's root requires it, as an `allOf` may of a
key its object leaves optional. The field read lies in what its record's schema declares for
the key, an upper bound of its values. A field valgebra proves some value lacks, with no test
narrowing a field on the way, reads refuted, and one whose presence valgebra leaves undecided
reads undecided. A task holds the slots of each value the agent
starts with, read as a result's are, and the set its goal is. A value's source is read by its
name, as a flow rule names its sources by input or by tool alike, so a task names no input as
a tool: `verify`, `critique`, `plan`, `run` and `read_library` raise `ValueError` on one.

A library with `"closedResults": true` in its `tools.json` reads each whole result closed at its
root, through the companion's `closed`: an output schema writing no `additionalProperties` is
an open record, which takes any value at a key no field names, so a detection's result meets a
distance argument wherever a detection may carry a distance's keys. Read closed, a result holds
no key no keyword of its schema evaluates, a key an `allOf` or a `$ref` declares included, and
a nested record stays as written. It is an assumption about the server, which MCP holds only to
the published, open set: each link and binding over such a result names it, a result inside the
declared set and outside the closed one is blamed on it, a result it empties is vacuous, and a
result the companion cannot close is refused naming its tool.

Where the input schema relates its arguments, through `oneOf`, `dependentRequired` or any
keyword beside `type`, `properties`, `required` and `additionalProperties`, a call is accepted
only once the whole argument record is proven inside the input set, each argument under its
name as written, a trailing `?` included. The record is asked as the product of its arguments'
sets, which holds every record the call can make, so a proof stands. Two arguments read from
one value may move together, so the product refutes only where each argument is a constant or
a whole result or input, no two the same, with no field of it tested on the way; otherwise a
record refutes only where valgebra proves the values the program holds carry it jointly, and
the link is undecided where none does.

## The verifier

A program is calls, then one ending: a return, a stop, or a branch on whether a value lies in a
tool's argument or in listed constants (`valgebra_planning/program.py`). A call binds each
argument to a value the program holds or to a JSON constant written into it, and a test lists
JSON constants, each the literal `render` writes, `-1` and `[1, 2]` among them. Its text reads as
Python.

The verifier (`valgebra_planning/verify.py`) walks a program with the set each value lies in,
narrowed by intersection or complement at every branch, and asks valgebra each inclusion the
program rests on: a value inside the argument it fills, the record where the arguments are
related, the returned value inside the goal, and the value a return or a stop names one the
program holds there, a field the result may lack refuted at either. Each link reads proven, refuted or undecided, and a
refuted or undecided one carries a value outside the inclusion where the examples given or the
values drawn from the value's own schema hold one, shrunk before it is shown, shorter and then
nearer zero, while it stays a value the program may hold and outside the inclusion. A concrete
member is a proof, so such a value refutes an undecided link. A field's declared schema may admit more than its record allows, as
where an `allOf` narrows it, so a link on a field its declared set does not prove is asked of
the value the field belongs to: whether the field lies inside on every such value carrying it.
That value is asked in its belief, its own set met with each field of it a test on the way
narrowed, so its answer is exact both ways, and a value drawn for the field is reported only
where valgebra proves a value of that belief carries it there. A constant's link is decided by
membership alone, the constant being its own counterexample where it lies outside. The verifier reads neither the cells nor the
search.

A loop, `for item in ref:` over an array, is walked once for an arbitrary item of the array's
item set, the item and its fields read as a result's are, a test showing a key included. The
item's name holds the item alone, never a field of a value the program held under that name
before. The item set is what the array declares in `items`, which another keyword of the array
may narrow, so a question it leaves open is asked of the value holding the array: whether every
item the body's tests admit carries the field, or lies inside. Where the walk reads no single
`items`, as under `prefixItems`, an `allOf` or a `$ref` beside another keyword, the item set is
every value, and each question is asked of the array so; the loop's body and every step after it
are walked either way. A value refutes a link on an item
only where valgebra proves an item carrying it in an array of a value the program may hold,
proven on a value drawn or given for that value with such an array in place of its own.
No value crosses iterations, since what the
body calls is scoped to its iteration, so the one walk holds for every item. A `return` in the
body ends the program at the first item reaching it: the array is finite, so the iterations are
acyclic, and each return is a terminal state its link proves in the goal, a strong solution's
structure (Cimatti et al. 2003) with no fairness assumed. The block after the loop runs where no
iteration returned or stopped, so it runs under every test such a path made, and a policy's
obligation at a return in the body is read in the states any number of iterations may leave. A
loop is a test on whether its array holds an item: the block after it is walked from the states
before the loop where the array may be empty, and from the states once the body ran where it
may hold one, each ending in its own links, so an act the body surely takes is refuted on the
side holding an item rather than undecided over both; a side the array's set rules out, as the
empty one under `minItems: 1`, is not walked.

## The cells

Every slot, input and goal is a predicate on JSON values, and the cells are their minterms
within each of the seven kinds of JSON value (`valgebra_planning/cells.py`): predicate
abstraction (Graf and Saidi 1997) over the minterms of an effective Boolean algebra (D'Antoni
and Veanes 2017). Each predicate is exactly a union of cells, so an inclusion between cells
implies the inclusion between predicates.

A part of a cell is dropped only where valgebra proves it empty, inhabited where a concrete value
lies in it or valgebra refutes its emptiness, and kept as undecided otherwise: an undecided cell
can cost a program, never admit a wrong one. A part is asked as the meet its cell holds with the
new predicate or its complement, and where valgebra leaves that undecided, as its kind, each
predicate it lies inside and the complement of each it lies outside but for one a predicate
inside it already excludes. The two spell one set, and each is decided where the other is not:
the second holds the complement of a predicate a cell lies outside by a relation, which some meets
of arrays of records need, and the first holds only the predicates it was cut by, which keeps a
cell of many overlapping ranges within what valgebra decides. Schemas valgebra compiles to one validator, a
description or a default apart, are one predicate, since equal validators denote one set.
Relations between two predicates, each asked once, settle most cuts. The concrete values are the edges of common bounds, every listed value, draws
from each schema and objects merged from those draws, since an open record meets another where a
value carries both their keys. The predicates are cut in the order of their names: which meets
valgebra leaves undecided depends on the order the cut splits by and draws in, so a fixed order
makes a library's cells the same however it lists its tools.

Before any cell is cut, the planner keeps only the tools a program can reach the goal through:
callable, every required argument meeting a value the agent may hold, and mattering, a result
meeting the goal or an argument of a tool that matters (SyPet, Feng et al. 2017). A value no
argument and no goal can hold is left out, with its reference.

## The planner

A state maps each value the agent holds to its cells (`valgebra_planning/planner.py`). A call is
enabled when each required argument has a value whose cells lie inside the argument's. A guarded
call branches on a value that only overlaps an argument, calls the tool on the narrowed value in
one branch and goes on in the other; a second overlapping argument is guarded inside it. A
field of a call's result whose declared set lists its members, an `enum` or a `const`, and meets
the goal without lying inside it, is tested on the members it lists that the goal holds: the
`then` branch returns it, and the other takes a strong program from there or stops. A weak
search writes that test before searching past the call. No task input, constant or field listing
no members is tested into the goal, since a free-text value equal to the goal's member is no
outcome a tool reported, and a plan with no program names each such field that meets the goal. A
program is strong when every path returns a value whose cells lie inside the goal's: strong
planning under nondeterministic outcomes (Cimatti et al. 2003), a branch being an observation
(Bertoli et al. 2006). The search deepens on the calls and tests along a
path, so the first strong program is a shallowest one. A state that fails is remembered by what
every rule reads of it, each value's cells and, under a flow rule or mined types, its origin, so a
failure through one origin is never taken for a state another origin reaches. Where none exists, one search at the full
bound lets a branch stop once its sibling returns, and each stop names the values it leaves
uncovered. The tools are tried in their names' order, so a server listing them in another order
gets the same program; a weak program is searched for each tool the start may call first, until
`RANKING` (`valgebra_planning/planner.py`) expansions past the first found, and the one whose stops leave the fewest cells uncovered
is returned, a tie going to the earlier name. A task may name its `goals` in the order it
prefers them, as "grasp, else say why not": its goal is their union, which every return is
verified in, and the planner searches a strong program for the first, else a weak one whose
stops a program toward the next fills, so "grasp or say" grasps where the object is in reach
and says where it would have stopped, where a search toward the union returns the shallowest,
`say` alone. `Plan.preferred` names where each return inside the first goal lies.

A call whose result no later step reads is dropped, since a call can act on the world, and a
branch whose two sides then read the same is collapsed. A call before a test that only one of
its branches needs, as a confirmation the grasp on one side requires, moves into that branch,
so the test is made first and the call only where it is needed; the program is verified again
and the move kept only where every link and every stop read as before.

## Rules a program is held to

Each rule is checked the same way by the planner, which makes no call breaking it, and by the
verifier, which adds a link for it.

- **Errors** (`errors`). Any tool may answer with an error. A call may be retried only where the
  tool's annotations say it is read-only or idempotent, a strong cyclic solution (Daniele et al.
  2000) ending under fairness (Cimatti et al. 2003); any other call needs an error block,
  `try:`/`except ToolError:`, since retrying it may act twice. A call to the tool in its error
  block on values that may be the ones it was given is a retry written out, and held as one:
  the values are read as sets, so another name for the same value, or one read again after the
  error, repeats the call, and only an argument whose set valgebra proves disjoint from the
  failed call's shows the two apart. The planner's error block makes no such call, and reads
  again what the failed call may have changed. A retry's link
  lists what it rests on: the tool's errors transient within the runner's 8 attempts, and the
  annotations, a hint the server gives, unless the library marks the server trusted. A multi
  round-trip request is the client's loop around one call and leaves the plan unchanged.
- **Policies** (`valgebra_planning/policy.py`). An automaton over a program's calls whose guards
  are valgebra sets over the argument record. Along each path a guard is taken surely, possibly or
  never, and a path surely reaching a bad state is refuted, possibly undecided. A state's
  transitions on one call are read in file order, the first whose guards hold taken, before the
  run as at it: a transition is read on what the earlier ones leave, and one they leave no record
  for is reported vacuous. A loop's body may
  run any number of times, so the states a policy may be in when it starts are taken to a
  fixpoint, the states being finite. A state may lapse: a transition of `from`, `after_s` and `to`
  alone is taken where the state has been held that many seconds with no call leaving it, the time
  since the event that entered it last (Bauer et al. 2011, Def. 4.2), so a yes is held for two
  minutes and no longer. A monitor reads it exactly at each event by a clock; no lapse leaves a bad
  state or a state nothing enters, and no chain of them leads back where it began. The walk reads no
  time and takes no lapse: a policy's link that would not be proven with each taken possibly names,
  as an assumption, that the run reaches each call within the seconds of entering the state held
  there, and a plan names the same. A policy reads one program's calls in order
  and, before the
  run, carries no value from one call to another; with `per`, the runner holds one instance per
  value. A transition may read an annotation hint, `destructiveHint` or
  another, on any tool giving it, MCP's default read where a tool sets none: a tool setting no
  annotation is destructive and open-world, and read-only only where it says so; the policy's link
  then lists the hints it reads as an assumption. A transition may read the call's result instead,
  a set it must lie in: the runner and the proxy step it when the result arrives, a call answering
  with an error taking none. The verifier splits the path at such a call, one case for each cell
  of the guards and their complements that valgebra leaves inhabited, the result held in that
  cell and the policy stepped on it, and a branch no value of a case reaches is no path: a program
  walking only under a yes is proven, and one walking after a no is refuted on that case. The
  planner writes the same split as a test where the guard reads one field and lists its members,
  `if c3.confirmed in (True,):`, each side stepped on its own set, so it plans a grasp only under
  the yes; a guard of another form it takes possibly at most. A pending state is an obligation: a path may pass through it and must
  not end in it, as a booking owes a notice. Every path of a program ends, a loop's after any
  number of items, so a pending state is decided before the run, where a monitor reading a prefix
  cannot decide it (Bauer et al. 2011); the runner reads it when the run ends. A bad state is
  never left.
- **Flows** (`valgebra_planning/flows.py`). A value from an untrusted source reaches a sink
  argument only inside the rule's endorsed set, and with `implicit` no sink call runs under a
  test on such a value, in a loop over an array from such a source, or after a loop whose body
  may end the run under such a test: the integrity half of CaMeL (Debenedetti et al. 2025) and
  FIDES (Costa et al. 2025), and Progent's restriction of a tool's arguments (Shi et al. 2025),
  decided over sets. With `only`, the rule holds every origin but its sources, so a sink takes
  a value from a named tool or input on every path, and a literal or another tool's value only
  inside the endorsed set: an identifier the program did not retrieve, the integrity leak of
  Sah et al. 2026, is refuted before the run. An `only` rule with `fresh` refutes a value its
  sources returned before a call that may act, as a task's `fresh` refutes an argument read
  then, or before a call it names where `fresh` lists the tools and hints that withdraw it, the
  author's claim that no other call changes what the sources read, which each link it decides
  names; with `key`, an argument's name, a value from a source reaches the sink only from a call
  that took at that argument the value the sink's call takes, so a distance measured for one
  object is refuted where it is passed for another. Both read the value a name holds on the
  path, not the name: each call's result, loop item and task input is a version of its own, so
  an error's block reads the value from before the call that failed, stale if it was, a name
  bound again holds another value, a branch's bindings stay on its side, and a key holds where
  both calls read one version at one path, or one literal. A source's value failing either
  reaches the sink only inside the endorsed set, as a value from elsewhere does.
- **Fresh reads** (a task's `fresh`). A call whose annotations do not say it is read-only may
  act, and an action may change what a reading before it reported: a distance read before a walk
  is no reading of the distance after it. A task names the arguments it reads fresh, as
  `grasp.distance`; a binding of a value held before the last call that may act before the one
  it fills is refuted, a constant is refuted as no reading, and the planner gives such an argument
  no such value and makes a read-only call again after an action, so a pick that walks first
  estimates the pose after the walk. A loop whose body may act runs after its own action from
  its second item on, so each value held before the loop, the item among them, is held before
  such a call in the body and after the loop. The annotations are the server's word, so a link
  proven over them lists that assumption unless every server is trusted.
- **Postconditions** (`postconditions` in `tools.json`). What a call leaves a later read
  showing is no contract's and none implies it (Mansoor et al. 2026), so a library declares it:
  `{"after": "walk_to", "when": {...}, "read": "estimate_pose", "with": {"object_id":
  "object_id"}, "shows": {...}}` says that whenever `walk_to` returns a result in `when`, a call
  to `estimate_pose` made after it, before any other call that may act, on the value `walk_to`
  was given as `object_id`, returns a value in `shows`. The verifier meets that read with
  `shows`, its fields with the sets `shows` gives them, only where valgebra proves every result
  `walk_to` may have returned there inside `when`, and every link over the read names "the
  postcondition of walk_to on estimate_pose". The pair is matched by reference, so a name it
  reads that is bound again in between leaves the read uncovered. The runner refuses a read
  outside `shows`, and `blame` lays it on the declaration; `check` reports as vacuous a `when`
  meeting no result of its call, and a `shows` meeting no result of its read or holding every
  one.
- **Verify before retry** (`"when": "acted"`). The postcondition then says what the read shows
  whenever the call acted at all, an error answered or not, so `shows` is the weakest trace any
  effect of it leaves, a partial one included, never the full effect a success leaves. After
  the call answers with an error, a read on its values outside `shows` says it did not act, so
  the call made again on the same values is no retry where valgebra proves the read, under the
  tests on the way, outside `shows`, as `if "label_id" in c2:` and its else branch prove it:
  "book is made again where find_booking shows it did not act", naming the declaration and that
  the read sees the effect at once. Another call that may act, a new value under the read's
  name, or the call made again a second time asks again: Mansoor et al. 2026's verify before
  retry, no retry before a read says the effect is absent.
- **Mined types** (`valgebra_planning/mining.py`). Slots holding one observed value are one
  semantic type (APIphany, Guo et al. 2022), and a value fills an argument only where their
  types agree. A call a planned program made, marked `"planned": true` in the log, shares a
  value with the result it was bound from by construction, so its arguments link nothing and a
  wrong binding a plan made is never read back as observed. A task input a log records,
  `{"inputs": {...}}`, is typed as any slot; one it never records fills no argument whose type
  a tool's result was seen in. A mined type is an assumption: the verifier reads none, and a
  plan names each binding that rests on one.
- **Inferred outputs** (`valgebra_planning/mining.py`). A tool listed without an `outputSchema`
  gets one shaped from its results in the observed calls: each kind seen, an object's keys every
  observed one holds required, nothing narrower guessed. It is an assumption as a mined type is:
  every link resting on it lists it beside valgebra's verdict, the plan names each binding that
  rests on it, and a run refuses a result outside it, naming it.
- **Privileges** (`valgebra_planning/privilege.py`). A privilege policy in Progent's format
  (Shi et al. 2025) is a set of calls per tool: each allowing rule's condition, minus every rule
  tried before it, the first rule matching deciding and every other call denied. Whether an update
  narrows a policy is an inclusion between two such sets, which valgebra decides or refutes with a
  call one allows and the other does not, every rule and argument at once. As an automaton whose
  bad state a call outside its tool's set reaches, the policy holds a program like any other.

## The critic

`valgebra_planning/critic.py` reads a program written elsewhere, in the Python an agent writes
against typed tool APIs, verifies it under the rules the task is held to and returns a critique:
each link not proven, the value refuting it, and what the author can change, the test that makes a
call safe for a binding, the call a policy needs first, the source a flow rule trusts, the error
block a call needs. A language model proposes, a sound critic checks (Kambhampati et al. 2024).
The text reads as Python runs it: what follows an `if` runs after each side that runs out, an
absent `else` among them, and what follows a `try` after a handler that runs out, in a loop's body
as outside one, each side carrying the rest, which is read once and shared. A test of `in` against
a string, a substring test in Python, is refused, naming the tuple to write.
Mined types decide nothing here either: a binding crossing two types the observed calls tell apart
is a note, and the program is accepted on its links alone.

`valgebra_planning/codemode.py` gives the tools as typed Python stubs for a model to write
against, each docstring holding the tool's own input and output schemas, since a pattern or a
bound has no Python type; and `repair` hands each critique back to the model for the next
proposal, until a program is accepted or the rounds run out, any callable being the model.

## Specifications that hold vacuously

A check that passes without testing what its author meant is vacuous (Kupferman and Vardi 1999),
and `valgebra_planning/vacuity.py` finds each kind a library can hold, every one a set question:
a goal no value meets or every value meets; an input, an argument record or a result no value
meets, a result the closed reading empties named as such; a policy transition naming no tool, or whose guard no argument record of its tool meets,
and a policy no transition that can fire takes to a bad state; a flow rule naming no tool or
argument, or whose endorsed set meets nothing its sink takes, or holds all of it; a postcondition
whose `when` meets no result of its call, or whose `shows` meets no result of its read or holds
every one; a policy or rule no task is held to. Values drawn from a schema are tried first, a member found proving a set
inhabited; valgebra is asked only where none is, and a question it leaves undecided is reported
as undecided.

A contract may also be wider than its keywords suggest, and `valgebra_planning/meaning.py`
notes seven such readings: an argument a `format` alone types, which takes every string where the
library does not assert `format`; a numeric argument a side of which no bound closes, read by
the companion's `interval`; a `const` with no `type` beside it in an input schema; and a tool
marked destructive, or left unmarked, whose results meet only arguments taking one value or a
boolean and no task's goal, which a plan calls only to fill a gate; and a whole result, read as
declared, that meets an argument or a goal only through keys its output schema does not
declare, asked of it as declared and read closed; and a result, or a field it guarantees, that
any string fills, which a proxy answers to an agent inside the checked value; and a lapse out of a
pending state into one that is not, which ends an obligation with the time and not with the call
that meets it. A note decides nothing: the plan, the proof and the run read the contracts as
written.

## Running a program

`valgebra_planning/run.py` calls the tools a program names and checks each step as it is made: the
argument record against the tool's input set, each flow rule, each policy's step, the result
against the output set, or against the JSON values where the tool declares none, and the returned
value against the goal. A run binds each name to the call that returned its value, on the path it
runs, a loop's item to the call that returned the array, so it holds a flow rule on values where
the verifier holds it on the sets a program's references may bind: a value from an origin the
rule holds reaches the sink only inside the endorsed set; under `fresh` a source's value is one no
call that may act was made after, an error of that call included, and a source that acts answers
with a fresh one; under `key` its call took the value the sink's call takes at the key, compared as
JSON values; under `implicit` the sink's call runs under no test on a value the rule holds, what
follows a loop counted under the test on its array as the verifier counts it. A
coroutine an async tool hands back unawaited is no JSON value: it is refused and closed, never run.
`arun` awaits it instead: one interpreter walks the program and asks for each call, doing no I/O
of its own, `run` making each call and `arun` awaiting it, so the two make the same checks in the
same order. A cancel while `arun` awaits a call ends the run: the call may have acted and
returned anything, its outcome unread, so each policy holds every state the call may have
reached, a transition reading its result taken possibly, as a proxy holds a call it canceled,
and the `CanceledRunError` it raises, an
`asyncio.CancelledError` a timeout reads as its own, carries the run, its outcome `canceled`
naming the call and each obligation a policy may leave pending. Python 3.11's and 3.12's
timeouts read only the exact class as their cancel and let a subclass through, so there `arun`
raises the cancel it received, with what the run left as its note.
It fails closed: the first broken check ends the run before the call is made, the result bound or
the value returned, and `Run.refused` names it. Before the first call each input the task names is
held to its set. A tool raising anything but a tool error ends the run, neither retried nor handed
to an error block, its message naming the exception's class alone, since its text may hold a value;
an interrupt propagates. A call to a tool the run was not given ends the run at that call. A call
that answers with an error may have acted or not, so each policy is held in the states before and
after it, as the verifier reads the error's block. A call no result answered, a timeout or a
protocol error, which `run_on_server` raises as `UnansweredError`, may also have returned
anything, so each policy holds every state the call may have reached, a transition reading its
result taken possibly, as the error's block holds it; a bad state among them ends the run. A proof of a program holds over the contracts, so
a tool answering outside its own contract is the case a run must not pass on. With `enforce=False`
the runner records every break and goes on, a diagnosis rather than a guard. `blame` names why a run
broke: a proven program breaks only where something it rests on fails, a server returning outside
the output schema it declares, a schema inferred from observed calls not holding, an error
outlasting a retry's attempts, a policy's state lapsed before the call that needed it, which is the
caller's timing; an argument record, a policy its calls break or a returned value breaking in a
proven program is a defect to report, and in an unproven one its own unproven link. A run reads a
policy's lapses by a clock, `time.monotonic` or the one it is given, at each call, each result and
its end. A lying annotation and
a wrong mined type break no check a run can see. `valgebra_planning/proxy.py` holds the same checks
as an MCP server in front of another, for an agent no program governs: each call is answered with a
tool error naming the check it breaks, a policy's refusal with the calls that move it on from where
it leaves the run, and the paths and codes of the value's first three failures, at most, but never
the value or a key the schemas do not declare, so a refused result reaches the agent through no
error. A result that passes is answered with the checked value as its text, its JSON where the
server answered with structured content, and with a block other than text only for a tool
`tools.json` names under `"nonTextBlocks"`, since a client hands a model the text and the gate
checks the value. An error
the server answers reaches the agent with a fixed text naming the tool, or as written where its
text lies in the set `tools.json` names for the tool under `"errorTexts"`, and a protocol error
with the tool and the error's code, never its message. The mcp client
checks a result against the output schema the server declares before the proxy does, and its refusal
holds the value, so it is answered with a refusal naming the tool alone, as `run_on_server` ends the
run on the `result` check naming it. A call a policy reads holds the gate from its check to its
result, so each policy reads such calls in the order they act, and a call no policy reads waits on
none. A call to a tool of `"preempts"` or `"afterViolation"`, as a robot's stop, is sent at once,
past that order, since it must act while the call it ends is in flight, and each policy holds
every order the two may take: the stop acting before the call's effect, from the states before
the call, the order the gate took, and the call answering before the stop acts. A refusal in any
of them refuses the stop, and a bad state only one reaches refuses the call's result. A fresh rule
keeps no value its source returned while a preempting call that may act was in flight. A refused call steps no policy, a call the server answers with
an error leaves each policy in the states before and after it, and a call made whose outcome the
gate does not read, any way a sent call ends but a result read, its result refused, the call
canceled or answered by no result, or an exception the client raises, leaves each in every state
the call may have reached, a transition reading its result taken possibly, before anything else
reads the call. A flow rule reads a value's
source from equality with what a source tool returned, an assumption. With `only` that is the
grounding gate of Sah et al. 2026: an argument equal to no value its sources returned is refused,
and a value from a task input with it, since a proxy sees no task. An `only` rule with `fresh`
admits only a value its sources returned since the last call that may act, and one with `key` only
the latest value a source returned for a call taking the sink's call's value at the key. Its listing is the server's, read once for the tools it lists and the
contracts its gate holds, a cursor the server names twice and a listing past `MAX_PAGES` pages
refused,
or, with `spell_out`, each input schema written with `type` and a one-value `enum` for each
`const` and a description for each `pattern`, for a chat template that drops either: a tool is
listed so only where valgebra proves the two input sets equal, and the gate reads the published
ones.

## Limits

- **A program runs at most 4096 paths** (`MAX_PATHS`). Each `if` whose sides run on into what
  follows doubles the paths, and the verifier walks each, so twelve in a row are the most a
  program holds and a thirteenth is refused, naming the count; the planner's programs over the
  examples take at most five.
- **A proxy reads no text a tool declaring no output schema answers.** Such a tool's result is
  answered as the server wrote it, so its text reaches an agent held to no contract, and a
  result field any string fills reaches it inside the checked value: patterns and enums on the
  output schema bound what an agent reads, as they bound what a plan means.
- **Types are the specification.** A slot whose schema admits any string, as an unasserted
  `format` does, takes any string, and a library with `"assertFormat": true` reads `format` as an
  assertion, so a `uuid` slot takes a UUID and free text reaches it only under a test; distinct schemas, patterns, enums and ranges are what make a
  plan mean what it should, and mined types narrow what observations support.
- **What is not modeled.** Side effects beyond the calls a policy reads, the readings a task's
  `fresh` holds stale after a call that may act, and the reads a library's postconditions declare; a field or an item inside a resource with an `$id` of its own that refers into that resource,
  which the walk resolves from the document's root and so reads as any value, a gap the
  companion's reader of the whole schema closes; a field whose record's `properties` do not declare it, which takes any JSON
  value, as one an `anyOf` alone declares; and a program the planner writes holds no
  constant but those its task fixes, tests no key but an acted postcondition's, and loops over
  no array. A program written elsewhere may do all three, and each is checked like any value. A task fixes the constants its
  intent gives in `constants`, as the action a confirmation asks about: the planner writes each
  where its argument is, required or not, the verifier holds every call to the tool to it and
  refutes one leaving it out, since the tool then reads its own default, and a flow rule judges
  it as the literal it is. A choice the planner should not make, as which hand grasps, is a task
  input, typed by its enum.
- **No Python door.** A tool is read from the JSON Schema its server publishes, never from a
  Python function's annotations. Where a call travels as JSON, the MCP SDK, FastMCP and the
  OpenAI Agents SDK publish that schema from the same annotations, so a second reading adds no
  tool, and it disagrees with the published one where Python's equality is kept: valgebra's
  `Literal[1]` refuses `1.0`, which `{"const": 1}` holds, so a program proven over one reading
  can be refuted over the other. A tool called with Python values, a tuple, a dataclass or a
  `datetime`, lies outside the seven kinds of value `json.loads` returns that the cells
  partition (`KINDS` in `valgebra_planning/cells.py`), and would need an abstraction over
  valgebra's whole universe, not a reader. Refused until a consumer calls tools with Python
  values under no published JSON Schema.
- **Verify before retry reads the declaration, never the world.** Whether a call acted is never
  seen, so the runner checks no `"when": "acted"` postcondition, and under delayed visibility,
  where the read reports an effect absent that holds, the call is made twice: the assumption
  the proof names. On the path where the read rules the effect out, each policy is still read
  in the states of both sides, so a policy a second call would break there is reported possibly
  broken.
- **The planner writes the block for one key alone.** Where an acted postcondition's `shows`
  requires one key, `{"required": ["label_id"]}`, a weak program answers the call's error with
  the read retried on its own error, `if "label_id" in c1:`, the field it opens returned or
  passed on, and on the other side the call made once more with a stop on its error; any other
  `shows` gets no block, since the language writes no other test whose side valgebra proves
  outside it.
- **The planner reads no postcondition on a call that returned.** Its search holds each value
  in cells of sets fixed before it starts, among which no postcondition's `shows` is, so a
  program resting on one, a read after a walk that arrived, is written, by hand or by a model in
  code mode, and proven by the critic.
- **A stop is not a question.** Each weak plan of the parcel library stops on a value a tool
  returned, a parcel's state or country. Asking the user for one inside the set the next call
  takes would put a value they make up where the tool's must be: the integrity leak a flow rule
  with `only` refutes. A stop on a task input says the task admits inputs no tool takes, which
  its input schema fixes before any run.
- **A stop is not replanned.** The cells are exact for every argument set, so a run's value in
  a stopped cell meets no argument the search did not try: a plan from the run's values gains
  only past the depth bound, which a larger bound gives, or where valgebra left a cell
  undecided. From every stop of the parcel library, its values held as constants, no plan exists
  at depth 8. A resumed segment would also have to start from the run's policy states and keep
  each held value's origin, or a flow rule would no longer see it.
- **A policy carries no value between calls before the run.** A rule about one parcel across two
  calls is read as one about any two, so a program approving one parcel and booking another
  passes a rule that a booking follows its own parcel's approval. That one call takes the value
  another returned is a flow rule with `only`. A policy naming an argument in `per` holds an
  instance for each value of it at run time, the parametric monitoring of Roşu and Chen 2012, so
  the runner and the proxy refuse a booking of a parcel no approval was for; the walk holds no
  value's identity, takes every transition of such a policy possibly at most, and proves none of
  its links. A proxy reads no pending state: MCP 2026-07-28 has no session, and a proxy sees no
  end of a task.
- **A proof reads no time.** A program is walked before it runs, so a policy's lapse is taken by no
  walk and no search: a link that needs one not to come due is proven under the timing it names,
  which a run and a proxy hold by their clocks, and a late call is refused there. A proxy's clock is
  its process's, so one started again holds no state of the last; a lapse is read at each call, and
  one that comes due while a call is under way withdraws nothing from it; and where a call that
  overtakes another leaves open when a state was entered, every state it may leave held is held.
- **A call is given no input, and a person's answer is forwarded only where a library says.** A
  server may answer a call by asking for input: an elicitation, a sampling, its roots, or an
  interaction it waits on, MCP 2026-07-28's `input_required`. The runner gives none, so the call
  fails as a tool error, which an error block takes or which ends the run: a value a user or a
  model supplied there would reach the call held to no contract the program was proven against,
  the leak a stop answered by a user would be. The proxy forwards to its client a request for a
  person's answer, a form or a URL, for a tool `tools.json` names under
  `"forwardsElicitation"`, as a robot's confirmation asks the user: the round may have acted, so
  each policy holds the states before it; the server's state travels unread inside the proxy's,
  which the mcp SDK's request-state boundary seals under a key the process alone holds and binds
  to the call, the principal the token names, its client, issuer and subject, and an expiry; and
  the answers that resume it reach the server only inside the schema each form asked under, its
  `format` asserted, and a decline or a cancel only with no content. The
  resumed call retries the request its first round made (MCP 2026-07-28), so it is held to the
  contract and the flow rules and steps no policy again, and once it is answered with a result
  each policy no other call stepped meanwhile leaves the states before it, the one call having
  been made, before the result steps it. Any other request is answered with a tool
  error. A form's answer is the client's to give, and no text of MCP keeps a client's model from
  giving it; a URL keeps the answer off the client and the proxy alike, so a consent no steered
  model may give is asked by URL.
- **A proxy over HTTP is one gate for every client holding a token.** `valgebra-planning proxy`
  serves the agent that starts it over stdio, and with `--http` every client holding a token
  issued for it over Streamable HTTP, through one gate: a policy reads the calls of all of them,
  and a flow rule pools what each was returned
  ([one gate](code-mode.md#the-proxy-for-an-agent-whose-program-is-not-seen)). A policy or a flow
  rule naming `"scope": "principal"` holds each principal apart, the client, issuer and subject
  its token names as the mcp SDK reads them: two operators' consents do not mix, and nor do two
  agents' of one operator, since each agent is a client of its own. Every principal's calls
  reach the one server, so a bad state any principal's instance holds still stops every call, and
  a call that may act withdraws every principal's fresh values. No session keys
  either: MCP 2026-07-28 leaves state across calls to handles a server mints as tool arguments,
  which would change the signatures the proxy lists. A gate served to any client is moved by any
  client, so the proxy serves HTTP only as an OAuth 2.1 resource server, as MCP 2026-07-28 has
  an HTTP server authorize: a request carries a bearer token issued for the proxy's URL as its
  audience, unexpired and holding the scopes asked, or is answered 401, or 403 where a scope is
  missing; a Host other than the
  URL's is answered 421 and an Origin not listed 403. Reading a token is the verifier's the
  deployment names, and issuing one its authorization server's; the proxy reaches its server on
  a connection of its own, so no client's token goes upstream. Which client holds a value is
  read by no rule, so a value one client was returned grounds another's call.
- **Search bounds.** Six calls and tests a path by default, and the first 16 bindings of a call.
  `plan` takes another depth, and so do a task's `depth` and the command line's `--depth`. A plan
  with no program says why in `Plan.reason`: the bound cut a path short of the goal, and whether
  a deeper search finds one is not known, or no path reached it, so none does from the bindings
  the search tries. It also names each argument of a tool the goal needs that no tool's result
  guarantees a value for, which a task input fills only under a test, as a free-text name tested
  into `left|right`: the planner writes no constant, so such an argument wants a task input of its
  own. The goal needs a tool whose result meets it, callable or not, and one whose result fills a
  required argument of such a tool that no input fills, so an argument of a tool that could only
  fill what an input already fills is not named. It names apart those of a tool only a policy names, whose
  results lead to no goal, and each result field listing its members that meets the goal and lies
  outside it, which only a test into the goal would narrow.
