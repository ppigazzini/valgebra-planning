---
description: Plan a library's tasks, critique a program, run one against a live server, or serve the proxy, from the command line.
---

# Using valgebra-planning

How to plan the tasks of a tool library, or check a program written for one, from the command
line. What each answer means is [the method](method.md)'s.

## A library

A library is a directory (`valgebra_planning/library.py` reads it):

| file | holds |
|---|---|
| `tools.json` | a server's `tools/list` result: `{"tools": [...]}`, each tool its `name`, `inputSchema`, and the `outputSchema` and `annotations` it declares; `"trusted": true` where the server's annotations are trusted, and `"assertFormat": true` where `format` is read as an assertion, as valgebra-jsonschema reads it on request, in every contract, task, guard and endorsed set the library holds |
| `tasks.json` | `{"tasks": [...]}`, each task a `name`, the JSON Schema of each value the agent starts with in `inputs`, the JSON Schema of the `goal`, and the rules it is held to: `errors` (any tool may fail), `policies` and `flows` by name, `types`, the file of observed calls its semantic types are mined from; and `depth`, the calls and tests a path may take where the task sets its own bound |
| `policies.json` | `{"policies": [...]}`, each an automaton over calls (`valgebra_planning/policy.py`): `initial`, the `bad` states no path may reach, the `pending` states no path may end in, and the `transitions` |
| `flows.json` | `{"flows": [...]}`, each a flow rule (`valgebra_planning/flows.py`) |
| a file `types` names | `{"calls": [...]}`, each an observed call: `tool`, `arguments`, `result`; a tool listed without an `outputSchema` gets one shaped from its results there, an assumption every binding resting on it names |

A tool without an `outputSchema` can be called, and nothing it returns can fill another call's
argument: its result is a set no argument is proven to hold. `examples/parcels` is a library
holding every file.

A rule means what its keys say. A key no reader reads, in a file, a task, a policy, a transition or
a flow rule, is refused by name, as a misspelled `only` or `arguments` would leave a rule weaker
than the one written; `errors`, `trusted`, `only` and `implicit` are `true` or `false`, a `depth`
is a positive integer, and a transition names a `tool` or a `hint`, one of the two. A command reading such a library exits 2
naming the key.

## Plan

```bash
uv run valgebra-planning plan examples/parcels --task "return a parcel"
```

prints the program for each task (every task when no `--task` is given; `--task` repeats), whether
it is strong or weak, how many of its links valgebra proves, each link not proven, each value a
weak program's stops leave uncovered, and each binding resting on a mined type. With `--json` it
prints one JSON document, a list holding a row per task. The exit status is 1 when a task gets no
program, and the row says why: the depth bound cut a path, so a deeper search may find one, or no
path reached it. `--depth` sets the calls and tests a path may take, for every task planned; a task
with a `depth` of its own is planned to it otherwise, and any other to six.

## Critique a program written elsewhere

```bash
uv run valgebra-planning critique examples/parcels --task "return a parcel" program.py
```

reads `program.py`, the Python an agent writes against typed tool APIs (`valgebra_planning/critic.py`
states what it reads), verifies every link under the rules the task names, and prints each link not
proven with the value that refutes it and what to change: the test that makes a call safe, the call
a policy needs first, the source a flow rule trusts, the error block a call needs. A binding crossing
two types the task's observed calls tell apart is a note, and decides nothing. The exit status is 0 when the program is
accepted, 1 when it is not, and 2 when the reader refuses its text.

## Stubs for a model

```bash
uv run valgebra-planning stubs examples/parcels
```

prints the library's tools as Python stubs, after a preamble stating the language a program
uses: one function a tool, every argument by name, its docstring holding the tool's input and
output schemas. Hand them to a model to write a program against, then `critique` it; in Python,
`valgebra_planning.codemode.repair` runs that loop with any callable as the model.

## Check a library

```bash
uv run valgebra-planning check examples/parcels
```

lists every specification in the library that holds vacuously, as
[the method](method.md#specifications-that-hold-vacuously) defines them: a goal no value or
every value meets, a policy transition that never fires, a policy that can never be violated, a
flow rule that lets nothing or everything through, a rule no task is held to. A finding valgebra
leaves undecided is marked so. The exit status is 1 when it finds one.

## Compare two privilege policies

```bash
uv run valgebra-planning privileges old.json new.json
```

reads two privilege policies in Progent's format, `{"tool": [[priority, effect, condition,
fallback], ...]}`, and prints for each tool the new one names whether it narrows the old one,
expands it, with a call it newly allows, or is left undecided. The exit status is 0 only when
every tool narrows. In Python, `valgebra_planning.privilege.as_policy` turns a policy into one the
planner, the verifier and the runner hold a program to.

## A live server

With the `mcp` extra (`uv sync --extra mcp`, or `pip install "valgebra-planning[mcp]"`), `--server`
reads the tools from a live MCP server instead of `tools.json`: a URL connects over Streamable
HTTP, anything else is a command starting a server over stdio. The command is split as a POSIX
shell splits it, on Windows as well: a backslash escapes the character after it, so a path
holding one, or a space, goes in single quotes, as `shlex.quote` writes it.

```bash
uv run valgebra-planning plan examples/parcels --task "return a parcel" \
  --server "uv run python tests/parcel_server.py"
uv run valgebra-planning run examples/parcels --task "settle a parcel" \
  --inputs '{"tracking": "AB123456789IT"}' --server "uv run python tests/parcel_server.py"
```

`run` plans the task over the server's tools and runs the program against the server
(`valgebra_planning/servers.py`): each call is a `tools/call`, its `structuredContent` the value it
returns, an `isError` result, a protocol error or a request for input a tool error, and the run
fails closed on any argument record, policy step or result outside its contract. It prints how the
run ended, and the cause of a break (the server, an assumption, or the program), and exits 1 unless
it returned a value with no break. With `--timeout`, `run` and `proxy` give each call that many
seconds to be answered, after which it fails as a tool error; with none, a call waits for its
answer. A canceled run sends no call after the cancel. `tests/parcel_server.py` serves the example's
tools over stdio.

## A proxy in front of a server

```bash
uv run valgebra-planning proxy --server "uv run python tests/parcel_server.py" --library examples/parcels
```

serves an MCP server over stdio, in front of `--server`, that lists its tools as they are and
answers a call only after holding it to the tool's contract, to every policy and flow rule the
library defines, and, the call made, its result to the output schema
(`valgebra_planning/proxy.py`). A broken check answers the call with a tool error naming it, and
the call never reaches the server, or its result the agent. The error names each failure's path
and code, as `run_id: string_pattern_mismatch`, and never the value: a key the tool's schemas do
not declare is written `*`, so no part of a refused result reaches the agent through the error.
A result the proxy's client refuses against the output schema the server declares is refused
naming the tool alone, since the client's own message holds the value.
A refused call leaves every policy
where it was, and a call the server answers with an error leaves each in the states before and
after it, since the call may have acted or not. A flow rule reads provenance from values: an argument equal to a value a source
tool returned is taken as that tool's, an assumption. Under `only` an argument equal to no value
its sources returned is refused, and so is a value from a task input, since a proxy sees no
task. A call the server answers by asking for input is answered with a tool error, and the
request is not forwarded. An agent connects to the proxy as to any stdio server, and no agent
changes.

## Limits

- With `--server`, the tasks, policies, flow rules and observed calls are still the library's files,
  and so are `trusted` and `assertFormat`: the server's tools are read with them, and a
  `tools.json` holding them alone needs no listing.
