# The theory

What the method rests on, and which code each result holds up. A citation here is a claim
that a named item of the package exists because of a numbered result, not a reading list:
[the works cited](../references.md) is the list of works, and this page is the account of
what in `valgebra_planning` each one supports, and of the tests that fail when the account is
false. valgebra's own theory, the sets, the decision procedure and its bounds, is valgebra's
page; this one starts where a set is a predicate a plan reads.

A citation is a key and a place, `(Cimatti et al. 2003, Def. 2.10)`, as
[the rule for citing a paper](writing.md#citing-a-paper) says, and the place is the number the copy on the
references page prints. A work cited only by a user page, for what an agent faces or for a
pattern this package follows, is on the references page alone: no code rests on it.

Each claim ends in a tag. **LOAD-BEARING**: code here implements the result. **DEVIATION**: the
code departs from its source on purpose, and the claim names the cost. Each is followed by an
`IMPLEMENTED-BY:` line naming the items resting on it, a module as `cells.py` or an item in it
as `planner.py::_reachable`, under `valgebra_planning/`, and a `HELD-BY:` line naming the tests
that fail when the claim is false.

`tools/docs_lint.py` holds the page and the package to each other: every citation in the
package and on this page carries its place, every item an `IMPLEMENTED-BY:` line names exists
and cites a work its claim cites, every item citing a work is named under a claim citing it,
and every `HELD-BY:` name is a test. Where the papers are at hand, which is only in the
author's clone, it also reads each place in its paper and refuses one the copy does not print.
What no check reads is an item implementing a result and citing nothing; a reader decides that
an item is a theorem, and the lint holds the citation once it is written.

## The cells

**A predicate is exactly a union of cells.** The cells are the abstract states of predicate
abstraction: the canonical monomials over the predicates a plan reads, each taken inside or
outside (Graf and Saidi 1997, §2.2), which are the minterms of the predicates in an effective
Boolean algebra (D'Antoni and Veanes 2017, §2.1). They are cut within each of the seven kinds
of JSON value, which partition the values, so every value lies in exactly one cell of its kind,
and an inclusion between two predicates' cells is an inclusion between the predicates. A part
of a cell is dropped only where valgebra proves it empty; one it neither proves empty nor finds
a member of is kept as undecided, which can cost a plan and never admits a wrong one.
**[LOAD-BEARING: cells-are-minterms]**

IMPLEMENTED-BY: cells.py

HELD-BY: test_a_predicate_is_exactly_the_union_of_its_cells, test_the_kinds_partition_json_values, test_every_value_lies_in_a_cell_the_abstraction_kept, test_every_edge_and_listed_value_lies_in_a_kept_cell, test_cells_are_decided_and_the_relations_asked_once_each

## Strong programs

**A program is strong when every path returns inside the goal.** The planner searches for a
strong solution under nondeterministic outcomes: one whose every execution ends in a goal
state, whatever outcome each action has (Cimatti et al. 2003, Def. 2.10). An outcome is a cell
of a tool's result, so the outcomes are the cells of its output set rather than a list. A
branch on a runtime type test is an observation, and the program a conditional plan, `if o
then π1 else π2` (Bertoli et al. 2006, Def. 2.8). Where no strong program exists within the
bound, a weak one is returned with each stop naming the cells it leaves uncovered: one that
reaches the goal on some execution (Cimatti et al. 2003, Def. 2.10). Which one is not the
listing's: the tools are tried in their names' order, and among the weak programs each first
call gives within an expansion budget, the one leaving the fewest cells uncovered covers the
most of what the inputs may hold, and is returned. A task may name goals in the order it prefers
them. Any set of states is a goal (Cimatti et al. 2003, Def. 2.9), so a program each path of
which returns in one of them is a strong solution for their union, as one table is strong for
one goal and weak for a part of it (Cimatti et al. 2003, Ex. 2.12): the planner searches a
strong program for the first, else a weak one whose stops a program toward the next fills, and
verifies the whole against the union. A call the search makes before a test that only one
branch needs moves into that branch, since one action can always be postponed to observing
(Bertoli et al. 2006, §6.2); the program is verified again, and the move kept only where every
link and every stop read as before. **[LOAD-BEARING: strong-programs]**

IMPLEMENTED-BY: planner.py, planner.py::_ranked, planner.py::_Search.stopping, planner.py::_test_first

HELD-BY: test_a_strong_program_branches_on_every_status_and_verifies, test_a_weak_program_names_the_status_no_tool_settles, test_a_strong_program_breaks_no_contract_whatever_the_tools_return, test_the_depth_bounds_the_calls_and_tests_on_a_path, test_the_program_is_the_same_whatever_order_the_tools_are_listed_in, test_the_weak_program_leaving_the_fewest_cells_uncovered_is_returned, test_a_preferred_goal_is_returned_where_it_can_be_and_the_next_fills_its_stops, test_where_no_program_reaches_the_preferred_goal_the_plan_returns_in_the_next, test_a_call_one_branch_alone_needs_is_made_after_the_test_that_decides_it, test_a_call_every_path_needs_stays_before_the_test

**The verifier reads a program as belief execution.** The set a reference lies in at a point of
a program is its belief there: a call's result enters as its output set, replacing every belief
held under its name, and a test narrows the tested reference to the set on its `then` branch and
to the complement on its `else` branch (Bertoli et al. 2006, Def. 3.4). A loop's item enters as
its array's item set the same way, a step of this package's own, since a conditional plan holds
no loop. A plan applicable at every call is a strong solution exactly
when every final belief lies inside the goal (Bertoli et al. 2006, Thm. 3.5), so a program whose
every link is proven and on which no path stops is strong. Each reference's set is read apart
from the others', a belief containing the real one, so a proof stands. A question asked of a
value rather than its field is asked of the value's belief, its own set met with each field of
it a test narrowed, so a refutation there names a value the program may hold; the verifier
reads neither the cells nor the search. **[LOAD-BEARING: belief-execution]**

IMPLEMENTED-BY: verify.py

HELD-BY: test_a_guarded_program_is_proven_link_by_link_and_its_stop_is_a_gap, test_the_naive_chain_is_refuted_at_the_one_binding_that_is_wrong, test_a_strong_program_branches_on_every_status_and_verifies, test_a_record_the_input_schema_relates_is_asked_whole, test_a_record_no_call_makes_is_never_a_witness, test_a_record_a_call_makes_refutes_two_fields_of_one_value, test_a_loop_item_reads_no_field_of_a_value_its_name_held, test_a_test_on_another_field_of_the_value_narrows_what_a_refutation_reads

**A field is read where the held set proves it present.** Selection requires the selected
field to be defined, the record's type a subtype of a record holding it (Castagna 2023,
§3.2.2). A reference to a field below a value the program holds is held where valgebra proves
every value held for its record an object holding the key, as a test showing the key narrows
the record to (Bertoli et al. 2006, Def. 3.4), and otherwise where it proves every value of the
root's belief carries the path: the root's set met with each field of it a test on the way
narrowed. A field valgebra proves absent from some value of that belief, or one a value of it
is proven to lack, is refuted; any other is undecided, so an undecided presence can cost a
program and never admit a wrong one.
**[LOAD-BEARING: field-selection]**

IMPLEMENTED-BY: verify.py::_Walk.resolve, verify.py::_Walk.present, verify.py::_Walk.belief

HELD-BY: test_a_key_test_on_an_item_makes_its_field_a_value_the_program_holds, test_a_field_two_objects_down_an_item_is_read_as_a_result_s_is, test_a_field_below_an_optional_one_is_held_under_both_tests, test_a_key_test_on_a_task_input_holds_its_field, test_a_field_another_keyword_requires_is_held, test_a_field_only_a_predicate_holds_present_is_undecided, test_a_field_is_held_only_where_every_member_carries_it_and_refuted_where_one_lacks_it, test_a_field_a_test_on_the_way_may_have_kept_is_never_refuted, test_every_held_value_is_read_as_a_result_is, test_a_test_on_another_field_of_the_value_narrows_what_a_refutation_reads

**The field read lies in its declared schema, not in its projection.** The type selected is
the projection of the record's type on the label (Castagna 2023, §4.2), which valgebra does not
compute. A field read lies in the subschema its record's schema declares for the key, a belief
holding every value the field takes (Bertoli et al. 2006, §3) that another keyword of the
record, as an `allOf`, may narrow further; valgebra proves each subschema the walk reads holds
the values it stands for, or the field takes any value. A proof over it stands. The cost is a value the
declared set admits that no member carries, so a witness drawn from it refutes only once valgebra
proves a value of the root's belief carries it, and a loop's item read in the array's declared
`items`, which another keyword of the array may narrow, as item-links below asks.
**[DEVIATION: declared-fields]**

IMPLEMENTED-BY: contracts.py::field_slot, verify.py::_Walk.lacking

HELD-BY: test_a_flow_rule_reads_a_field_the_verifier_reads_below_an_item, test_a_field_another_keyword_requires_is_held, test_reading_the_field_without_the_test_is_refuted_by_a_record_lacking_it, test_a_value_no_member_carries_is_never_a_witness, test_a_result_slot_the_whole_schema_refuses_is_no_slot, test_a_field_read_its_record_refuses_takes_any_value, test_a_loop_over_items_the_walk_misreads_is_asked_of_the_array

**A link on a field is asked of the value it belongs to.** The projection of a type on a label
is the least type the record holding that label under it includes (Castagna 2023, §4.2), so the
field lies inside a set on every value carrying it exactly when the value's set lies inside the
record of values carrying the field in that set, which valgebra decides without the projection.
The program's own tests prove the field carried on the path the link sits on (Bertoli et al.
2006, Def. 3.4). A link the field's declared set does not prove is asked so, of the value's
belief, every test on the way included, so a refutation names a value the program may hold
there. **[LOAD-BEARING: field-links]**

