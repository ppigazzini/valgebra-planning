# valgebra-planning

Plans of tool calls for agents, proven well-typed for every output the tool contracts admit.
A plan here is a program of calls and type tests on values, in the sense of strong planning
under nondeterministic outcomes: it is accepted only where each value it passes to a tool is
proven inside that tool's argument, whatever values inside their output schemas the tools
return. A program proposed elsewhere, by hand or by a language model, is verified the same
way, link by link.

It is part of the [valgebra](https://github.com/ppigazzini/valgebra) ecosystem.
[valgebra-jsonschema](https://github.com/ppigazzini/valgebra-jsonschema) reads each tool's
contract, the MCP `inputSchema` and `outputSchema`, into valgebra's sets, and valgebra decides
every inclusion a program rests on. Nothing here reads JSON Schema itself.

Status: pre-alpha, not on an index.

## What it does

| step | module |
|---|---|
| read each tool's contract as argument and result slots | `valgebra_planning/contracts.py` |
| cut the JSON values into the cells of every slot, input and goal | `valgebra_planning/cells.py` |
| search a strong program, or a weak one whose stops name what they leave | `valgebra_planning/planner.py` |
| verify every link of a program with valgebra alone | `valgebra_planning/verify.py` |
| read a program written in Python, verify it and critique it | `valgebra_planning/critic.py` |
| hold a program to tool errors, policies, flow rules and mined types | `planner.py`, `policy.py`, `flows.py`, `mining.py` |
| run a program, failing closed on every contract and policy | `valgebra_planning/run.py` |
| plan a library's tasks or critique a program from the command line | `valgebra_planning/cli.py` |
| read a live MCP server's tools and run a program against it (`mcp` extra) | `valgebra_planning/servers.py` |
| find the specifications of a library that hold vacuously | `valgebra_planning/vacuity.py` |
| read Progent's privilege policies as sets: narrowing, expansion, a program held to one | `valgebra_planning/privilege.py` |
| serve an MCP proxy holding every call to the contracts, policies and flow rules | `valgebra_planning/proxy.py` |

[`docs/00-using.md`](docs/00-using.md) shows the command line,
[`docs/01-method.md`](docs/01-method.md) states the method and its limits, and
[`docs/04-results.md`](docs/04-results.md) the studies: on a parcel-returns library of ten
tools, eight tasks get programs whose every link valgebra proves, from 3 of 3 to 24 of 24.

## Prior work

The method starts from valplan
([doi:10.5281/zenodo.23686600](https://doi.org/10.5281/zenodo.23686600)), predicate
abstraction over a Boolean algebra of JSON Schema sets with strong planning over the cells.
This package is written anew on the valgebra ecosystem and shares no code with it.

## License

MIT OR Apache-2.0, at your option. See [`LICENSE-MIT`](LICENSE-MIT) and
[`LICENSE-APACHE`](LICENSE-APACHE).
