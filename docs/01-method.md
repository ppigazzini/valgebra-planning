# Method

## Contracts as slots

An agent fills each argument of a call from a value it holds, so a tool is read as slots
(`valgebra_planning/contracts.py`): one per property of its MCP `inputSchema`, a required name
no property declares included, and one per field its `outputSchema` guarantees, nested to three
levels, beside the whole result. valgebra-jsonschema compiles each slot against the tool's own
schema as root, so a `$ref` resolves where the tool wrote it and an older draft reads as it
means. A task holds the slots of each value the agent starts with, read as a result's are, and
the set its goal is.

Where the input schema relates its arguments, through `oneOf`, `dependentRequired` or any
keyword beside `type`, `properties`, `required` and `additionalProperties`, a call is accepted
only once the whole argument record is proven inside the input set.

## The verifier

A program is calls, then one ending: a return, a stop, or a branch on whether a value lies in a
tool's argument or in listed constants (`valgebra_planning/program.py`). Its text reads as
Python.

The verifier (`valgebra_planning/verify.py`) walks a program with the set each value lies in,
narrowed by intersection or complement at every branch, and asks valgebra each inclusion the
program rests on: a value inside the argument it fills, the record where the arguments are
related, the returned value inside the goal. Each link reads proven, refuted or undecided, and a
refuted or undecided one carries a value outside the inclusion where the examples given or the
values drawn from the value's own schema hold one. A concrete member is a proof, so such a value
refutes an undecided link. The verifier reads neither the cells nor the search.

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
planning under nondeterministic outcomes (Cimatti, Pistore, Roveri and Traverso 2003), a branch
being an observation (Bertoli et al. 2006). The search deepens on the calls and tests along a
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
  fairness (Daniele, Traverso and Vardi 2000); any other call needs an error block,
  `try:`/`except ToolError:`, since retrying it may act twice. The annotations are hints a
  server gives, and a retry trusts them. A multi round-trip request is the client's loop around
  one call and leaves the plan unchanged.
- **Policies** (`valgebra_planning/policy.py`). An automaton over a program's calls whose
  guards are valgebra sets over the argument record. Along each path a guard is taken surely,
  possibly or never, and a path surely reaching a bad state is refuted, possibly undecided. A
  policy reads one program's calls in order and carries no value from one call to another.
- **Flows** (`valgebra_planning/flows.py`). A value from an untrusted source reaches a sink
  argument only inside the rule's endorsed set, and with `implicit` no sink call runs under a
  test on such a value: the integrity half of CaMeL, FIDES and Progent, decided over sets.
- **Mined types** (`valgebra_planning/mining.py`). Slots holding one observed value are one
  semantic type (APIphany, Guo et al. 2022), and a value fills an argument only where their
  types agree. A mined type is an assumption: the verifier reads none, and a plan names each
  binding that rests on one.

## The critic

`valgebra_planning/critic.py` reads a program written elsewhere, in the Python an agent writes
against typed tool APIs, verifies it and returns a critique: each link not proven, the value
refuting it, and for a binding the test that makes the call safe. A language model proposes, a
sound critic checks (Kambhampati et al. 2024).

## Limits

- **Types are the specification.** A slot whose schema admits any string, as an unasserted
  `format` does, takes any string; distinct schemas, patterns, enums and ranges are what make a
  plan mean what it should, and mined types narrow what observations support.
- **What is not modeled.** Side effects beyond the calls a policy reads, constants written into
  a call, and a result field the output schema does not guarantee.
- **Search bounds.** Six calls and tests a path by default, and the first 16 bindings of a call.