IMPLEMENTED-BY: verify.py::_Walk.inclusion, verify.py::_Walk.asked

HELD-BY: test_a_field_its_record_narrows_is_proven_by_the_record, test_a_value_no_member_carries_is_never_a_witness, test_a_record_s_refutation_a_test_on_the_way_may_have_excluded_is_undecided, test_a_witness_drawn_for_a_field_is_reported_where_a_member_carries_it, test_a_field_link_is_proven_only_where_every_member_s_field_lies_inside, test_a_test_on_another_field_of_the_value_narrows_what_a_refutation_reads

**A question on a loop's item is asked of every item of its array.** A loop reads its item in
the array's declared `items`, which another keyword of the array may narrow, as an `allOf`
beside `items`. A question that set leaves open, whether the item carries a field or lies with
its field inside a set, is asked of the value holding the array: every item of its array that
the tests in the body admit lies inside, the inclusion that types a selected field (Castagna
2023, §4.2) with the array's items for the field, read in the holder's belief where the
question sits (Bertoli et al. 2006, Def. 3.4). A refutation stands only where that belief is
the one the program holds there: the holder is unchanged since the loop began, or the item
took its name. A value refutes an item only where valgebra proves it held: the item's belief
holds values carrying it, and a value of the holder's belief holds a non-empty array of them,
a level at a time up to a value no loop reads, there proven on a value drawn or given for it
with that array in place of its own. An item set the walk does not read, or valgebra does not
prove, is every value, and every question on the item is asked so. **[LOAD-BEARING: item-links]**

