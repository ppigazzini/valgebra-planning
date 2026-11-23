---
description: The studies, each count with the command that checks it.
---

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

## A booking under injected faults

```bash
uv run python tools/faults.py examples/bookings --check docs/studies/faults.json
```

`examples/bookings` holds `book`, which is not idempotent, `find_booking`, which reads a parcel's
label once one is booked, and the postcondition that whenever `book` acts, `find_booking` on the
same parcel shows a label. Three programs are verified and run, 400 seeded episodes for each
fault mode and rate: the one planned without the postcondition, which stops on an error of
`book`; the one planned with it, which reads the booking after the error and books again only
where the read shows no label; and a blind retry, which books again whatever happened, the
retry the verifier refutes. A call to `book` faults in one of the failure modes of non-atomic
tools (Mansoor et al. 2026): acting and then answering with an error, or answering with an
error without acting, half each; acting with the next read stale; or leaving a booking with no
label.

| faults | rate | planned: stopped, duplicates | with the postcondition: stopped, duplicates | blind retry: stopped, duplicates |
|---|---:|---|---|---|
| act or not, then an error | 0.1 | 37, 0 | 2, 0 | 5, 16 |
| act or not, then an error | 0.3 | 114, 0 | 18, 0 | 29, 47 |
| act or not, then an error | 0.5 | 191, 0 | 49, 0 | 102, 82 |
| act, then a stale read | 0.1 | 37, 0 | 8, 37 | 8, 37 |
| act, then a stale read | 0.3 | 128, 0 | 48, 128 | 48, 128 |
| act, then a stale read | 0.5 | 190, 0 | 100, 190 | 100, 190 |
| a booking with no label | 0.1 | 33, 0 | 2, 33 | 2, 33 |
| a booking with no label | 0.3 | 126, 0 | 35, 126 | 35, 126 |
| a booking with no label | 0.5 | 199, 0 | 89, 199 | 89, 199 |

- Where the read reports what the call did, the block turns most stops into returns and books
  no parcel twice: 49 stops in 400 at a fault rate of 0.5, where the program without it stops
  in 191.
- A stale read and a partial booking are where the postcondition is false: the read shows no
  label after a call that acted. There the block books twice as often as the blind retry. Both
  are what its proof names: that the read sees the effect at once, and that `shows` is the
  weakest trace any effect leaves, which a label-less booking is not.
