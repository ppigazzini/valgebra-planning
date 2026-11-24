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
| `tools.json` | a server's `tools/list` result: `{"tools": [...]}`, each tool its `name`, `inputSchema`, and the `outputSchema` and `annotations` it declares; `"trusted": true` where the server's annotations are trusted, `"assertFormat": true` where `format` is read as an assertion, as valgebra-jsonschema reads it on request, in every contract, task, guard and endorsed set the library holds, `"closedResults": true` where each whole result is read closed, holding no key its schema does not declare, an assumption every link and binding over it names, and `"postconditions": [...]`, each what a call that returned, or with `"when": "acted"` one that acted at all, leaves a later read showing, an assumption every link resting on it names (the method's rules), and `"afterViolation": [...]`, the tools a proxy calls once a result has taken a policy to a bad state, as a robot's stop, each one no policy reads, and `"nonTextBlocks": [...]`, the tools whose blocks other than text, as a camera's image, a proxy answers beside the checked value, and `"errorTexts": {tool: schema}`, the JSON Schema each tool's error text lies in to reach an agent as the server wrote it, any other answered with a fixed text, and `"forwardsElicitation": [...]`, the tools whose request for a person's answer a proxy forwards to its client |
| `tasks.json` | `{"tasks": [...]}`, each task a `name`, the JSON Schema of each value the agent starts with in `inputs`, the JSON Schema of the `goal`, or `goals`, a list of them in the order the task prefers them, and the rules it is held to: `errors` (any tool may fail), `policies` and `flows` by name, `types`, the file of observed calls its semantic types are mined from; `depth`, the calls and tests a path may take where the task sets its own bound; `stops`, for a tool or an input, the JSON Schema of the whole value a path may stop on there, an outcome the task accepts as an end; `constants`, for a `tool.argument`, the JSON value the task's intent fixes it to, which the planner writes there and the verifier holds every call to; and `fresh`, the `tool.argument`s whose value is read after the last call before them that may act, none of them one `constants` fixes, since a constant is read at no time |
| `policies.json` | `{"policies": [...]}`, each an automaton over calls (`valgebra_planning/policy.py`): `initial`, the `bad` states no path may reach, the `pending` states no path may end in, and the `transitions` |
| `flows.json` | `{"flows": [...]}`, each a flow rule (`valgebra_planning/flows.py`) |
| a file `types` names | `{"calls": [...]}`, each an observed call: `tool`, `arguments`, `result`, and `"planned": true` where a planned program made it, whose arguments then link nothing; or `{"inputs": {...}}`, the values a run started with, which types each task input it shows; a tool listed without an `outputSchema` gets one shaped from its results there, an assumption every binding resting on it names |

A tool without an `outputSchema` can be called, and nothing it returns can fill another call's
argument: its result is a set no argument is proven to hold. A tool whose name Python does not
spell, as MCP allows a dotted or hyphened one, is called in a program by its alias, each
character outside `[A-Za-z0-9_]` written `_`: `g1.grasp` is `g1_grasp`. Two tools one alias
spells are refused naming both. `examples/parcels` is a library
holding every file.

A rule means what its keys say. A key no reader reads, in a file, a task, a policy, a transition or
a flow rule, is refused by name, as a misspelled `only` or `arguments` would leave a rule weaker
than the one written, and so is a rule naming what the library lacks: a transition's tool, a hint
that is none of MCP's and no tool sets, a flow's sink, its argument, or a source that is no tool
and no task's input, each of which reads no call, as a proxy refuses one naming what its server
lists, and two policies or two flow rules of one name, since a task and a proxy know a rule by its
name; `errors`, `trusted`, `assertFormat`, `closedResults`, `only` and
`implicit` are `true` or `false`, a `depth`
is a positive integer, and a transition names a `tool` or a `hint`, one of the two; `per`, an argument's name, gives the policy an instance for each value of it at run time, and `scope`, on a policy or a flow rule, is `"shared"`, every client of a proxy together and the default, or `"principal"`, each principal a token names apart. A command reading such a library exits 2
naming the key.

## Plan

```bash
uv run valgebra-planning plan examples/parcels --task "return a parcel"
```

prints the program for each task (every task when no `--task` is given; `--task` repeats), whether
it is strong or weak, how many of its links valgebra proves, each link not proven, each value a
weak program's stops leave uncovered, and each binding resting on a mined type. With `--json` it
prints one JSON document, a list holding a row per task. The exit status is 1 when a task gets no
program, and the row says why: the depth bound cut a path short of the goal, and whether a deeper
search finds one is not known, or no path reached it; and each argument no tool returns a value
for, which a task input fills only under a test. `--depth` sets the calls and tests a path may take, for every task planned; a task
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
flow rule that lets nothing or everything through, a result the closed reading empties, a
postcondition that covers no read or narrows none, a rule no task is held to. A finding valgebra
leaves undecided is marked so. The exit status is 1 when it finds one. After them, each line
starting `note:` names a contract wider than its keywords suggest
([the method](method.md#specifications-that-hold-vacuously)), which the exit status does not
read; with `--json` each entry carries its `kind`, `vacuous` or `wide`.

## Compare two privilege policies

```bash
uv run valgebra-planning privileges old.json new.json
```

reads two privilege policies written as Progent's code writes them, `{"tool": [[priority,
effect, condition, fallback], ...]}` (Shi et al. 2025, whose paper names the same four fields
in an object), and prints for each tool the new one names whether it narrows the old one,
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
answer. A call a policy reads holds the proxy's gate until it is answered or fails, so the
timeout bounds how long it keeps the next such call waiting. `--depth` sets the calls and tests a path of the planned program may take, as for
`plan`. A canceled run sends no call after the cancel. `tests/parcel_server.py` serves the example's
tools over stdio.

## A proxy in front of a server

```bash
uv run valgebra-planning proxy --server "uv run python tests/parcel_server.py" --library examples/parcels
```

serves an MCP server over stdio, in front of `--server`, that lists its tools as they are and
answers a call only after holding it to the tool's contract, to every policy and flow rule the
library defines, and, the call made, its result to the output schema
(`valgebra_planning/proxy.py`). A broken check answers the call with a tool error naming it, and
the call never reaches the server, or its result the agent. The server's tools are read as the
library reads its own listing, with its `assertFormat` and `closedResults`. The error names each failure's path
and code, as `run_id: string_pattern_mismatch`, and never the value: a key the tool's schemas do
not declare is written `*`, so no part of a refused result reaches the agent through the error.
With `--spell-out`, each tool is listed with its input schema spelled out for a model's chat
template, which may drop a `const` or a `pattern`: each `const` is written as its value's
`type`, a one-value `enum` and a `description`, `{"type": "string", "enum": ["delivered"],
"description": "always \"delivered\""}`, and each `pattern` is named in the `description`, an
upstream description kept before it. A tool is listed so only where valgebra proves the
arguments the spelling admits equal, both ways, to the published ones; any other is listed as
published, and a warning names it. The gate holds every call to the published schemas.
A result the proxy's client refuses against the output schema the server declares is refused
naming the tool alone, since the client's own message holds the value.
A refused call leaves every policy
where it was, and a call the server answers with an error leaves each in the states before and
after it, since the call may have acted or not. A call whose outcome the gate does not read, its
result refused, the call canceled or answered by no result, leaves each in every state it may
have reached, a transition reading its result taken possibly. A transition reading the result steps when the
result arrives; once a result takes a policy to a bad state, the call having been made, every
later call is refused but one to a tool `tools.json` names under `"afterViolation"`, which no
policy may read. A flow rule reads provenance from values: an argument equal to a value a source
tool returned is taken as that tool's, an assumption. Under `only` an argument equal to no value
its sources returned is refused, and so is a value from a task input, since a proxy sees no
task. A call the server answers by asking for input is answered with a tool error, but for a
tool `tools.json` names under `"forwardsElicitation"`: a request for a person's answer, a form or
a URL, is forwarded with its state sealed by the mcp SDK's request-state boundary, bound to the
call and the principal its token names, and the answers that resume it reach the server only
inside the schema each form asked under. An agent connects to the proxy as to any stdio server, and no agent
changes.

```bash
uv run valgebra-planning proxy --server "uv run python tests/parcel_server.py" --library examples/parcels \
    --http 127.0.0.1:8000 --resource http://127.0.0.1:8000/mcp --issuer https://auth.example \
    --verifier deployment:VERIFIER
```

serves the same proxy over Streamable HTTP to every client holding a token issued for
`--resource`, the proxy's URL, all of them through its one gate. `--http` takes `--resource`,
`--issuer`, the authorization server a client is pointed at, and `--verifier`, named
`module:name` and found from the working directory: here `VERIFIER` in a `deployment.py` of the
deployment's own, an object with mcp's `verify_token` that reads a token that server issued. It
serves no client without the three. A request without a valid token
issued for `--resource`, or one missing a `--scope`, is answered 401 or 403, naming the
protected resource metadata the proxy serves; a request whose Host is not `--resource`'s is
answered 421, and one carrying an Origin no `--origin` lists 403.

## Limits

- With `--server`, the tasks, policies, flow rules and observed calls are still the library's files,
  and so are `trusted`, `assertFormat` and `closedResults`: the server's tools are read with them, and a
  `tools.json` holding them alone needs no listing.
