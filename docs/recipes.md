---
description: Tasks done end to end over tool listings in hand -- a model's program checked before it runs, a server answering outside its contract, an approval a policy requires, a notice held to the address on file, a privilege update, a policy that cannot fire, and a contract a plan can mean.
---

# Recipes

Each recipe is one task done end to end in Python, over tool listings already in hand as a
server's `tools/list` returns them, and each says what it cannot do. Each block runs as written
and prints the text that follows it. The same steps have [a command line](command-line.md), and
what each answer means is [the method](method.md)'s.

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
                "status": {"type": "string", "const": "paid"},
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
a program as the model ([code mode](code-mode.md)).

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
find_order returned a value outside its contract (status: union_error)
find_order broke the output schema its server declares
```

The refusal names the field and valgebra's code, never the value: a result outside its contract
reaches no reader through the message that refuses it.

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
That one call takes the value another returned is a flow rule's, below. At run time a policy
naming `"per": "order_id"` holds an instance for each order, so the runner refuses a refund of an
order no approval was for; before the run it proves none of its links.

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
([the proxy](code-mode.md#the-proxy-for-an-agent-whose-program-is-not-seen)).

## A privilege policy update

A privilege policy in Progent's format maps each tool to its rules, each with a priority, an
effect, a condition and a fallback (Shi et al. 2025); valgebra-planning reads a rule as the list
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
([compare two privilege policies](command-line.md#compare-two-privilege-policies)); `privilege.as_policy` holds a
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
so, and exits 1 when it finds one ([check a library](command-line.md#check-a-library)). A value drawn
from a schema is tried first, a member found proving a set inhabited, and valgebra is asked only
where none is.

**Vacuous is not wrong.** A guard meeting some argument records but not the ones its author
meant, a bound written in euros over an argument in cents, is not vacuous and is not flagged. A
guard over an argument the tool does not name is flagged only where the input schema sets
`additionalProperties: false`: an open record may carry any other key, so the guard meets one.

## A contract a plan can mean

The planner and the critic pass a value to an argument wherever valgebra proves it inside the
argument's set, so a plan means what its contracts let it mean: an argument taking any string
takes any string a program holds. A listing written to be planned over states, of each value,
the set it lies in:

- **A closed result, and `type` beside each `const`.** A result record with
  `additionalProperties: false` holds the fields it names and no other. `{"type": "string",
  "const": "m"}` holds the one value `{"const": "m"}` does, and a template showing a model the
  schema that drops `const` still shows it a string. A listing that cannot be changed is shown
  to a model through `proxy --spell-out`, which writes each `const` as `type` and a one-value
  `enum` where valgebra proves the two equal, and is read
  closed with `"closedResults": true` in `tools.json`, an assumption about the server each link
  over a result names, and `check` notes, where it is not set, a result meeting an argument only
  through keys its schema does not declare.
- **A number with its unit, bounded.** A distance is a record, `{"unit": "m", "value": ...}`,
  whose value has bounds, and an argument in reach is a narrower set: a distance reaches it only
  under a test, `if c2.distance in grasp.distance:`, and a server answering in centimeters
  breaks its contract at the run, before any grasp.
- **A pattern per entity, an enum per outcome.** `^obj_[0-9]+$` and `^grs_[0-9]+$` are disjoint,
  so a grasp's id never fills an object's argument. An answer is `"yes"` or `"no"`, and an
  argument taking only `"yes"` is filled under a test that the answer is one. A `format` reads as
  an annotation, taking any string, unless the library sets `"assertFormat": true`; a pattern
  reads the same either way.
- **A confirmation is a token an `only` flow holds.** `grasp` takes the answer it acts on, and
  the flow admits to `grasp.answer` only a value `confirm` returned. A token alone is forged from
  any value whose set meets its own: the task's `name`, tested into `grasp.answer`, fills it.
  `confirm` takes the action and the object, so the question a user answers is worded from them
  rather than by the model.
- **A hint policy, read with MCP's defaults.** A tool setting no annotation is read as MCP
  defaults it, destructive and open-world, so `grasp` falls under "a destructive call follows a
  confirmation", as would a tool listed later setting none. The hints are the server's word: the
  link lists them as assumed unless the library marks the server trusted, `"trusted": true`.

```python
from valgebra_planning.contracts import read_contract, read_task
from valgebra_planning.critic import critique
from valgebra_planning.flows import read_flow
from valgebra_planning.policy import read_policy
from valgebra_planning.run import run


def closed(properties):
    """Return the schema of a record holding exactly these properties."""
    return {
        "type": "object",
        "properties": properties,
        "required": [*properties],
        "additionalProperties": False,
    }


def tool(name, arguments, result, annotations=None):
    """Return the contract of a tool whose arguments and result are closed records."""
    listed = {"name": name, "inputSchema": closed(arguments), "outputSchema": closed(result)}
    if annotations is not None:
        listed["annotations"] = annotations
    return read_contract(listed)


