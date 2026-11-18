---
description: Tasks done end to end over tool listings in hand -- a model's program checked before it runs, a server answering outside its contract, an approval a policy requires, a notice held to the address on file, a privilege update, and a policy that cannot fire.
---

# Recipes

Each recipe is one task done end to end in Python, over tool listings already in hand as a
server's `tools/list` returns them, and each says what it cannot do. Each block runs as written
and prints the text that follows it. The command line for the same steps is
[command-line.md](command-line.md)'s, and what each answer means is [method.md](method.md)'s.

## A model's program, checked before it runs

A model writing a program against a library's tools passes what one call returns to the next,
and nothing about the model makes each value fit the argument it fills. `critique` asks valgebra
every link the program rests on, for every value the tools' output schemas admit, and names each
link not proven with the test that makes the call safe; `feedback` is the text to hand back to
the model. A program it accepts runs with `run`, each call a function standing for the server:

```python
from valgebra_planning.codemode import feedback
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import critique
from valgebra_planning.planner import plan
from valgebra_planning.program import render
from valgebra_planning.run import run

ORDER_ID = {"type": "string", "pattern": "^ord_[0-9]+$"}
TOOLS = [
    {
        "name": "find_order",
        "inputSchema": {
            "type": "object",
            "properties": {"order_id": ORDER_ID},
            "required": ["order_id"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"enum": ["open", "paid", "shipped"]},
                "total_cents": {"type": "integer", "minimum": 0},
            },
            "required": ["status", "total_cents"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "refund",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": ORDER_ID,
                "status": {"const": "paid"},
                "amount_cents": {"type": "integer", "minimum": 1},
            },
            "required": ["order_id", "status", "amount_cents"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"refund_id": {"type": "string", "pattern": "^rfd_[0-9]+$"}},
            "required": ["refund_id"],
        },
    },
    {
        "name": "escalate",
        "inputSchema": {
            "type": "object",
            "properties": {"order_id": ORDER_ID},
            "required": ["order_id"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string", "pattern": "^tkt_[0-9]+$"}},
            "required": ["ticket_id"],
        },
    },
]
contracts = {tool["name"]: read_contract(tool) for tool in TOOLS}
task = read_task(
    "refund an order",
    {"order_id": ORDER_ID},
    {"type": "string", "pattern": "^(rfd|tkt)_[0-9]+$"},
)

proposed = critique(
    "c1 = find_order(order_id=order_id)\n"
    "c2 = refund(order_id=order_id, status=c1.status, amount_cents=c1.total_cents)\n"
    "return c2.refund_id\n",
    contracts,
    task,
)
print(proposed.accepted)
print(feedback(proposed))

repaired = critique(
    """
c1 = find_order(order_id=order_id)
if c1.status in refund.status:
    if c1.total_cents in refund.amount_cents:
        c2 = refund(order_id=order_id, status=c1.status, amount_cents=c1.total_cents)
        return c2.refund_id
    else:
        c3 = escalate(order_id=order_id)
        return c3.ticket_id
else:
    c3 = escalate(order_id=order_id)
    return c3.ticket_id
""",
    contracts,
    task,
)
checked = repaired.verification
print(repaired.accepted, checked.count("proven"), "of", len(checked.links), "links proven")

server = {
    "find_order": lambda order_id: {"status": "paid", "total_cents": 2500},
    "refund": lambda order_id, status, amount_cents: {"refund_id": "rfd_9"},
    "escalate": lambda order_id: {"ticket_id": "tkt_4"},
}
print(run(repaired.program, contracts, task, {"order_id": "ord_7"}, server).value)

print(render(plan(contracts, task).program))
```

```text
False
refuted: c1.status <= refund.status; 'open' lies outside; test `if c1.status in refund.status:` and make the call in that branch
refuted: c1.total_cents <= refund.amount_cents; test `if c1.total_cents in refund.amount_cents:` and make the call in that branch
True 9 of 9 links proven
rfd_9
c1 = escalate(order_id=order_id)
return c1.ticket_id
```

The first program refunds whatever order the lookup returns. valgebra refutes both links it
rests on: an `open` order lies outside `refund`'s `status`, and a total of `0` outside its
`amount_cents`. The second tests each value into the argument it fills and escalates every other
order, so every link is proven, and `run` checks each argument record and result again as the
calls are made. `codemode.repair` runs the same loop with any callable from the last critique to
a program as the model ([code-mode.md](code-mode.md)).