IMPLEMENTED-BY: verify.py::_Walk.asked, verify.py::_Walk.built

HELD-BY: test_an_item_its_array_narrows_is_proven_by_the_narrowing, test_an_item_s_witness_is_one_an_array_of_some_result_holds, test_an_item_s_field_is_held_only_where_every_item_carries_it, test_every_held_value_is_read_as_a_result_is, test_a_binding_in_the_body_outside_its_argument_is_refuted, test_a_loop_over_items_the_walk_cannot_read_is_proven_where_each_is_inside, test_a_loop_over_items_the_walk_cannot_read_is_never_proven_where_one_is_outside, test_a_loop_over_items_the_walk_misreads_is_asked_of_the_array

**A retry is a strong cyclic solution.** A call made again in its own error block is a loop that
can always reach the goal (Daniele et al. 2000, Def. 7), and it ends under fairness: an
execution looping forever is one where the outcome reaching the goal never occurs, which a
strong cyclic solution assumes away (Cimatti et al. 2003, §2.3). A retry is accepted only
for a tool that is read-only or idempotent, since calling twice must act as calling once.
**[LOAD-BEARING: retry-is-strong-cyclic]**

IMPLEMENTED-BY: run.py::retry_assumptions, verify.py::verify

HELD-BY: test_a_retry_is_accepted_only_for_an_idempotent_tool, test_a_call_made_again_in_its_own_error_block_is_a_retry, test_when_tools_may_fail_the_plan_retries_or_stops_and_reads_back, test_a_run_against_failing_tools_breaks_no_contract

