---
description: What verifying and planning cost, how to count it, how to spend less, how the speed is made, and what is measured and not taken.
---

# Performance

What a verification, a plan or a critique spends is the questions it asks valgebra, so a call is
made cheaper by asking fewer, cheaper or already answered ones. A count moves with the machine,
the interpreter and valgebra's release, so none is written here: one command prints each, and the
release notes give a version's beside the one before.

## Counting a call

```bash
uv run --no-sync python tools/perf.py
uv run --no-sync python tools/perf.py verify:parcels:1 mistakes:parcels:1
uv run --no-sync python tools/perf.py --first
uv run --no-sync python tools/perf.py --against /tmp/last1
```

`tools/perf.py` counts the instructions of one call under cachegrind, so it needs valgrind, and a
loaded machine reads the same count as an idle one. A case names a function, an example and one
of its tasks: `verify:parcels:1` verifies the program the planner finds for the second task of
`examples/parcels`. `--against` counts the same case in an environment holding a release, made as
[the releasing page](https://github.com/ppigazzini/valgebra-planning/blob/main/docs/dev/releasing.md#before-the-bump)
makes one, and each count is then read against the first environment named.

Three readings tell different things:

- **A call among many** on one library, the default: what an agent's loop or a critic in its
  repair rounds spends on each program.
- **The first call** on contracts just read, with `--first`: what a command spends on the first
  program it reads.
- **Programs with mistakes**, the case `mistakes`: the planned program with a line's fields and a
  line's test edited, most of them refuted. A proof asks one inclusion a link, and a refutation
  looks for a value besides, so a library whose programs verify cheaply may refute dearly.

A count reads no cache and no branch. Where a time is the claim, it is read on a quiet machine,
in environments taking turns, as the releasing page says.

## What a call spends

valgebra decides an inclusion in one of two ways, and
[its own page](https://ppigazzini.github.io/valgebra/guides/performance/#what-a-relation-between-two-validators-costs)
gives both costs: by a rule, read off the two schemas' shapes, or by the set representation,
which builds automata and costs orders of magnitude more. Membership of one value walks the value
and enters neither. The questions of this package fall so:

| The question | Asked of valgebra as | What decides it |
|---|---|---|
| a value inside an argument | an inclusion of two schemas | a rule, mostly |
| a value inside a target after a test left it out of a set | an inclusion of a difference | the sets |
| whether a path past its tests is reached | a meet of a whole record and each tested field, not empty | the sets |
| whether a drawn value is a witness | the same meet, with the value at its field | the sets |
| whether two sets share a value | a meet, not empty | a rule or the sets |
| a listed constant inside an argument | membership | one value |

So the recipe is the same everywhere below: a concrete value before a set, a rule before the
sets, and an answer once.

## Spending less

- **Read a library once and hold it.** A contract keeps what is found in it, the slots of its
  declared fields, the values its schema lists, some members of each slot and the values a seed
  draws first, and a second reading of the same listing finds each again. A process that answers
  many programs calls `read_library` or `read_contract` at its start.
- **Verify a program in hand rather than plan one.** A plan cuts the library's predicates into
  cells and searches, then verifies what it finds. Where a model wrote a program, `critique` or
  `verify` reads it, and a repair round costs one more of the same.
- **Pass only the rules a program is held to.** A policy makes the walk build each call's argument
  record, a flow rule or a task's `reads` makes it keep what each call was given, and
  postconditions make it read what each result shows. With none, it does none of it.
- **List a field's values where they are few.** The members of an `enum` or a `const` and the ends
  of a range are read off the schema, and a value for a `pattern` is drawn from it and checked
  against its set at the first call that needs one.
- **Give a task its `depth`.** The search deepens until a program is strong, so a task no strong
  program meets is searched to its bound, and then once more for a weak one.

## How the speed is made

Each technique below changes how an answer is reached and never an answer valgebra gives. Where
valgebra leaves a question undecided, a value found may answer it, since a value is a proof. A
test holds the cheap reading to the one it stands beside, or counts the questions it leaves
unasked, and is named with the code that owns it.

### Sets and contracts

- **A question is asked once, by its sets.** `meet`, `inside` and `sides` in
  `valgebra_planning/contracts.py` keep their answers by the validators they were asked of, up to
  a bound: valgebra compares validators by value and equal ones denote one set, so the search,
  the cells, the verifier and the notes of `check` share an answer, and so do two readings of one
  listing (`test_a_test_s_sides_and_an_inclusion_are_those_asked_afresh`,
  `test_a_library_planned_over_again_asks_no_pair_of_sets_again`,
  `test_a_program_verified_again_asks_no_difference_again`).
- **A slot keeps what is read off it.** The slot of a declared field, the values a schema lists,
  the keys it declares, some of its members and the first values a seed draws are found once
  (`Slot.below`, `listed`, `declared`, `members`, `stream`). An undeclared key keeps nothing, so
  the schema bounds what a program can make a slot hold
  (`test_a_declared_field_s_slot_is_read_once_and_an_undeclared_one_is_not_kept`,
  `test_a_slot_s_kept_draws_are_those_a_seeded_stream_draws`).
- **A constant's set is kept by its text.** `listed` keeps the set of a program's constants by
  their JSON text, in which `1`, `1.0` and `true` are three texts, where Python finds one equal to
  another (`test_a_listed_set_is_the_enum_s_whether_its_text_was_read_or_not`).

### Verifying a program

- **One value shows a path reached.** A return past tests on several fields is reached where the
  meet of the whole result and each tested field holds a value. `_Walk.holds` in
  `valgebra_planning/verify.py` builds a value of the contract's fields, `Contract.shown`, moves
  each tested field into its set, and asks valgebra one membership in the result's own set; the
  meet is asked where no value built lies inside
  (`test_the_returns_reached_are_the_ones_the_whole_belief_shows`,
  `test_a_return_its_fields_show_asks_no_whole_belief`).
- **One value shows a witness.** A value drawn for a field refutes its link only where the program
  may hold it there, the same question with the value given. A value the field's own set refuses
  needs none (`test_a_member_is_shown_by_a_value_found_and_refused_by_its_field`).
- **A member shows a set holds one.** A stop is no gap where a test on the way left a value the
  program holds no member, and a known member the test left in its set shows the set holds one,
  so valgebra is asked the emptiness only where none lies there, and the answer is kept by the
  set (`_proven_empty` in `valgebra_planning/verify.py`,
  `test_a_stop_a_member_reaches_asks_no_emptiness`).
- **The first draws are kept.** Every verification of a contract draws the same first values from
  its seed, so a slot keeps them with the state they leave the stream in, and a walk's later draws
  go on from that state (`test_a_witness_is_the_one_a_walk_s_stream_draws`). A link's witness is
  copied, since a caller may write one.
- **What a rule reads is built where it reads it.** The record of a call's arguments, what each
  call was given, the state an error's block starts from, the sets a retry is told apart by and the
  stream of draws are each built by the rule or the witness asking
  (`test_a_program_no_rule_reads_builds_no_record_and_draws_nothing`).
- **The walk's records are named tuples.** A held value, a read and a path are made at every call
  and every branch, and a named tuple is built and replaced in fewer steps than a frozen dataclass;
  a reference the walk looks up is made once (`_ref`).

### Planning

- **A candidate refutes a relation first.** The cells ask whether one predicate lies in another,
  or apart from it, for every pair. `_Relations` in `valgebra_planning/cells.py` reads each
  predicate's members of the candidate pool once, a bit each, and a candidate in one and not the
  other, or in both, settles the pair; valgebra is asked the rest
  (`test_a_relation_read_off_the_candidates_is_the_one_valgebra_answers`).
- **What a search reads of a tool is read once.** The cells an argument takes and a result gives
  are read off the abstraction at the first call tried, and the arguments a task fixes are set
  aside once (`taken`, `given` in `valgebra_planning/planner.py`,
  `test_a_constant_the_task_fixes_fills_no_other_argument`). A search under no withdrawing rule,
  or with no failed call, reads neither.

### Reading and writing programs

- **A program's text is shared where it runs on.** A statement after an `if` is read once for both
  of its sides, and the paths are counted, only where some statement runs on into one or the text
  is longer than the paths allowed, since without sharing each path ends in text of its own
  (`_block` and `_statements` in `valgebra_planning/critic.py`,
  `test_what_follows_each_side_that_runs_on_is_read_once`,
  `test_a_reading_leaves_nothing_shared_to_the_next`).
- **A name is read once.** Whether Python spells a name, and how a program writes it as a member,
  are kept by the name, and an ASCII name is in NFKC as it stands (`spelled`, `_member` and
  `alias` in `valgebra_planning/program.py`).
- **A program naming every tool as Python spells it is kept as it is**
  (`test_a_program_of_tools_each_called_by_its_name_is_kept_as_it_is`), and its stubs ask no
  two tools whether one alias spells both: two collide only where an alias differs from its
  tool's name (`stubs` in `valgebra_planning/codemode.py`,
  `test_a_library_one_alias_spells_twice_is_refused_by_stubs_and_by_plan`).
- **A place is compiled once.** The notes `check` makes read a place below a result in the set its
  slot compiled (`_Read.set_of` in `valgebra_planning/meaning.py`,
  `test_a_place_s_set_is_the_one_its_slot_compiled`).

## Measured and not taken

Each of these is read beside the code above by `tools/perf.py`, or by an answer it would change,
and stays out.

- **A field-by-field proof that a return is reached.** The record of the fields' sets inside the
  result, each field not empty, proves what one value shows. The emptiness of a `pattern` builds
  its automaton, so the first call costs more, where the value costs a draw.
- **`is_empty` for a meet, in place of `relation_to(nothing)`.** It reads no lower on a repeated
  call, whose meets are kept, and a little lower on a first, and it says only whether a set is
  proven empty, which leaves a set valgebra proves inhabited and one it does not decide alike.
- **A reference's hash kept with it.** `verify` reads a little lower and `plan` a little higher,
  for a field on a class whose fields are what a program writes.
- **`Verification.returns` built when it is read.** It would hold validators until then, and a
  validator does not pickle, so a verification would not.
- **A random stream for each predicate.** A pool's draws come off one stream in the order of the
  predicates' names, so another stream draws other candidates, and other cells and witnesses.
- **A key every record holds left out of a shrink's tries.** A refuted record lacking a field is
  shrunk a key at a time, and a key valgebra proves every record of its set holds is never
  dropped; leaving it untried counts 0.4% fewer instructions on sixty programs with mistakes,
  where the membership each kept try asks is the cost.
- **The reader's names walked off one stack.** What a program binds, calls and reads is read a
  block at a time, a call each; one loop over a stack of blocks counts no fewer.
- **The objects holding a key, kept by the key.** A key test builds them at each walk; kept, the
  verify cases count no fewer.
- **The listed values of every field, found at the first reading.** Each is read off its schema by
  compiling it, and most fields are never asked.

## Limits

- **A first call costs more than one repeated**, by the slots, members and draws it finds, and by
  the sets valgebra builds, an automaton for each `pattern` among them. A process that answers
  once pays it once.
- **The kept answers are bounded**: a library with more pairs of sets than `_PAIRS` in
  `valgebra_planning/contracts.py` asks some of them again, and reads no different answer.
- **The cells are minterms**, so predicates that overlap one another cut more of them, and no kept
  answer changes how many ([the method](method.md#the-cells)).
- **A run's and a proxy's calls are not counted here**: what one checks is a membership of its
  arguments, of each rule's guards and of its result.
