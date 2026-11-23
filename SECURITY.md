# Security policy

valgebra-planning is **pre-alpha**, and the code has not been independently audited: do not place
it on a trust boundary in production before a stable release and an external review.

## Supported versions

Only the latest published release receives fixes. A fix ships as the next release rather than as a
backport to an earlier one.

## What is a security issue

A caller trusts that a link is accepted on a proof or not at all, and that the runner and the proxy
fail closed. Please report, privately:

- **A program accepted that can pass a value outside a tool's argument.** The verifier or the
  critic accepts a program, and some outputs inside the tools' output schemas lead it to call a
  tool with an argument record outside that tool's input schema, or to break a rule its task
  names: a policy, a flow rule, a retry or an error handled.
- **A check passed on.** With `enforce` left on, the runner makes a call, binds a result or
  returns a value whose check broke; or the proxy forwards a call, or returns a result, that
  breaks a contract, a policy step or a flow rule, or answers a result whose output schema it
  checked with text, or a block other than text its library does not name, beside the value.
- **A crash or unbounded cost within the bounds.** Reading a library, planning within its search
  bounds or verifying a program that crashes the interpreter, raises past Python's recursion
  limit, or takes time or memory without bound.

A defect in reading a schema is reported to
[valgebra-jsonschema](https://github.com/ppigazzini/valgebra-jsonschema/blob/main/SECURITY.md), and
one in deciding a set to [valgebra](https://github.com/ppigazzini/valgebra/blob/main/SECURITY.md); a
report here that turns out to be one moves there.

## What is not a security issue

- **A cell valgebra leaves undecided.** It is kept, so it can cost a program and never admit a
  wrong one.
- **A mined type.** It is an assumption: the verifier reads none, and a plan names each binding
  that rests on one.
- **A limit [the method](docs/method.md#limits) states**, such as a policy carrying
  no value between calls, or a run with `enforce=False`, which records a break and goes on: a
  diagnosis, never a guard.
- **A program refused that would have been safe.** It is a defect to report as an issue.

## Reporting a vulnerability

Do **not** open a public issue for a suspected vulnerability. Use GitHub's private vulnerability
reporting, the **Report a vulnerability** button under the repository's *Security* tab, which opens
a private advisory with the maintainer, or email the maintainer at the address valgebra's
[security policy](https://github.com/ppigazzini/valgebra/blob/main/SECURITY.md#reporting-a-vulnerability)
gives, with "valgebra-planning security" in the subject.

Include the library's tool listing, the program or the task, the answer given, and the call,
binding or value that breaks the contract or the rule.

## Process

This is a solo, pre-release project, so timelines are best effort: an acknowledgment within
**7 days**, an assessment within **30 days**, and a fix and an advisory published together,
crediting the reporter unless anonymity is asked for.
