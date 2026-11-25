# valgebra-planning

MIT OR Apache-2.0, at your option.

Plans of tool calls for agents, proven well-typed for every output the tool contracts admit.
A plan here is a program of calls and type tests on values, in the sense of strong planning
under nondeterministic outcomes: it is accepted only where each value it passes to a tool is
proven inside that tool's argument, whatever values inside their output schemas the tools
return. A program proposed elsewhere, by hand or by a language model, is verified the same
way, link by link.

📖 **Documentation: <https://ppigazzini.github.io/valgebra-planning/>**

🤖 **For AI assistants and coding agents:** the documentation is also published as
[`llms.txt`](https://ppigazzini.github.io/valgebra-planning/llms.txt) (a curated manifest)
and [`llms-full.txt`](https://ppigazzini.github.io/valgebra-planning/llms-full.txt) (the
full text, including the API reference) per the [llmstxt.org](https://llmstxt.org)
convention.

It is part of the [valgebra](https://github.com/ppigazzini/valgebra) ecosystem.
[valgebra-jsonschema](https://github.com/ppigazzini/valgebra-jsonschema) reads each tool's
contract, the MCP `inputSchema` and `outputSchema`, into valgebra's sets, and valgebra decides
every inclusion a program rests on. Nothing here reads JSON Schema itself.

Status: pre-alpha.

```bash
pip install valgebra-planning            # Python 3.11 or later
pip install "valgebra-planning[mcp]"     # with a live MCP server's tools, and the proxy
```

Every `0.0.x` release of the ecosystem may change what an earlier one answered, so a deployment
pins the set a release was tested with: the
[`constraints.txt`](https://github.com/ppigazzini/valgebra-planning/blob/main/constraints.txt) at
its tag holds valgebra, valgebra-jsonschema and every other dependency at the versions the
suite and the studies ran on, `pip install "valgebra-planning[mcp]==0.0.9" -c constraints.txt`.

## What it does

| step | module |
|---|---|
| read each tool's contract as argument and result slots | `valgebra_planning/contracts.py` |
| cut the JSON values into the cells of every slot, input and goal | `valgebra_planning/cells.py` |
| search a strong program, or a weak one whose stops name what they leave | `valgebra_planning/planner.py` |
| verify every link of a program with valgebra alone | `valgebra_planning/verify.py` |
| read a program written in Python, verify it and critique it | `valgebra_planning/critic.py` |
| hold a program to tool errors, policies, flow rules, mined types, fresh reads and postconditions | `planner.py`, `policy.py`, `flows.py`, `mining.py`, `contracts.py` |
| run a program, failing closed on every contract and policy, a policy's lapses by a clock | `valgebra_planning/run.py` |
| plan a library's tasks or critique a program from the command line | `valgebra_planning/cli.py` |
| read a live MCP server's tools and run a program against it (`mcp` extra) | `valgebra_planning/servers.py` |
| find the specifications of a library that hold vacuously | `valgebra_planning/vacuity.py` |
| note contracts wider than their keywords suggest | `valgebra_planning/meaning.py` |
| read Progent's privilege policies as sets: narrowing, expansion, a program held to one | `valgebra_planning/privilege.py` |
| serve an MCP proxy holding every call to the contracts, policies and flow rules | `valgebra_planning/proxy.py` |
| give a model typed stubs, and repair its program with the critique | `valgebra_planning/codemode.py` |

The guide has [how a program is proven, a diagram for each step](https://ppigazzini.github.io/valgebra-planning/how-it-works/),
[the command line](https://ppigazzini.github.io/valgebra-planning/command-line/),
[the Python names each module promises](https://ppigazzini.github.io/valgebra-planning/api/),
[how a model's program is proven before it runs](https://ppigazzini.github.io/valgebra-planning/code-mode/),
[tasks done end to end in Python](https://ppigazzini.github.io/valgebra-planning/recipes/), a policy, a flow rule and a privilege
update among them, [the method and its limits](https://ppigazzini.github.io/valgebra-planning/method/),
[what a call costs and how the speed is made](https://ppigazzini.github.io/valgebra-planning/performance/), and
[the studies](https://ppigazzini.github.io/valgebra-planning/results/): on a parcel-returns library of ten tools, eight tasks get programs whose every
link valgebra proves, from 3 of 3 to 24 of 24.

## Prior work

The method starts from valplan
([doi:10.5281/zenodo.23686600](https://doi.org/10.5281/zenodo.23686600)), predicate
abstraction over a Boolean algebra of JSON Schema sets with strong planning over the cells.
This package is written anew on the valgebra ecosystem and shares no code with it.

## License

MIT OR Apache-2.0, at your option. See
[`LICENSE-MIT`](https://github.com/ppigazzini/valgebra-planning/blob/main/LICENSE-MIT) and
[`LICENSE-APACHE`](https://github.com/ppigazzini/valgebra-planning/blob/main/LICENSE-APACHE).
