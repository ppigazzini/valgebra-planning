# Method

## Contracts as slots

An agent fills each argument of a call from a value it holds, so a tool is read as slots
(`valgebra_planning/contracts.py`): one per property of its MCP `inputSchema`, a required name
no property declares included, and one per field its `outputSchema` guarantees, nested to three
levels, beside the whole result. valgebra-jsonschema compiles each slot against the tool's own
schema as root, so a `$ref` resolves where the tool wrote it and an older draft reads as it
means. A field the output schema declares and does not guarantee is a slot held apart: a
program holds it only in the branch of a test showing its key, `if "note" in c1.parcel:`
(occurrence typing, the test refining the record). A task holds the slots of each value the agent
starts with, read as a result's are, and the set its goal is.

Where the input schema relates its arguments, through `oneOf`, `dependentRequired` or any
keyword beside `type`, `properties`, `required` and `additionalProperties`, a call is accepted
only once the whole argument record is proven inside the input set.

## The verifier

A program is calls, then one ending: a return, a stop, or a branch on whether a value lies in a
tool's argument or in listed constants (`valgebra_planning/program.py`). A call binds each
argument to a value the program holds or to a JSON constant written into it. Its text reads as
Python.

The verifier (`valgebra_planning/verify.py`) walks a program with the set each value lies in,
narrowed by intersection or complement at every branch, and asks valgebra each inclusion the
program rests on: a value inside the argument it fills, the record where the arguments are
related, the returned value inside the goal. Each link reads proven, refuted or undecided, and a
refuted or undecided one carries a value outside the inclusion where the examples given or the
values drawn from the value's own schema hold one. A concrete member is a proof, so such a value
refutes an undecided link. A constant's link is decided by membership alone, the constant being
its own counterexample where it lies outside. The verifier reads neither the cells nor the
search.

A loop, `for item in ref:` over an array, is walked once for an arbitrary item of the array's
item set, the item's fields read as a result's are. Nothing crosses iterations, since what the
body calls is scoped to its iteration and a body never returns, so the one walk holds for every
item.

## The cells

Every slot, input and goal is a predicate on JSON values, and the cells are their minterms
within each of the seven kinds of JSON value (`valgebra_planning/cells.py`): predicate
abstraction (Graf and Saidi 1997) over the minterms of an effective Boolean algebra (D'Antoni
and Veanes 2017). Each predicate is exactly a union of cells, so an inclusion between cells
implies the inclusion between predicates.

A part of a cell is dropped only where valgebra proves it empty, inhabited where a concrete value
lies in it or valgebra refutes its emptiness, and kept as undecided otherwise: an undecided cell
can cost a program, never admit a wrong one. Relations between two predicates, each asked once,
settle most cuts. The concrete values are the edges of common bounds, every listed value, draws
from each schema and objects merged from those draws, since an open record meets another where a
value carries both their keys.

Before any cell is cut, the planner keeps only the tools a program can reach the goal through:
callable, every required argument meeting a value the agent may hold, and mattering, a result
meeting the goal or an argument of a tool that matters (SyPet, Feng et al. 2017). A value no
argument and no goal can hold is left out, with its reference.

## The planner

A state maps each value the agent holds to its cells (`valgebra_planning/planner.py`). A call is
enabled when each required argument has a value whose cells lie inside the argument's. A guarded
call branches on a value that only overlaps an argument, calls the tool on the narrowed value in
one branch and goes on in the other; a second overlapping argument is guarded inside it. A
program is strong when every path returns a value whose cells lie inside the goal's: strong
planning under nondeterministic outcomes (Cimatti et al. 2003), a branch being an observation
(Bertoli et al. 2006). The search deepens on the calls and tests along a
path, so the first strong program is a shallowest one. Where none exists, one search at the full
bound lets a branch stop once its sibling returns, and each stop names the values it leaves
uncovered.

A call whose result no later step reads is dropped, since a call can act on the world, and a
branch whose two sides then read the same is collapsed.

## Rules a program is held to