**The goal is the specification.** `plan` finds the shallowest program whose every path returns
inside the goal, and for this task that is to escalate every order: a goal admitting a ticket
admits a program that only ever opens one. A goal of refunds alone leaves an `open` or a
`shipped` order, and a paid one totaling `0`, no program, and the planner returns the second
program with a stop where it escalates: a weak program, whose stops name those values. The
critic reads the language `codemode.stubs` states and refuses any other construct by its line,
with `ProgramError`.

## A server that answers outside its contract

A proof holds over the tool contracts, so a server returning a value its own output schema
refuses is the case a run must not pass on. `run` checks each result before binding it and ends
the run at the first that breaks, so no later call reads the value; `blame` names whose break it
is:

```python
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import critique
from valgebra_planning.run import blame, run

STATUS = {"enum": ["open", "paid", "shipped"]}
find_order = read_contract(
    {
        "name": "find_order",
        "inputSchema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"status": STATUS},
            "required": ["status"],
        },
    }
)
contracts = {"find_order": find_order}
task = read_task("an order's status", {"order_id": {"type": "string"}}, STATUS)
checked = critique("c1 = find_order(order_id=order_id)\nreturn c1.status\n", contracts, task)

server = {"find_order": lambda order_id: {"status": "refunded"}}
ended = run(checked.program, contracts, task, {"order_id": "ord_7"}, server)
print(checked.accepted, ended.value)
print(ended.refused)
print(blame(ended, contracts, proven=checked.accepted))
```

```text
True None
find_order returned {'status': 'refunded'}, outside its contract
find_order broke the output schema its server declares
```

A proven program breaks at run time only where something it rests on fails, here the server's
own output schema; an argument record, a policy or a returned value breaking in a proven program
is a defect to report. With `enforce=False` the run records every break and goes on: a diagnosis
of what a server returns, not a guard.

**A run is not a proof.** It checks the values one run sees, and the next run's server may
answer otherwise. An annotation that lies, a `readOnlyHint` on a tool that writes, breaks no
check a run can see.

## A refund over a bound, approved first

A rule about the order of calls is a policy: an automaton over a program's calls whose guards
are sets of argument records (`valgebra_planning/policy.py`). The verifier follows every path
of a program through it, and a path that may reach a bad state is a link that is not proven:

```python
from valgebra_planning.codemode import feedback
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import critique
from valgebra_planning.policy import read_policy
from valgebra_planning.run import run


def tool(name, arguments, result):
    """Return the contract of a tool listed with these properties, every one required."""
    return read_contract(
        {
            "name": name,
            "inputSchema": {"type": "object", "properties": arguments, "required": [*arguments]},
            "outputSchema": {"type": "object", "properties": result, "required": [*result]},
        }
    )


ORDER_ID = {"type": "string", "pattern": "^ord_[0-9]+$"}
CENTS = {"type": "integer", "minimum": 1}
REFUND_ID = {"type": "string", "pattern": "^rfd_[0-9]+$"}
contracts = {
    "find_order": tool("find_order", {"order_id": ORDER_ID}, {"total_cents": CENTS}),
    "approve_refund": tool("approve_refund", {"order_id": ORDER_ID}, {"approval_id": {}}),
    "refund": tool(
        "refund", {"order_id": ORDER_ID, "amount_cents": CENTS}, {"refund_id": REFUND_ID}
    ),
}
task = read_task("refund an order", {"order_id": ORDER_ID}, REFUND_ID)
approved_first = read_policy(
    {
        "name": "a refund over 100.00 is approved first",
        "initial": "start",
        "bad": ["violated"],
        "transitions": [
            {"from": "start", "tool": "approve_refund", "to": "approved"},
            {
                "from": "start",
                "tool": "refund",
                "arguments": {"properties": {"amount_cents": {"minimum": 10001}}},
                "to": "violated",
            },
        ],
    }
)

PROGRAM = """
c1 = find_order(order_id=order_id)
c2 = refund(order_id=order_id, amount_cents=c1.total_cents)
return c2.refund_id
"""
unapproved = critique(PROGRAM, contracts, task, policies=[approved_first])
print(feedback(unapproved))

APPROVED = PROGRAM.replace("c2 = refund", "c3 = approve_refund(order_id=order_id)\nc2 = refund")
print(critique(APPROVED, contracts, task, policies=[approved_first]).accepted)

server = {
    "find_order": lambda order_id: {"total_cents": 25000},
    "approve_refund": lambda order_id: {"approval_id": "apr_1"},
    "refund": lambda order_id, amount_cents: {"refund_id": "rfd_9"},
}
ran = run(
    unapproved.program, contracts, task, {"order_id": "ord_7"}, server, policies=[approved_first]
)
print(ran.refused)
```