**Fairness is assumed within a bound.** Under fairness a strong cyclic solution ends after
finitely many failures, with no bound on how many (Cimatti et al. 2003, §2.3); the runner
makes a retried call at most `ATTEMPTS` times. So a retry rests on the assumption that an error is transient within that many
attempts, the plan names it, and a run whose error outlasts the attempts is charged to the
assumption rather than to the program. **[DEVIATION: fairness-within-attempts]**

IMPLEMENTED-BY: run.py::retry_assumptions

HELD-BY: test_an_error_a_retry_cannot_outlast_is_the_fairness_it_assumes

**A tool no program can use is never cut into cells.** Component synthesis prunes the places
that are not backward reachable from the goal in the graph of its components, an
over-approximation of reachability (Feng et al. 2017, §6.3). The planner prunes backward from
the goal and forward from the task's inputs alike: a tool is kept when each required argument
meets a value the agent may hold, an input or a result of a tool so found, and one of its
results meets the goal or an argument of a tool kept, valgebra deciding each meet. A tool a policy reads is kept whenever it can be called.
**[LOAD-BEARING: reachability-pruning]**

IMPLEMENTED-BY: planner.py::_reachable

HELD-BY: test_a_tool_no_held_value_can_call_and_one_reaching_nothing_are_dropped, test_a_tool_a_policy_reads_is_kept_when_it_can_be_called, test_the_cells_stay_fixed_as_distractors_are_added

## Policies

