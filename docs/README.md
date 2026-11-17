---
description: What valgebra-planning is, how it rests on valgebra and valgebra-jsonschema, and the index to this guide.
---

# valgebra-planning

**Plans of tool calls for agents, proven well-typed for every output the tool contracts admit.**

A plan is a program of calls and type tests on values, in the sense of strong planning under
nondeterministic outcomes: it is accepted only where each value it passes to a tool is proven
inside that tool's argument, whatever values inside their output schemas the tools return. A
program proposed elsewhere, by hand or by a language model, is verified the same way, link by
link. [valgebra-jsonschema](https://ppigazzini.github.io/valgebra-jsonschema/) reads each tool's
MCP `inputSchema` and `outputSchema` into [valgebra](https://ppigazzini.github.io/valgebra/)'s
sets, and valgebra decides every inclusion a program rests on.

```bash
pip install valgebra-planning            # Python 3.11 or later
pip install "valgebra-planning[mcp]"     # with a live MCP server's tools, and the proxy
```

| page | what it holds |
|---|---|
| [00-using.md](00-using.md) | the command line: a library's files, `plan`, `critique`, `stubs`, `check`, `privileges`, `run` and `proxy`, their output and exit statuses |
| [03-code-mode.md](03-code-mode.md) | a model's program proven before it runs and run so the proof holds, and the two kinds of check the proxy holds |
| [12-changelog.md](12-changelog.md) | every released version and what it changed |
| [01-method.md](01-method.md) | contracts, the verifier, the cells, the planner, the rules a program is held to, the critic, and the limits |
| [04-results.md](04-results.md) | the studies and the commands that check them |
| [02-api.md](02-api.md) | the Python API: the public modules, the names each promises, the two needing the `mcp` extra |
| [11-references.md](11-references.md) | every work the method and the modules cite, under its key |

The site serves [`llms.txt`](https://ppigazzini.github.io/valgebra-planning/llms.txt), a manifest
of these pages, and [`llms-full.txt`](https://ppigazzini.github.io/valgebra-planning/llms-full.txt),
their full text, for a reader that is not a browser. The developer pages under `docs/dev/` are not
published: how a version reaches PyPI, `docs/dev/05-releasing.md`, and the rules for every text,
`docs/dev/10-writing.md`.
