# The theory

What the method rests on, and which code each result holds up. A citation here is a claim
that a named item of the package exists because of a numbered result, not a reading list:
[the works cited](../references.md) is the list of works, and this page is the account of
what in `valgebra_planning` each one supports, and of the tests that fail when the account is
false. valgebra's own theory, the sets, the decision procedure and its bounds, is valgebra's
page; this one starts where a set is a predicate a plan reads.

A citation is a key and a place, `(Cimatti et al. 2003, Def. 2.11)`, as
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
state, whatever outcome each action has (Cimatti et al. 2003, Def. 2.11). An outcome is a cell
of a tool's result, so the outcomes are the cells of its output set rather than a list. A
branch on a runtime type test is an observation, and the program a conditional plan, `if o
then π1 else π2` (Bertoli et al. 2006, Def. 2.8). Where no strong program exists within the
bound, a weak one is returned with each stop naming the cells it leaves uncovered.
**[LOAD-BEARING: strong-programs]**

IMPLEMENTED-BY: planner.py

HELD-BY: test_a_strong_program_branches_on_every_status_and_verifies, test_a_weak_program_names_the_status_no_tool_settles, test_a_strong_program_breaks_no_contract_whatever_the_tools_return, test_the_depth_bounds_the_calls_and_tests_on_a_path

**The verifier reads a program as belief execution.** The set a reference lies in at a point of
a program is its belief there: a call's result enters as its output set, and a test narrows the
tested reference to the set on its `then` branch and to the complement on its `else` branch
(Bertoli et al. 2006, Def. 3.4). A plan applicable at every call is a strong solution exactly
when every final belief lies inside the goal (Bertoli et al. 2006, Thm. 3.5), so a program whose
every link is proven and on which no path stops is strong. Each reference's set is read apart
from the others', a belief containing the real one, so a proof stands; the verifier reads
neither the cells nor the search. **[LOAD-BEARING: belief-execution]**

IMPLEMENTED-BY: verify.py

HELD-BY: test_a_guarded_program_is_proven_link_by_link_and_its_stop_is_a_gap, test_the_naive_chain_is_refuted_at_the_one_binding_that_is_wrong, test_a_strong_program_branches_on_every_status_and_verifies, test_a_record_the_input_schema_relates_is_asked_whole

**A retry is a strong cyclic solution.** A call made again in its own error block is a loop that
can always reach the goal (Daniele et al. 2000, Def. 7), and it ends under fairness: an
execution looping forever is one where the outcome reaching the goal never occurs, which a
strong cyclic solution assumes away (Cimatti et al. 2003, Def. 2.10). A retry is accepted only
for a tool that is read-only or idempotent, since calling twice must act as calling once.
**[LOAD-BEARING: retry-is-strong-cyclic]**

IMPLEMENTED-BY: run.py::retry_assumptions, verify.py::verify

HELD-BY: test_a_retry_is_accepted_only_for_an_idempotent_tool, test_a_call_made_again_in_its_own_error_block_is_a_retry, test_when_tools_may_fail_the_plan_retries_or_stops_and_reads_back, test_a_run_against_failing_tools_breaks_no_contract

**Fairness is assumed within a bound.** Under fairness a strong cyclic solution ends after
finitely many failures, with no bound on how many (Cimatti et al. 2003, Def. 2.10); the runner
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
monitor stepping on concrete records. **[LOAD-BEARING: policy-automaton]**

IMPLEMENTED-BY: policy.py

HELD-BY: test_the_monitor_reads_a_trace_call_by_call, test_a_bad_state_is_never_left, test_a_guard_the_arguments_only_overlap_leaves_the_policy_undecided, test_a_program_planned_under_the_policy_approves_first_and_is_proven, test_a_program_planned_without_the_policy_is_refuted_on_its_freight_path_alone

**An obligation is decided over the whole program.** A property that no continuation of a
run's prefix makes true or false for good, as a notice owed after every booking, is not
monitorable after that prefix (Bauer et al. 2011, Def. 3.4), and a trace rule checked one
prefix at a time defers it to the completed trace (Elkoussy and Perez 2026, §3.4). A program's every path ends, a loop's after any number of
items, so a pending state at the end of a path is decided before the program runs; the
monitor reads the pending states when a run ends. **[LOAD-BEARING: obligations-over-programs]**

IMPLEMENTED-BY: policy.py

HELD-BY: test_a_path_ending_with_an_obligation_open_is_refuted, test_the_planner_settles_every_obligation_and_pads_no_path, test_an_obligation_a_loop_may_leave_open_is_undecided_and_one_it_settles_proven, test_a_run_ending_with_an_obligation_open_breaks, test_a_policy_holds_over_any_number_of_iterations