**A policy is a symbolic automaton over calls.** A transition's guard is a valgebra set over a
tool's argument records, as a symbolic automaton's guard is a predicate of an effective Boolean
algebra (D'Antoni and Veanes 2017, Def. 1). It states what a domain policy says in prose
(Yao et al. 2024, §3). Statically a guard is taken surely where valgebra proves the record
inside it, never where it proves them disjoint, and possibly otherwise, so the automaton is
stepped on sets of states; a bad state is never left. At run time the same automaton is a
monitor stepping on concrete records. A call that answers with an error may have acted or not,
so the walk reads its error block from either set of states, and the monitor holds both after
it, refusing a call that takes any of them to a bad state. A transition may read the call's
result: the monitor steps it when the result arrives. The walk splits the path at the call, as
an observation splits a belief execution tree (Bertoli et al. 2006, Def. 3.6): each case holds
the result in one cell of the guards and their complements, the belief met with the
observation (Bertoli et al. 2006, Def. 3.4), and the automaton stepped on that cell, so a guard
is taken surely or never in each; a cell valgebra proves empty is dropped, and so is a branch
no value of a case reaches. The planner writes the observation as a test where the guard reads
one field and lists its members, `if c3.confirmed in (True,):`, and steps the automaton on each
side's set as the walk does (Bertoli et al. 2006, Def. 3.6); any other guard it takes possibly at
most. **[LOAD-BEARING: policy-automaton]**

IMPLEMENTED-BY: policy.py, verify.py::_Walk.call, verify.py::_Walk.reached, planner.py::_Search.split

HELD-BY: test_the_monitor_reads_a_trace_call_by_call, test_a_bad_state_is_never_left, test_a_call_that_answered_with_an_error_holds_the_policy_in_either_state, test_a_run_refuses_the_call_an_approval_that_answered_with_an_error_would_allow, test_a_call_the_server_answers_with_an_error_leaves_the_policy_in_either_state, test_a_transition_reading_the_result_is_taken_when_the_result_arrives, test_a_walk_splits_at_a_call_a_result_guard_reads_and_refutes_the_case_it_breaks, test_a_program_moving_only_under_the_answer_its_policy_reads_is_proven, test_the_gate_reads_a_result_a_transition_waits_on, test_a_guard_the_arguments_only_overlap_leaves_the_policy_undecided, test_a_program_planned_under_the_policy_approves_first_and_is_proven, test_a_program_planned_without_the_policy_is_refuted_on_its_freight_path_alone, test_the_planner_tests_the_answer_a_result_guard_reads

**An obligation is decided over the whole program.** A property that no continuation of a
run's prefix makes true or false for good, as a notice owed after every booking, is not
monitorable after that prefix (Bauer et al. 2011, Def. 3.4), and a trace rule checked one
prefix at a time defers it to the completed trace (Elkoussy and Perez 2026, §3.4). A program's every path ends, a loop's after any number of
items, so a pending state at the end of a path is decided before the program runs; the
monitor reads the pending states when a run ends. **[LOAD-BEARING: obligations-over-programs]**

IMPLEMENTED-BY: policy.py

HELD-BY: test_a_path_ending_with_an_obligation_open_is_refuted, test_the_planner_settles_every_obligation_and_pads_no_path, test_an_obligation_a_loop_may_leave_open_is_undecided_and_one_it_settles_proven, test_a_run_ending_with_an_obligation_open_breaks, test_a_policy_holds_over_any_number_of_iterations

**A policy carries no value from one call to another, but at run time with `per`.** A
parametric property is a base property read once for each binding of its parameters, one trace
slice an instance (Roşu and Chen 2012, Def. 12). A policy without `per` is one instance for the
whole program, so a rule about the same parcel across two calls is read as a rule about any two
calls, and a program approving one parcel and booking another passes it; a rule that a call
takes a value another returned is a flow rule with `only`. A policy naming an argument in `per`
is the parametric property at run time: the monitor holds an instance for each value the
argument takes, a call carrying it an event of its value's instance alone and a call carrying
none an event of every instance (Roşu and Chen 2012, Def. 11), as Agent-C binds a file read to
the file opened before it (Kamath et al. 2025, §5.1). The walk holds no value's identity, so it
takes every transition of such a policy possibly at most and proves none of its links: the
static half is not built. **[DEVIATION: no-parametric-policy]**

IMPLEMENTED-BY: policy.py, policy.py::Monitor.stepping

HELD-BY: test_a_rule_about_one_parcel_reads_as_a_rule_about_any_two_calls, test_every_call_on_an_item_the_user_listed_is_a_flow_rule_with_only, test_a_policy_per_value_holds_an_instance_for_each_value_its_argument_takes, test_a_call_carrying_no_value_steps_every_instance_and_the_ones_to_come, test_a_run_refuses_a_grasp_of_an_object_no_confirmation_was_for

## Vacuity

**A specification may hold for a trivial reason.** A system satisfies a formula vacuously when
it satisfies it and some subformula does not affect its truth there (Kupferman and Vardi 1999,
Def. 2). A goal no value meets, a goal every value meets, a guard no argument record of its
tool meets, a policy no transition that can fire takes to a bad or pending state, and an
endorsed set that admits nothing or everything of its sink are each such a specification, and
each is a set question valgebra decides: emptiness, inclusion, a meet. One valgebra leaves
undecided is reported as undecided, never as vacuous. **[LOAD-BEARING: vacuity]**

IMPLEMENTED-BY: vacuity.py

HELD-BY: test_an_empty_goal_and_a_universal_goal_are_found, test_a_guard_no_record_meets_leaves_the_policy_unbreakable, test_an_endorsed_set_that_admits_nothing_or_everything_is_found, test_a_guard_valgebra_cannot_part_is_undecided_and_the_policy_kept_live, test_the_parcel_library_holds_no_vacuous_specification

## Mined types

**Slots linked by an observed value are one semantic type.** Type mining reads the witnesses of
calls and merges into one group the locations a value was seen at, by union-find (Guo et al.
2022, §4). A binding across two mined types is refused, which narrows the planner's choices
to what the observations show. A boolean or a null links nothing, and a mined type is an
assumption: the verifier reads none, and a plan names each binding resting on one. A witness
is a call a server answered, collected ahead of time in a sandbox or from production traffic
(Guo et al. 2022, §1), and two locations sharing a value by chance are merged spuriously (Guo et
al. 2022, §7.2); a call a planned program made shares a value with the result it was bound
from by construction, so its arguments link nothing. A task input is a value from outside the
tools: one a log shows is typed as any slot, and one it never shows fills no argument whose
type a tool's result was seen in. **[LOAD-BEARING: type-mining]**

IMPLEMENTED-BY: mining.py

HELD-BY: test_slots_holding_one_observed_value_are_one_type, test_a_boolean_or_a_null_links_nothing, test_mined_types_route_the_notice_through_the_sanitizer_as_observed, test_the_verifier_reads_no_mined_type, test_a_planned_call_s_arguments_link_nothing, test_a_task_input_no_log_shows_fills_no_argument_a_tool_s_result_was_seen_in, test_a_task_input_a_log_shows_is_typed_as_any_slot

## Results read closed

**A result read closed is its declared set narrowed by an assumption about the server.** An open
record type admits any value at a label no field names, and a closed one admits none (Castagna
2023, §3.2). A schema that writes no `additionalProperties` is open, and a server's structured
result conforms to its output schema as published (MCP 2026-07-28, Output Schema).
`additionalProperties` reads only the `properties` and `patternProperties` beside it (Attouche
et al. 2024, §3.2), so written false at the root of a result composed with `allOf` or a `$ref`
it refuses every key the composition declares and empties the result;
`unevaluatedProperties: false` refuses a key exactly where no keyword evaluated it, through a
`$ref` as beside it (Attouche et al. 2024, §2.2). A library with `"closedResults": true` reads each whole result
with it at the root, valgebra-jsonschema's `closed`. The cost: every link and binding resting
on such a result names the assumption, a result inside the declared set and outside the closed
one is blamed on it, a result the closing empties is vacuous, a nested record stays as written,
and a result the companion cannot close is refused. Where results are read as declared, `check`
notes a whole result meeting an argument or a goal only through keys its schema does not
declare, the two meets asked of it as declared and read closed. **[DEVIATION: closed-results]**

IMPLEMENTED-BY: contracts.py::read_contract, meaning.py::_undeclared_only

HELD-BY: test_an_inherited_result_read_closed_admits_the_server_s_result, test_a_whole_result_read_closed_fills_no_argument_its_declared_keys_miss, test_an_argument_record_over_a_result_read_closed_names_the_assumption, test_a_result_with_a_key_its_schema_does_not_declare_is_the_closed_reading_s, test_a_result_the_closed_reading_empties_is_found, test_a_result_the_companion_cannot_close_is_refused_naming_its_tool, test_a_result_meeting_an_argument_through_undeclared_keys_alone_is_found

## Flows and privileges

**A value from an untrusted source reaches a sink only inside its endorsed set.** A value's
provenance is the capability it carries (Debenedetti et al. 2025, §5.3), its integrity a label
(Costa et al. 2025, §4.1), and a privilege rule restricts a tool's arguments (Shi et al. 2025,
§4.1). A program's control flow is fixed before any tool runs, so the origin of every value is
known from the program, and a flow rule is decided over sets: a value from a source reaches the
sink only where valgebra proves it inside the endorsed set, the narrowing a test or a
sanitizing tool performs. **[LOAD-BEARING: integrity-flows]**

