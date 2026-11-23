---
description: The public modules, the names each promises, the two needing the mcp extra, and their reference read from the docstrings.
---

# The Python API

The names valgebra-planning promises a Python caller: each public module, the names its
`__all__` holds, and the two modules that need the `mcp` extra. [The command line](command-line.md)
has a page of its own, and what each answer means is [the method](method.md)'s.

## Import a name from its module

```python
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.planner import plan
from valgebra_planning.program import render
from valgebra_planning.verify import verify

find = read_contract(
    {
        "name": "find_parcel",
        "inputSchema": {
            "type": "object",
            "properties": {"tracking": {"type": "string", "pattern": "^[A-Z]{2}[0-9]{9}[A-Z]{2}$"}},
            "required": ["tracking"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"parcel_id": {"type": "integer", "minimum": 1}},
            "required": ["parcel_id"],
        },
    }
)
contracts = {"find_parcel": find}
task = read_task(
    "find a parcel",
    {"tracking": {"type": "string", "pattern": "^[A-Z]{2}[0-9]{9}[A-Z]{2}$"}},
    {"type": "integer", "minimum": 1},
)
found = plan(contracts, task)
assert found.strong and found.program is not None
print(render(found.program))
checked = verify(found.program, contracts, task)
print(checked.count("proven"), "of", len(checked.links), "links proven")
```

```text
c1 = find_parcel(tracking=tracking)
return c1.parcel_id
2 of 2 links proven
```

The tracking code lies inside `find_parcel`'s argument, and the parcel identifier its output
guarantees inside the goal.

**The package itself exports nothing**, and `valgebra_planning.__all__` is empty. Three
modules define a function of their own name, `run`, `verify` and `proxy`, so a function
exported from the package would hide its module there; `from valgebra_planning import *`
would bind `plan`, `run` and `verify` over a caller's own names; and an export of `proxy` or
`servers` would import the `mcp` SDK on every import of the package, with or without the
extra.

## The public names

| module | names | needs |
|---|---|---|
| `valgebra_planning.contracts` | `Contract`, `Postcondition`, `Slot`, `Task`, `read_contract`, `read_postcondition`, `read_task` | |
| `valgebra_planning.program` | `Block`, `Branch`, `Call`, `Const`, `For`, `Next`, `Ref`, `Return`, `Stop`, `render`, `steps` | |
| `valgebra_planning.planner` | `DEPTH`, `Plan`, `Uncovered`, `plan`, `predicates` | |
| `valgebra_planning.cells` | `Abstraction`, `Cell`, `Predicate`, `Status`, `abstract`, `gather` | |
| `valgebra_planning.verify` | `Gap`, `Link`, `Verdict`, `Verification`, `verify` | |
| `valgebra_planning.critic` | `Critique`, `ProgramError`, `critique`, `read_program` | |
| `valgebra_planning.codemode` | `Repair`, `feedback`, `repair`, `stub`, `stubs` | |
| `valgebra_planning.run` | `CanceledRunError`, `Outcome`, `Refusal`, `RefusedResultError`, `Run`, `ToolError`, `arun`, `blame`, `run` | |
| `valgebra_planning.policy` | `Monitor`, `Policy`, `Reach`, `Snapshot`, `Transition`, `read_policy` | |
| `valgebra_planning.flows` | `Flow`, `read_flow` | |
| `valgebra_planning.mining` | `SlotKey`, `Types`, `infer_outputs`, `mine` | |
| `valgebra_planning.library` | `Library`, `Problem`, `after_violation`, `asserts_format`, `error_texts`, `forwards_elicitation`, `non_text_blocks`, `read_library`, `reads_closed` | |
| `valgebra_planning.privilege` | `Change`, `Rule`, `Update`, `allowed`, `as_policy`, `describe`, `read_privileges`, `read_rules`, `updates` | |
| `valgebra_planning.vacuity` | `Certainty`, `Finding`, `vacuities` | |
| `valgebra_planning.meaning` | `findings` | |
| `valgebra_planning.servers` | `MAX_PAGES`, `connect`, `read_listing`, `read_server`, `refused_by_client`, `run_on_server` | the `mcp` extra |
| `valgebra_planning.proxy` | `Gate`, `http_app`, `proxy`, `read_proxy`, `serve`, `serve_http`, `spelled_out` | the `mcp` extra |

A module's `__all__` holds what a caller calls, the types those calls take and return, and the
defaults a caller passes, `DEPTH` among them. Every other name a module defines serves the
package's other modules and is not promised. `valgebra_planning._valgebra`, the one module
importing valgebra and valgebra-jsonschema, and `valgebra_planning.cli`, the command line's,
are not public.

## What holds the table

`tests/test_api.py` holds the table to the package:

- each public module's `__all__` to its row, and the public modules to the rows;
- each listed name to a definition in its own module, not one it imports;
- each type a public function, class or method names in its signature to a public name, or
  to one valgebra, valgebra-jsonschema or the standard library defines;
- the `needs` column to the modules that import `mcp` when they are imported.

**The limit.** Under `0.0.x` a release may change any public name; the table says which names
a release can break, not that it will not. The changelog records each change.

## Reference

Each public module's names, read from their docstrings.

::: valgebra_planning.contracts

::: valgebra_planning.program

::: valgebra_planning.planner

::: valgebra_planning.cells

::: valgebra_planning.verify

::: valgebra_planning.critic

::: valgebra_planning.codemode

::: valgebra_planning.run

::: valgebra_planning.policy

::: valgebra_planning.flows

::: valgebra_planning.mining

::: valgebra_planning.library

::: valgebra_planning.privilege

::: valgebra_planning.vacuity

::: valgebra_planning.meaning

::: valgebra_planning.servers

::: valgebra_planning.proxy