**A policy carries no value from one call to another.** A parametric property is a base property
read once for each binding of its parameters, one trace slice an instance (Roşu and Chen 2012,
Def. 12); a policy here is one instance for the whole program. So a rule about the same parcel
across two calls is read as a rule about any two calls, and a program approving one parcel and
booking another passes it. A rule that a call takes a value another returned is a flow rule
with `only`. **[DEVIATION: no-parametric-policy]**

IMPLEMENTED-BY: policy.py

HELD-BY: test_a_rule_about_one_parcel_reads_as_a_rule_about_any_two_calls, test_every_call_on_an_item_the_user_listed_is_a_flow_rule_with_only

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
assumption: the verifier reads none, and a plan names each binding resting on one.
**[LOAD-BEARING: type-mining]**

IMPLEMENTED-BY: mining.py

HELD-BY: test_slots_holding_one_observed_value_are_one_type, test_a_boolean_or_a_null_links_nothing, test_mined_types_route_the_notice_through_the_sanitizer_as_observed, test_the_verifier_reads_no_mined_type

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
call in a loop over an untrusted array alike. **[LOAD-BEARING: implicit-flows]**

IMPLEMENTED-BY: flows.py

HELD-BY: test_a_sink_called_under_a_test_on_an_untrusted_value_is_an_implicit_flow, test_a_call_in_a_loop_over_an_untrusted_array_is_an_implicit_flow, test_only_with_implicit_refutes_a_sink_under_a_test_on_any_other_origin

**An identifier must come from the tool that retrieves it.** A model that makes up an identifier
to pass an authentication step commits an integrity leak (Sah et al. 2026, §4.3). With `only`,
a flow rule holds every origin but its sources: a literal, a task input or a result of a tool
it does not name reaches the sink only inside the endorsed set. **[LOAD-BEARING: grounded-identifiers]**

IMPLEMENTED-BY: flows.py

HELD-BY: test_only_refutes_an_identifier_written_into_the_program, test_only_refutes_an_input_or_a_tool_it_does_not_name, test_only_lets_a_literal_inside_the_endorsed_set_through, test_the_planner_grounds_every_identifier_a_rule_names

**A privilege policy is a set of argument records.** A policy maps each tool to rules with an
effect, a condition on the arguments and a fallback (Shi et al. 2025, §4.1), applied with the
forbidding rules first, the first whose condition holds deciding, and a call no rule matches
blocked (Shi et al. 2025, §4.2, Alg. 2). The rules a tool allows are then one set: each
allowing rule's condition, less the conditions tried before it, joined, and the policy an
automaton the verifier, the planner and the runner read. The rules are sorted by the format's
priority first and put forbidding first within one priority, where Algorithm 2 sorts by effect
alone, so an allowing rule of an earlier priority comes before a forbidding one; a rule asking
the user is read as denied. **[DEVIATION: privilege-order]**

IMPLEMENTED-BY: privilege.py

HELD-BY: test_the_first_rule_that_matches_decides, test_a_string_restriction_matches_from_the_start_and_a_literal_never, test_a_program_is_held_to_a_privilege_policy_before_it_runs

**An update that narrows a policy needs no approval.** An update is a narrowing where the calls
the new policy allows lie inside those the old one allows, and an expansion otherwise, which
needs approval (Shi et al. 2025, §4.3). Progent asks an SMT solver; here a drawn call the new
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
against any. **[DEVIATION: provenance-by-value]**

IMPLEMENTED-BY: proxy.py

HELD-BY: test_a_call_inside_every_check_passes_through, test_an_argument_record_outside_the_input_set_never_reaches_the_server, test_a_value_an_untrusted_tool_returned_does_not_reach_the_sink, test_an_identifier_no_source_returned_never_reaches_the_server

## The critic and code mode

**A model proposes and a sound critic checks.** The soundness of a plan a language model writes
comes from the critics that check it, not from the model (Kambhampati et al. 2024, §3.1). The
critic reads a program from Python, verifies every link of it, and answers with each link not
proven, the value refuting it where one is found, and what the author can change.
**[LOAD-BEARING: sound-critic]**

IMPLEMENTED-BY: critic.py

HELD-BY: test_the_naive_chain_gets_its_counterexamples_and_the_guards_to_add, test_a_policy_the_program_breaks_is_refuted_with_its_remedy, test_an_error_nothing_handles_names_the_block_to_add, test_every_planned_program_reads_back_as_itself

**The critique is the next prompt.** The critiques are pooled and handed back to the model as its
next prompt (Kambhampati et al. 2024, §3.2), here until a program is accepted or the rounds run
out. The model is any callable from the last critique to a program, so no model is part of this
package. **[LOAD-BEARING: backprompt]**

IMPLEMENTED-BY: codemode.py

HELD-BY: test_a_model_s_program_is_repaired_by_the_critique_it_is_handed, test_the_rounds_run_out_on_a_program_never_mended, test_a_program_the_reader_refuses_is_handed_back_as_the_reason