IMPLEMENTED-BY: flows.py

HELD-BY: test_a_value_from_an_untrusted_source_does_not_reach_the_sink, test_the_planner_routes_the_value_through_the_sanitizer, test_a_test_narrowing_the_value_into_the_endorsed_set_lets_it_through

**Which branch runs carries a tested value's influence.** A call made under a condition depends
on what the condition read, the dependency CaMeL's STRICT mode adds (Debenedetti et al. 2025,
§5.4). With `implicit`, a sink called under a test on a value from a source is refused, and a
call in a loop over an untrusted array alike. What follows a loop whose body may end the run
runs only where no iteration did, so it runs under every test a path ending the run there is
under: a statement after an operation that may end a run early depends on what that operation
read (Debenedetti et al. 2025, §7). **[LOAD-BEARING: implicit-flows]**

IMPLEMENTED-BY: flows.py, verify.py::_Walk.loop

HELD-BY: test_a_sink_called_under_a_test_on_an_untrusted_value_is_an_implicit_flow, test_a_call_in_a_loop_over_an_untrusted_array_is_an_implicit_flow, test_only_with_implicit_refutes_a_sink_under_a_test_on_any_other_origin, test_a_sink_after_a_loop_its_body_may_stop_runs_under_the_body_s_tests, test_an_early_return_from_a_loop_is_a_test_on_its_array