```text
undecided: policy a refund over 100.00 is approved first holds; this path reaches a bad state of the policy or ends in a pending one: make the call it requires where it requires it, or leave out the call it forbids
True
a refund over 100.00 is approved first: refund reached violated
```

The refund takes whatever total the order holds, which may or may not exceed the bound, so the
bad state is reached possibly and the link is undecided; a refund of a constant above the bound
reaches it surely, and the link is refuted. A program calling `approve_refund` first is proven
for every total. Run with the policy, the runner steps it on each call before the call is made,
so the unapproved refund of 250.00 never reaches the server. `plan(..., policies=[...])` makes
no call that breaks a policy, and finds the approved program itself.

**A policy carries no value from one call to another.** It reads that some approval came first,
not that it approved this order: a program approving one order and refunding another passes it.
That one call takes the value another returned is a flow rule's, below.

## A notice to the address on file

A value the user wrote, or a page a tool fetched, may hold an address an attacker chose. A flow
rule with `only` holds a sink argument to the values its named sources return
(`valgebra_planning/flows.py`): every other origin, a task input or a constant included, reaches
the sink only inside the rule's endorsed set, and here the rule endorses none. The planner makes
no binding that breaks it, and the critic refutes one:

```python
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import critique
from valgebra_planning.flows import read_flow
from valgebra_planning.planner import plan
from valgebra_planning.program import render

ORDER_ID = {"type": "string", "pattern": "^ord_[0-9]+$"}
EMAIL = {"type": "string", "pattern": "^[^@]+@[^@]+$"}
NOTICE_ID = {"type": "string", "pattern": "^ntc_[0-9]+$"}
contracts = {
    spec["name"]: read_contract(spec)
    for spec in (
        {
            "name": "find_order",
            "inputSchema": {
                "type": "object",
                "properties": {"order_id": ORDER_ID},
                "required": ["order_id"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {"email": EMAIL},
                "required": ["email"],
            },
        },
        {
            "name": "notify",
            "inputSchema": {
                "type": "object",
                "properties": {"email": EMAIL, "text": {"type": "string"}},
                "required": ["email", "text"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {"notice_id": NOTICE_ID},
                "required": ["notice_id"],
            },
        },
    )
}
# `reply_to` is an address the user's message gave.
task = read_task("notify an order's customer", {"order_id": ORDER_ID, "reply_to": EMAIL}, NOTICE_ID)
on_file = read_flow(
    {
        "name": "notices go to the address on file",
        "sources": ["find_order"],
        "sink": "notify.email",
        "only": True,
    }
)

print(render(plan(contracts, task).program))
print(render(plan(contracts, task, flows=[on_file]).program))
proposed = critique(
    'c1 = notify(email=reply_to, text="refunded")\nreturn c1.notice_id\n',
    contracts,
    task,
    flows=[on_file],
)
print(proposed.notes[0])
```

```text
c1 = notify(email=reply_to, text=order_id)
return c1.notice_id
c1 = find_order(order_id=order_id)
c2 = notify(email=c1.email, text=order_id)
return c2.notice_id
refuted: flow notices go to the address on file: reply_to from reply_to reaches notify.email; pass a value from a source the rule trusts, or test it into the rule's endorsed set first
```

Without the rule the shortest program sends the notice to the address the message gave; with it
the planner looks the order up first. An identifier the program did not retrieve is the
integrity leak of Sah et al. 2026, and the critic refutes it before the run.

