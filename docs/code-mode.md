---
description: A model's program proven before it runs and run so the proof holds, and the two kinds of check the proxy holds.
---

# Code mode

How a program a model writes in Python against typed tool stubs is proven before it runs and
run so that the proof holds, and what the proxy holds for an agent whose program is never seen.
The same steps have [a command line](command-line.md); what a link means is
[the method](method.md)'s.

## Stubs, critique, run

`stubs` writes each tool as a typed Python function, under a preamble stating what a program may
hold and one worked program for a task unlike any library's. A program the reader does not read
is refused naming the line and the form it reads there: a call left unbound is answered with the
assignment to write, `c2 = notify(...)`, and the stubs handed back as a program with the line to
leave out.

Run from the repository root:

```python
import asyncio
import shlex
import sys
from pathlib import Path

from valgebra_planning.codemode import feedback, stubs
from valgebra_planning.critic import critique
from valgebra_planning.library import read_library
from valgebra_planning.planner import plan
from valgebra_planning.program import render
from valgebra_planning.servers import connect, run_on_server

library = read_library(Path("examples/parcels"))
settle = library.problem("settle a parcel")
prompt = stubs(library.tools)  # what a model writes against

first = critique(
    "c1 = find_parcel(tracking=tracking)\n"
    "c2 = book_return(parcel_id=c1.parcel.parcel_id, state=c1.parcel.state,"
    " weight_g=c1.parcel.weight_g)\n"
    "return c2.label_id\n",
    library.tools,
    settle.task,
)
print(first.accepted)
print(feedback(first))

# A second proposal, written as the critique asks: here the planner's own program.
second = critique(render(plan(library.tools, settle.task).program), library.tools, settle.task)
print(second.accepted)


async def ran():
    # The command is split as a POSIX shell splits it, so the interpreter's path is quoted.
    async with connect(shlex.join([sys.executable, "tests/parcel_server.py"])) as client:
        inputs = {"tracking": "AB123456789IT"}
        return await run_on_server(second.program, library.tools, settle.task, inputs, client)


result = asyncio.run(ran())
print(result.broken, result.value.startswith("lbl_"))
```

```text
False
refuted: c1.parcel.state <= book_return.state; 'returned' lies outside; test `if c1.parcel.state in book_return.state:` and make the call in that branch
refuted: c1.parcel.weight_g <= book_return.weight_g; 26325 lies outside; test `if c1.parcel.weight_g in book_return.weight_g:` and make the call in that branch
True
() True
```

The first program passes a parcel's state and weight to `book_return` untested, and valgebra
refutes both links with a value the parcel's schema admits and the argument refuses; each
critique names the test that makes the call safe. A field read without the test showing its
key, as `c1.parcel.note` where the parcel's schema leaves `note` optional, is refuted with a
record lacking it, and its critique names the key test, `if "note" in c1.parcel:`. The second
program tests both and covers every other state, and every link is proven. `codemode.repair` runs
the same loop with any callable from the last critique to a program as the model.

A task no program covers on every path, as an object a detector may not find, never has a
program accepted: every program stops somewhere. Such a task names the stops it accepts as ends,
`stops` in `tasks.json` or `read_task(stops=...)`, a JSON Schema for the whole value of a tool or
an input a path stops on, and `repair(..., accept="weak")` ends on a program every link of which
is proven, some path of which returns, and every stop of which lies in the set its task names
there; `Critique.weak` reads that, and each such stop's note says the task tolerates it.
`Repair.met` reads whether the last program met the mode the rounds ran in, where
`Repair.accepted` reads strict acceptance. A
program stopping on every path returns on none and is never accepted so.

## What makes the proof hold

The program that runs is the program proven. `valgebra_planning/run.py` interprets the `Block`
the critic read, with no `exec` of the model's text: it checks each argument record against
the tool's input set before the call, each policy's step, and each result against the output
set after it, and it fails closed on the first that breaks. So `if x in tool.argument:`,
`retry(...)` and `stop(...)` mean what the critic proved only in this runner.

This is the plan-then-execute and code-then-execute pattern (Beurer-Kellner et al. 2025): the
program is fixed before a tool's output is read, so no text a tool returns can choose a call.
CaMeL (Debenedetti et al. 2025) holds the same order with capabilities on values; here each
link is an inclusion of sets valgebra decides, and a flow rule is checked over the program's
references, which name the call a value came from.

Code orchestration that executes model-written code in a sandbox (Anthropic's programmatic
tool calling, Cloudflare's Code Mode, smolagents' `CodeAgent`) runs the code as written, and
the sandbox bounds what it reaches; no proof precedes the run. The two read the same contracts:
the stubs here are written from each tool's published `inputSchema` and `outputSchema`.

## The proxy, for an agent whose program is not seen

`valgebra-planning proxy` stands in front of a server for an agent written elsewhere, and holds
each call as it comes. What it holds is of two kinds.

- **Contract and policy checks hold against any author of the call.** The argument record
  against the tool's input set, each policy's step, and the result against the output set are
  decisions over the values themselves: an argument outside the input set is refused whatever
  produced it, a model steered by injected text included. A result is answered with the checked
  value as its text, so no other text the server wrote beside it reaches the agent.
- **Flow rules hold against no attacker that adapts.** A proxy sees values, not references, so
  it reads provenance by equality: an argument equal to a value an untrusted tool returned is
  taken as that tool's. A value the model upper-cases, encodes or cuts equals none it was
  returned and passes. A defense an attacker can steer through the model's output is judged
  against an attacker who adapts to it (Nasr et al. 2025), and this one stops none that does.
  A flow rule is a guarantee over a program the critic checked, where provenance is a
  reference, and a heuristic in the proxy.

**One gate, whatever reaches it.** `proxy()` returns an MCP server, and `http_app()` serves it
over Streamable HTTP to every client holding a token issued for it, as `proxy --http` does.
Every client of one proxy calls through its one gate: a policy reads the calls of all of them in order, and a flow rule takes a
value any of them was returned as returned. A call a policy reads holds the gate from its check to
its result, so a second one waits for the first's result before it is checked; a call no policy
reads waits on none. Two deployments in front of one robot differ there.

| deployment | policy state | provenance |
|---|---|---|
| one proxy, every agent its client | one: a call one agent makes moves it for every other | pooled: a value one agent was returned grounds another's call |
| one proxy per agent | one per proxy: each holds the calls made through it | per proxy |

A policy about one actuator, as "no walking while an object is held", is held by the one proxy in
front of it; one proxy per agent holds it for no agent, since each sees half the calls. What
pooling costs is which client holds a value, and no rule here reads that: `only` states that a
value was returned by a named tool, which holds whichever client it was returned to. A call its
server answers by asking for input is answered with a tool error, but a request for a person's
answer from a tool its library names under `"forwardsElicitation"` ([limits](method.md#limits)).

## Limits

- **The language computes nothing.** A program calls, tests, loops and returns; it has no
  arithmetic and no aggregation, so "the mean of the values the calls returned" is not a
  program here. A computation over the returned values, after every call is made, is not
  proposed until a consumer needs one.
- **The runner is the only interpreter.** A program proven here and run anywhere else, a
  sandbox included, carries none of the checks above.