**An identifier must come from the tool that retrieves it.** A model that makes up an identifier
to pass an authentication step commits an integrity leak (Sah et al. 2026, §4.3). With `only`,
a flow rule holds every origin but its sources: a literal, a task input or a result of a tool
it does not name reaches the sink only inside the endorsed set. **[LOAD-BEARING: grounded-identifiers]**

IMPLEMENTED-BY: flows.py

HELD-BY: test_only_refutes_an_identifier_written_into_the_program, test_only_refutes_an_input_or_a_tool_it_does_not_name, test_only_lets_a_literal_inside_the_endorsed_set_through, test_the_planner_grounds_every_identifier_a_rule_names, test_a_task_naming_an_input_as_a_rule_s_source_tool_is_refused

**A privilege policy is a set of argument records.** A policy maps each tool to rules with an
effect, a condition on the arguments and a fallback (Shi et al. 2025, §4.1), applied with the
forbidding rules first, the first whose condition holds deciding, and a call no rule matches
blocked (Shi et al. 2025, §4.2, Alg. 2). The rules a tool allows are then one set (Shi et al.
2025, §4.3): each allowing rule's condition, less the conditions tried before it, joined, and
the policy an automaton the verifier, the planner and the runner read. The rules are sorted by
the priority each carries in the format (Shi et al. 2025, Fig. 9) first and put forbidding
first within one priority, where Algorithm 2 sorts by effect alone, so an allowing rule of an
earlier priority comes before a forbidding one; a rule asking the user is read as denied.
**[DEVIATION: privilege-order]**

IMPLEMENTED-BY: privilege.py

HELD-BY: test_the_first_rule_that_matches_decides, test_a_string_restriction_matches_from_the_start_and_a_literal_never, test_a_program_is_held_to_a_privilege_policy_before_it_runs

**An update that narrows a policy needs no approval.** An update is a narrowing where the calls
the new policy allows lie inside those the old one allows, and an expansion otherwise, which
needs approval (Shi et al. 2025, §4.3, §5). Progent asks an SMT solver; here a drawn call the new
policy allows and the old refuses shows an expansion, valgebra decides the inclusion where no
such call is found, and an inclusion it leaves undecided is reported as undecided.
**[LOAD-BEARING: privilege-updates]**

IMPLEMENTED-BY: privilege.py::updates

HELD-BY: test_adding_tools_is_an_expansion_shown_by_a_call, test_binding_an_argument_narrows_and_a_forbidding_rule_does_too

**The proxy holds every call, and reads provenance from values.** In front of a server, a call is
checked as the fail-closed runner checks it, and a check that breaks answers the call with a
tool error naming it, as a denied call is answered with its fallback message in Progent's proxy
mode (Shi et al. 2025, §4.1, §7). The proxy sees values, not a program's references: an
argument equal to a value an untrusted tool returned is taken as coming from it, and with
`only` an argument equal to no value its sources returned is refused, the strict grounding gate
(Sah et al. 2026, §4.8). A value the agent transforms equals none it was returned, so a flow
rule here holds against no attacker who adapts, where the contract and policy checks hold
against any. Every client of one proxy calls through its one gate, so policy state and
provenance are the proxy's, not a client's. **[DEVIATION: provenance-by-value]**

IMPLEMENTED-BY: proxy.py

HELD-BY: test_a_call_inside_every_check_passes_through, test_an_argument_record_outside_the_input_set_never_reaches_the_server, test_a_value_an_untrusted_tool_returned_does_not_reach_the_sink, test_an_identifier_no_source_returned_never_reaches_the_server, test_every_client_of_one_proxy_over_http_calls_through_its_one_gate, test_a_proxy_per_client_holds_only_the_calls_made_through_it

