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

## Robot skills

```bash
uv run python tools/case.py examples/robot-skills --check docs/studies/robot-skills.json
```

`examples/robot-skills` holds nine skills of a humanoid robot as an adopter's report on this
package describes them: a detection, a pose estimate, a walk, a grasp, a scan listing nearby
objects, speech, a joint move whose speed is bounded by `additionalProperties`, a speed reading
and a user's confirmation. Two flow rules hold the grasp's distance: `measured`, read from
`estimate_pose` for the object grasped and after the last call that may act, and `scanned`, from a
scan's item for that object. Each task is planned and verified as the parcels' are.

| task | program | links proven | stops | cells (undecided) |
|---|---|---|---:|---|
| pick | strong | 3 of 3 | 0 | 12 (0) |
| pick, measured | strong | 6 of 6 | 0 | 12 (0) |
| pick, scanned | none | -- | -- | 12 (0) |
| pick, never grasping | none | -- | -- | 12 (0) |
| pick, tools may fail | weak | 3 of 3 | 1 | 12 (0) |
| move a joint | weak | 4 of 4 | 1 | 12 (0) |
| say | weak | 2 of 2 | 1 | 13 (0) |
| sweep a scan | strong | 4 of 4 | 0 | 12 (0) |
| confirm a grasp | strong | 2 of 2 | 0 | 13 (0) |

- A pick under `measured` measures the object and grasps it at once. Under `scanned` no program
  is found within 4 calls and tests, and under a policy refusing every grasp none can be.
- A joint's speed is read from the pose estimate and tested into the bound before the move; the
  speech stops where the status is not `spoken`.
- The confirmation is asked with the object alone: the action the task fixes,
  `confirm_with_user.action`, is an optional argument, and the planner leaves it out.

The library also holds 24 programs written as an agent writes one (`programs.json`). Each is
read, critiqued under its task's rules, and run where a row of the file scripts the tools'
answers. A verdict is `accepted` where every link is proven and no path stops, `proven, weak`
where a stop the task tolerates is left, and `proven, no return` where no execution returns.

| program | task | verdict | run |
|---|---|---|---|
| an error block grasps at the reading made before the walk | pick, measured | refuted: `flow measured: c1.distance_m from estimate_pose was returned after every call before grasp that may act` | refused: estimate_pose, walk_to, estimate_pose |
| an error block grasps one object at another's reading | pick, measured | refuted: `flow measured: c1.distance_m from estimate_pose was returned for the object_id grasp takes` | refused: estimate_pose, estimate_pose |
| a grasp reads a detection the program has since replaced | pick, measured | refuted: `flow measured: c2.distance_m from estimate_pose was returned for the object_id grasp takes` | refused: detect, estimate_pose, detect |
| a loop grasps one object at the distance of another's item | pick, scanned | refuted: `flow scanned: it.distance_m from scan was returned for the object_id grasp takes`; `return c1 <= goal` | refused: scan, scan |
| the same loop, its item named as a call's result | pick, scanned | refuted: `flow scanned: c1.distance_m from scan was returned for the object_id grasp takes`; `return c1 <= goal` | refused: scan, scan |
| a branch rebinds the reading the other side grasps at | pick, measured | refuted: `flow measured: c1.distance_m from estimate_pose was returned for the object_id grasp takes` | refused: estimate_pose, detect |
| the same branch, the faulty grasp on the side the test names | pick, measured | refuted: `flow measured: c1.distance_m from estimate_pose was returned for the object_id grasp takes` |  |
| a loop rebinds the reading the grasp after it reads | pick, measured | refuted: `flow measured: c2.distance_m from estimate_pose was returned for the object_id grasp takes` | refused: estimate_pose, scan, estimate_pose |
| a grasp tested against a tool no library holds | pick, never grasping | refuted: `grasp_object is a tool` |  |
| a grasp tested against an argument the grasp lacks | pick, never grasping | refuted: `grasp.distance is an argument` |  |
| a failed grasp retried on the walked-to object's other name | pick | refuted: `grasp may be retried` |  |
| a failed grasp retried on the object as given | pick | refuted: `grasp may be retried` |  |
| a joint moved at a speed read from any number | move a joint | refuted: `c0.v <= move_joint.speed` |  |
| a stop on a field the result may lack | say | refuted: `c1.reason is a value the program holds` |  |
| a return on a status the result never takes | say | proven, no return |  |
| a loop whose body tests, then calls | sweep a scan | proven, no return | stopped: scan, grasp, walk_to |
| a loop whose body tries, then calls | sweep a scan | proven, no return | stopped: scan, grasp, detect, walk_to |
| a test against a string | sweep a scan | refused, line 2 |  |
| a test against bytes | sweep a scan | refused, line 2 |  |
| a test against an ellipsis | sweep a scan | refused, line 2 |  |
| a test against a complex number | sweep a scan | refused, line 2 |  |
| an argument named for a keyword | sweep a scan | refused, line 1 |  |
| a grasp at a reading the walk has made stale | pick, measured | refuted: `flow measured: c1.distance_m from estimate_pose was returned after every call before grasp that may act` | refused: estimate_pose, walk_to |
| a confirmation leaving out the action the task fixes | confirm a grasp | accepted |  |

- Five programs grasp at a reading or a detection through a name the program rebinds before the
  grasp: in an error block, after a second detection, in a branch the grasp is not on, and in a
  loop. Each is refuted under `measured` on the path that reads the stale value or another
  object's, and a loop whose item takes a call's name is refuted under `scanned`: the verifier
  reads the value a name holds on the path, a version of its own for each result, item and
  input. Each is refused at its grasp when run on the path the scripted answers take, and so is
  the stale reading: a run binds each name to the call that returned it and holds the rule on
  the values, so the grasp is never made.
- A test against a tool the library lacks is refuted, as one against an argument the grasp lacks
  is: neither reads a set, and neither side either guards is walked.
- A failed grasp retried on the walked-to object's other name is refuted, as the same retry on
  the object as given is: the grasp may have acted, and the other name may hold the same object.
- A stop on a field the result may lack is refuted, as a return on one is. A return under a
  status the result never takes is reached on no execution, so its program, which only stops,
  is not weak.
- The two loops run as Python runs them: the call after the test, and the call after the error
  block, are made for each item.
- Five texts are refused by their line: `in` against a string, a test listing bytes, an ellipsis
  or a complex number, and an argument named `from`.
- A confirmation leaving out the action the task fixes is accepted.

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
  label after a call that acted. There the block books a parcel twice in as many episodes as the
  blind retry. Both
  are what its proof names: that the read sees the effect at once, and that `shows` is the
  weakest trace any effect leaves, which a label-less booking is not.
