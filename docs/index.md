---
description: What valgebra-planning is, how it rests on valgebra and valgebra-jsonschema, and where to start.
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

```python
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import critique


def tool(name, takes, returns):
    """Return the contract of a tool listed with these properties, every one required."""
    return read_contract(
        {
            "name": name,
            "inputSchema": {"type": "object", "properties": takes, "required": [*takes]},
            "outputSchema": {"type": "object", "properties": returns, "required": [*returns]},
        }
    )


ORDER = {"type": "string", "pattern": "^ord_[0-9]+$"}
REFUND = {"type": "string", "pattern": "^rfd_[0-9]+$"}
tools = {
    "find_order": tool("find_order", {"order_id": ORDER}, {"status": {"enum": ["open", "paid"]}}),
    "refund": tool(
        "refund", {"order_id": ORDER, "status": {"const": "paid"}}, {"refund_id": REFUND}
    ),
}
task = read_task("refund an order", {"order_id": ORDER}, REFUND)

# A program a model proposed: it refunds whatever order the lookup returns.
checked = critique(
    "c1 = find_order(order_id=order_id)\n"
    "c2 = refund(order_id=order_id, status=c1.status)\n"
    "return c2.refund_id\n",
    tools,
    task,
)
print(checked.accepted)
print(checked.notes[0])
```

```text
False
refuted: c1.status <= refund.status; 'open' lies outside; test `if c1.status in refund.status:` and make the call in that branch
```

```bash
pip install valgebra-planning            # Python 3.11 or later
pip install "valgebra-planning[mcp]"     # with a live MCP server's tools, and the proxy
```

Every inclusion a program rests on is a link, and each reads proven, refuted, with a value
that breaks it where one is found, or undecided where valgebra cannot tell, which never counts
as proven. A program is accepted when every link is proven and no path stops.
[How it works](how-it-works.md) builds this from a tool's contract up, with a diagram for each
step.

Start with [the command line](command-line.md), [a model's program checked](code-mode.md)
or [the recipes](recipes.md); [the method](method.md) is what every answer means.

The site serves [`llms.txt`](https://ppigazzini.github.io/valgebra-planning/llms.txt), a manifest
of its pages, and [`llms-full.txt`](https://ppigazzini.github.io/valgebra-planning/llms-full.txt),
their full text, for a reader that is not a browser. The developer pages under `docs/dev/` are not
published: [how a version reaches PyPI](https://github.com/ppigazzini/valgebra-planning/blob/main/docs/dev/releasing.md), and
[the rules for every text](https://github.com/ppigazzini/valgebra-planning/blob/main/docs/dev/writing.md).
