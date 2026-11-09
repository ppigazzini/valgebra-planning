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

## Prior work

The method starts from valplan
([doi:10.5281/zenodo.23686600](https://doi.org/10.5281/zenodo.23686600)), predicate
abstraction over a Boolean algebra of JSON Schema sets with strong planning over the cells.
This package is written anew on the valgebra ecosystem and shares no code with it.

## License

MIT OR Apache-2.0, at your option. See [`LICENSE-MIT`](LICENSE-MIT) and
[`LICENSE-APACHE`](LICENSE-APACHE).