## Effects

**A reading an action may change is read again after it.** Whether an action's effect holds is
read in the world, independently of what the action answered (Mansoor et al. 2026, §4.1). This
package reads the same of anything else an action may have changed: a distance read before a
walk is no reading of the distance after it. A task names the arguments it reads fresh, and a call whose annotations
do not say it is read-only may act; the verifier refutes a binding of a value held before the
last such call before the one it fills, and the planner gives such an argument no such value,
and lets a read-only call made before an action be made again after it. Which calls may act is
read off their annotations, so a link proven over them lists that assumption unless every
server is trusted. **[LOAD-BEARING: fresh-reads]**

IMPLEMENTED-BY: verify.py::_Walk.read_fresh, planner.py::_Search.after

HELD-BY: test_a_value_read_before_a_call_that_may_act_is_no_fresh_reading_after_it, test_the_planner_reads_again_after_a_call_that_may_act

## The critic and code mode

**A model proposes and a sound critic checks.** The soundness of a plan a language model writes
comes from the critics that check it, not from the model (Kambhampati et al. 2024, §3.1). The
critic reads a program from Python, verifies every link of it, and answers with each link not
proven, the value refuting it where one is found, and what the author can change.
**[LOAD-BEARING: sound-critic]**

IMPLEMENTED-BY: critic.py

HELD-BY: test_the_naive_chain_gets_its_counterexamples_and_the_guards_to_add, test_a_policy_the_program_breaks_is_refuted_with_its_remedy, test_an_error_nothing_handles_names_the_block_to_add, test_every_planned_program_reads_back_as_itself, test_the_critique_of_an_untested_optional_field_names_its_key_test

**The critique is the next prompt.** The critiques are pooled and handed back to the model as its
next prompt (Kambhampati et al. 2024, §3.2), here until a program is accepted or the rounds run
out. The model is any callable from the last critique to a program, so no model is part of this
package. **[LOAD-BEARING: backprompt]**

IMPLEMENTED-BY: codemode.py

HELD-BY: test_a_model_s_program_is_repaired_by_the_critique_it_is_handed, test_the_rounds_run_out_on_a_program_never_mended, test_a_program_the_reader_refuses_is_handed_back_as_the_reason, test_a_model_handed_the_key_test_writes_a_program_the_critic_accepts

**A weak program is accepted where its task tolerates each stop.** A weak solution reaches the
goal on some execution (Cimatti et al. 2003, Def. 2.10). A task names, for a tool or an input,
the set its whole value may lie in where a path stops on it, as an object not found; a program
every link of which is proven, some path of which returns, and every stop of which valgebra
proves inside the set its task names there is weak in that sense, every other end being one
the task accepts. `repair` ends on one where asked, `accept="weak"`, so a task no program
covers on every path is met by one stopping only where it may. A program stopping on every
path returns on none, and is never accepted so. **[LOAD-BEARING: weak-acceptance]**

IMPLEMENTED-BY: verify.py::Verification.weak

HELD-BY: test_a_weak_program_stops_only_where_its_task_tolerates, test_repair_accepts_a_weak_program_where_asked_and_stays_strict_by_default

**A return in a loop's body ends the program at the first item reaching it.** A strong
solution's execution structure is acyclic, and all its terminal states are in the goal (Cimatti
et al. 2003, Def. 2.10). Over a finite array each iteration takes the next item, so a loop whose
body may return keeps the structure acyclic, and its terminal states are the returns in the body
and after the loop and the stops, each return in the goal where its link is proven; no fairness
is assumed, where a retry's loop rests on it. The block after the loop runs only where no
iteration ended the run, and a policy's obligation at a return in the body is read in the
states the loop's fixpoint holds, any number of iterations coming before it.
**[LOAD-BEARING: first-success]**

IMPLEMENTED-BY: critic.py::_ending

HELD-BY: test_a_return_in_a_body_ends_the_program_at_the_first_item_reaching_it, test_an_obligation_at_a_return_in_a_body_is_read_after_any_number_of_iterations, test_an_early_return_from_a_loop_is_a_test_on_its_array