**Types are the specification.** The planner fills `text` with the order's identifier, since
`text` admits any string and the identifier is a string the agent holds: a slot whose schema
admits any string takes any string. **Provenance is a reference here, and an assumption in the
proxy.** The critic reads where each value of a program came from; `valgebra-planning proxy`
sees no program, reads a value's source by its equality with what a source returned, and holds
against no attacker that adapts
([code-mode.md](code-mode.md#the-proxy-for-an-agent-whose-program-is-not-seen)).

## A privilege policy update

A privilege policy in Progent's format (Shi et al. 2025) maps each tool to its rules,
`[priority, effect, condition, fallback]`, effect `0` allowing and `1` forbidding, so the calls
it allows a tool are a set. Whether an update narrows a policy is an inclusion between two such
sets, every rule and argument at once; an expansion comes with a call the new policy allows and
the old one denies:

```python
from valgebra_planning.privilege import describe, read_privileges, updates

OLD = {
    "refund": [[1, 0, {"amount_cents": {"type": "integer", "maximum": 10000}}, 0]],
    "notify": [[1, 0, {"email": "^[a-z.]+@example\\.com$"}, 0]],
}
NEW = {
    "refund": [[1, 0, {"amount_cents": {"type": "integer", "maximum": 50000}}, 0]],
    "notify": [
        [1, 1, {"email": "^ops@"}, 0],
        [2, 0, {"email": "^[a-z.]+@example\\.com$"}, 0],
    ],
}
for update in updates(read_privileges(OLD), read_privileges(NEW)):
    print(describe(update))
```

```text
notify: narrowing
refund: expansion, allowing {"amount_cents": 15968}
```

A narrowing can be applied without asking, and an expansion needs approval, as Progent has it.
`valgebra-planning privileges old.json new.json` prints the same lines and exits 0 only when
every tool narrows, so a policy file under review gates its own change
([command-line.md](command-line.md#compare-two-privilege-policies)); `privilege.as_policy` holds a
program to a policy as the planner, the verifier and the runner hold any other.

**A condition reads only the arguments a call passes,** as Progent's check does, so a call
leaving `amount_cents` out meets every bound on it. A rule asking the user, fallback `2`, is
read as denied, and so is every call to a tool a policy names no rule for.

## A policy that cannot fire

A policy whose transition names a tool the library does not hold is never violated, so every
program passes it: a check that passes without testing what its author meant is vacuous
(Kupferman and Vardi 1999). `vacuities` finds each such specification in a library, every one a
set question:

```python
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.library import Library, Problem
from valgebra_planning.policy import read_policy
from valgebra_planning.vacuity import vacuities

ORDER_ID = {"type": "string", "pattern": "^ord_[0-9]+$"}
contracts = {
    name: read_contract(
        {
            "name": name,
            "inputSchema": {
                "type": "object",
                "properties": {"order_id": ORDER_ID},
                "required": ["order_id"],
            },
            "outputSchema": {"type": "string"},
        }
    )
    for name in ("approve_refund", "refund")
}
approved_first = read_policy(
    {
        "name": "a refund is approved first",
        "initial": "start",
        "bad": ["violated"],
        "transitions": [
            {"from": "start", "tool": "approve_refund", "to": "approved"},
            {"from": "start", "tool": "refunds", "to": "violated"},
        ],
    }
)
task = read_task("refund an order", {"order_id": ORDER_ID}, {"type": "string"})
library = Library(
    contracts, (Problem(task, policies=(approved_first,)),), {approved_first.name: approved_first}
)
for finding in vacuities(library):
    print(f"{finding.where}: {finding.finding} [{finding.certainty}]")
```

```text
policy a refund is approved first, transition 1 (start -> violated): names refunds, no tool [proven]
policy a refund is approved first: no transition that can fire reaches a bad or pending state [proven]
```

`valgebra-planning check` prints these findings for a library directory, an undecided one marked
so, and exits 1 when it finds one ([command-line.md](command-line.md#check-a-library)). A value drawn
from a schema is tried first, a member found proving a set inhabited, and valgebra is asked only
where none is.

**Vacuous is not wrong.** A guard meeting some argument records but not the ones its author
meant, a bound written in euros over an argument in cents, is not vacuous and is not flagged. A
guard over an argument the tool does not name is flagged only where the input schema sets
`additionalProperties: false`: an open record may carry any other key, so the guard meets one.
