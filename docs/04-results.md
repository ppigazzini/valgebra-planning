# Results

Every count below is a row of a study file, and the command beside it fails when this code
no longer produces it. Times depend on the machine and are not recorded.

## Parcel returns

```bash
uv run python tools/case.py examples/parcels --check docs/studies/parcels.json
```

`examples/parcels` holds ten tools: a lookup returning a parcel's state, weight, country and
contact, one tool per state, two of them split by weight, a freight approval, a customs form, a
contact check and a notice. Each task is planned, then verified by valgebra alone.

| task | program | links proven | stops | cells (undecided) |
|---|---|---|---:|---|
| settle a parcel | strong | 18 of 18 | 0 | 18 (0) |
| settle a parcel, tools may fail | weak | 22 of 22 | 2 | 18 (0) |
| settle a parcel, freight approved first | strong | 24 of 24 | 0 | 18 (0) |
| return a parcel | weak | 9 of 9 | 1 | 14 (0) |
| customs for a parcel | weak | 3 of 3 | 1 | 11 (0) |
| notify a parcel's contact | strong | 3 of 3 | 0 | 11 (0) |
| notify a parcel's contact, verified addresses only | strong | 4 of 4 | 0 | 11 (0) |
| notify a parcel's contact, types mined from observed calls | strong | 4 of 4 | 0 | 11 (0) |

- Settling a parcel branches five ways on its state and weight. When tools may fail it retries
  the lookup and the idempotent calls and stops where a booking fails, since retrying a booking
  may book twice. Under the policy that freight is approved first, it approves right before the
  freight booking, on that path alone.
- A weak program's stops name what they leave: `lost`, `in_transit` and `returned` for a return,
  `IT`, `FR` and `DE` for a customs form.
- A notice goes to the contact as the sender wrote it, unless a flow rule or the types mined from
  observed calls route it through the contact check; under mined types the plan names both
  bindings that rest on them.
- `tests/test_run.py`, `tests/test_outcomes.py` and `tests/test_policy.py` run these programs
  against tools answering with values drawn from their own output schemas, and failing three
  times in ten: no contract is broken, and the monitor reports every policy violation the
  verifier refutes. Enforced, a run never makes the call a policy forbids, never sends an
  argument record outside the input set and never binds a result outside its contract.

## Libraries of growing size

```bash
uv run python tools/bench.py --check docs/studies/bench.json
```

Synthetic libraries of 8 to 256 tools hold a planted chain of four beside tools no held value can
call and tools whose results nothing reads, each size in three tool orders. At every size the
reachability pruning leaves 5 predicates and 12 cells, and the strong program is found and
proven, 5 of 5 links.

## Real servers

```bash
uv run --no-sync python tools/census.py --check docs/studies/census.json
```

The seven official MCP reference servers, each pinned to one release (`SERVERS` in
`tools/census.py`), list 66 tools, stored as listed under `examples/census/`. valgebra-jsonschema reads every one, and 31 carry an
`outputSchema`, which is 12 distinct schemas, all 16 of filesystem's being one text wrapper. Of
the 126 arguments, 72 carry a bare type and nothing more. Cut into cells together, as the planner
cuts them, the 106 predicates give 127 cells, of which 92 are undecided, 91 of them array cells:
arrays of records met with the complements of other arrays of records, 63 of them with arrays of
strings too, which valgebra does not part. They are kept, so no wrong program is admitted, and
they cost the search. The cut asks valgebra 3,082 relations.

The count reads the stored listings, with no network, so it is in the gate. A pinned server is
not a pinned listing: its dependencies resolve when it starts and its SDK writes the schemas, so
`tools/census.py --refresh --check docs/studies/census.json` reads the servers again and reports
a listing that moved. A moved listing, or a new release of a server, is a new study.