Each rule is checked the same way by the planner, which makes no call breaking it, and by the
verifier, which adds a link for it.

- **Errors** (`errors`). Any tool may answer with an error. A call may be retried only where the
  tool's annotations say it is read-only or idempotent, a strong cyclic solution ending under
  fairness (Daniele et al. 2000); any other call needs an error block,
  `try:`/`except ToolError:`, since retrying it may act twice. A retry's link lists what it
  rests on: the tool's errors transient within the runner's 8 attempts, and the annotations, a
  hint the server gives, unless the library marks the server trusted. A multi round-trip request is the client's loop around
  one call and leaves the plan unchanged.
- **Policies** (`valgebra_planning/policy.py`). An automaton over a program's calls whose
  guards are valgebra sets over the argument record. Along each path a guard is taken surely,
  possibly or never, and a path surely reaching a bad state is refuted, possibly undecided. A
  loop's body may run any number of times, so the states a policy may be in when it starts are
  taken to a fixpoint, the states being finite. A policy reads one program's calls in order and
  carries no value from one call to another. A transition may read an annotation hint,
  `destructiveHint` or another, on any tool setting it; the policy's link then lists the hints it
  reads as an assumption.
- **Flows** (`valgebra_planning/flows.py`). A value from an untrusted source reaches a sink
  argument only inside the rule's endorsed set, and with `implicit` no sink call runs under a
  test on such a value or in a loop over an array from such a source: the integrity half of CaMeL (Debenedetti et al. 2025), FIDES (Costa
  et al. 2025) and Progent (Shi et al. 2025), decided over sets.
- **Mined types** (`valgebra_planning/mining.py`). Slots holding one observed value are one
  semantic type (APIphany, Guo et al. 2022), and a value fills an argument only where their
  types agree. A mined type is an assumption: the verifier reads none, and a plan names each
  binding that rests on one.
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
Mined types decide nothing here either: a binding crossing two types the observed calls tell apart
is a note, and the program is accepted on its links alone.

## Specifications that hold vacuously

A check that passes without testing what its author meant is vacuous (Kupferman and Vardi 2003),
and `valgebra_planning/vacuity.py` finds each kind a library can hold, every one a set question:
a goal no value meets or every value meets; an input, an argument record or a result no value
meets; a policy transition naming no tool, or whose guard no argument record of its tool meets,
and a policy no transition that can fire takes to a bad state; a flow rule naming no tool or
argument, or whose endorsed set meets nothing its sink takes, or holds all of it; a policy or rule
no task is held to. Values drawn from a schema are tried first, a member found proving a set
inhabited; valgebra is asked only where none is, and a question it leaves undecided is reported
as undecided.

## Running a program

`valgebra_planning/run.py` calls the tools a program names and checks each step as it is made:
the argument record against the tool's input set, each policy's step, the result against the
output set and the returned value against the goal. It fails closed: the first broken check
ends the run before the call is made, the result bound or the value returned, and `Run.refused`
names it. A proof of a program holds over the contracts, so a tool answering outside its own
contract is the case a run must not pass on. With `enforce=False` the runner records every break
and goes on, a diagnosis rather than a guard. `valgebra_planning/proxy.py` holds the same checks
as an MCP server in front of another, for an agent no program governs: each call is answered with
a tool error naming the check it breaks, a refused call steps no policy, and a flow rule reads a
value's source from equality with what an untrusted tool returned, the grounding gate of Sah et
al. 2026, an assumption.

## Limits

- **Types are the specification.** A slot whose schema admits any string, as an unasserted
  `format` does, takes any string; distinct schemas, patterns, enums and ranges are what make a
  plan mean what it should, and mined types narrow what observations support.
- **What is not modeled.** Side effects beyond the calls a policy reads; a field the output
  schema does not guarantee below another such field, or in a task's input; an array with
  `prefixItems`; and a program the planner writes holds no constant, tests no key and loops
  over no array. A program written elsewhere may do all three, and each is checked like any
  value.
- **Search bounds.** Six calls and tests a path by default, and the first 16 bindings of a call.