OBJECT_ID = {"type": "string", "pattern": "^obj_[0-9]+$"}
GRASP_ID = {"type": "string", "pattern": "^grs_[0-9]+$"}
METERS = {"type": "string", "const": "m"}
DISTANCE = closed({"unit": METERS, "value": {"type": "number", "minimum": 0, "maximum": 20}})
IN_REACH = closed({"unit": METERS, "value": {"type": "number", "minimum": 0.2, "maximum": 0.6}})
ANSWER = {"type": "string", "enum": ["yes", "no"]}
READ_ONLY = {"readOnlyHint": True}
contracts = {
    "detect": tool("detect", {"name": {"type": "string"}}, {"object_id": OBJECT_ID}, READ_ONLY),
    "estimate_pose": tool(
        "estimate_pose", {"object_id": OBJECT_ID}, {"distance": DISTANCE}, READ_ONLY
    ),
    "confirm": tool(
        "confirm",
        {"action": {"type": "string", "enum": ["grasp", "walk"]}, "object_id": OBJECT_ID},
        {"answer": ANSWER},
        READ_ONLY,
    ),
    "grasp": tool(
        "grasp",
        {
            "object_id": OBJECT_ID,
            "distance": IN_REACH,
            "answer": {"type": "string", "const": "yes"},
        },
        {"grasp_id": GRASP_ID},
    ),
}
ends = {
    "estimate_pose": {"properties": {"distance": {"not": IN_REACH}}},
    "confirm": {"properties": {"answer": {"const": "no"}}},
}
task = read_task("pick", {"name": {"type": "string"}}, GRASP_ID, stops=ends)
the_users_yes = read_flow(
    {"name": "the user's yes", "sources": ["confirm"], "sink": "grasp.answer", "only": True}
)
confirmed_first = read_policy(
    {
        "name": "a destructive call follows a confirmation",
        "initial": "asking",
        "bad": ["violated"],
        "transitions": [
            {"from": "asking", "tool": "confirm", "to": "confirmed"},
            {"from": "asking", "hint": "destructiveHint", "to": "violated"},
            {"from": "confirmed", "hint": "destructiveHint", "to": "asking"},
        ],
    }
)
rules = {"flows": [the_users_yes], "policies": [confirmed_first]}
print(sorted(contracts["grasp"].hints))

FORGED = """
c1 = detect(name=name)
c2 = estimate_pose(object_id=c1.object_id)
if c2.distance in grasp.distance:
    if name in grasp.answer:
        c3 = grasp(object_id=c1.object_id, distance=c2.distance, answer=name)
        return c3.grasp_id
    stop(name)
stop(c2.distance)
"""
print(critique(FORGED, contracts, task).verified)
for note in critique(FORGED, contracts, task, **rules).notes:
    print(note)

ASKED = """
c1 = detect(name=name)
c2 = estimate_pose(object_id=c1.object_id)
if c2.distance in grasp.distance:
    c3 = confirm(action="grasp", object_id=c1.object_id)
    if c3.answer in grasp.answer:
        c4 = grasp(object_id=c1.object_id, distance=c2.distance, answer=c3.answer)
        return c4.grasp_id
    stop(c3.answer)
stop(c2.distance)
"""
asked = critique(ASKED, contracts, task, **rules)
print(asked.weak)
for note in asked.notes:
    print(note)

server = {
    "detect": lambda name: {"object_id": "obj_1"},
    "estimate_pose": lambda object_id: {"distance": {"unit": "cm", "value": 40}},
    "confirm": lambda action, object_id: {"answer": "yes"},
    "grasp": lambda object_id, distance, answer: {"grasp_id": "grs_1"},
}
ran = run(asked.program, contracts, task, {"name": "cup"}, server, policies=[confirmed_first])
print(ran.refused)
```

```text
['destructiveHint', 'openWorldHint']
True
refuted: flow the user's yes: name from name reaches grasp.answer (at then.then); pass a value from a source the rule trusts, or test it into the rule's endorsed set first
refuted: policy a destructive call follows a confirmation holds (at then.then); this path reaches a bad state of the policy or ends in a pending one: make the call it requires where it requires it, or leave out the call it forbids
uncovered: name (at then.else)
uncovered: c2.distance (at else); the task tolerates it
assumed: the annotation hints policy a destructive call follows a confirmation reads
True
uncovered: c3.answer (at then.else); the task tolerates it
uncovered: c2.distance (at else); the task tolerates it
assumed: the annotation hints policy a destructive call follows a confirmation reads
estimate_pose returned a value outside its contract (distance.unit: literal_error; distance.value: union_error)
```

Over the contracts alone, the program filling the answer with the task's `name` is verified:
its contracts are met, and it never asks. The flow refutes it, and so does the policy, since it
grasps with no confirmation before. The program asking first is verified under both, and stops
where the object is out of reach or the user says no, the two ends the task's `stops` name, so
it is weak (`Critique.weak`) and `repair(..., accept="weak")` ends on it. Run against a server
answering in centimeters, it stops at the pose, whose result breaks its contract.

**The flow reads where the answer came from, not what it was asked of.** A program confirming
one object and grasping another passes it, as a policy passes an approval of another order. The
server, which takes both calls with the object, can check that the grasp's object is the one
the answer was given for. A proxy in front of one robot holds a policy across every agent only
where every agent calls through it
([the proxy](code-mode.md#the-proxy-for-an-agent-whose-program-is-not-seen)).
