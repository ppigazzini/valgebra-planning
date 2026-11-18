# Writing rules

These govern every text the tree carries, the README, `AGENTS.md`, the pages under `docs/`,
the comments and docstrings in the sources, and every commit message. One standard: state
what is true, with the evidence, in as few words as carry it.

The working notes, the paper shelf and any analysis asked for stay out of the tree. **Do not
converge the two surfaces.** A page carries no campaign history, a note is never the only
place a shipped fact lives, and nothing tracked names where the notes are: the reference
dangles for every reader but its author. `tools/docs_lint.py` sweeps every tracked file and
every commit message for one.

## The rules

**State the page contract first.** The shortest accurate sentence saying what the page
covers. A reader on the wrong page learns it in one line.

**Say what a program means as inclusions.** A program, a link or a rule appears with the
sets it rests on: which value lies inside which argument, which record inside which input
set, which path reaches which state of a policy. "The planner handles errors" is not a
meaning; "a call is retried only where the tool's annotations say it is read-only or
idempotent" is.

**Separate a verified fact from a decision made here.** "valgebra proves the record inside
the input set" is checkable against valgebra. "A retry trusts the annotations" is a choice
this package makes. Blur them and nobody can tell what they are allowed to change.

**Describe a gap as a gap, never as a design.** Say undecided, say what it costs, cells kept
or a program refused, and say where it belongs: valgebra, the companion, or here. Framing a
hole as a decision is what keeps it alive.

**Never rationalize a defect into a convention.** When you find yourself writing the sentence
that makes the odd thing sound intended, stop and check whether it is.

**Name the owner and the invariant, not just the mechanism.** "The planner drops a call whose
result no later step reads" is accurate; what a reader needs is that a call can act on the
world, so a call a policy reads is kept even where nothing reads its result. Write the
sentence a reader needs before they delete your line.

**Verify the claim against the tree; run it when it is behavioral.** `git grep -n` for the
symbol, the study's `--check` for a count, `uv run --no-sync pytest -k` for a behavior.

**A run is not a proof, and a proof is not a run.**

| the claim is about | check it against |
|---|---|
| what a program does on some values | a run, `valgebra_planning/run.py`, a test |
| whether a program holds for every value its contracts admit | the verifier's links, `valgebra_planning/verify.py` |
| what a set holds, or whether two sets meet | valgebra's decision, never a sample |

A run that breaks no contract on 40 draws shows 40 values. A sentence claiming more rests on
the verifier, and says so.

**Every published number is a study row.** A count on a page is a row of a file under
`docs/studies/` that a command's `--check` reproduces, and the command stands beside it.
Never pin a number no study holds, a test count, a line count, a timing: a stale number tells
a reader to hold the wrong invariant.

**Never pin a list a gate owns.** The commands of the gate are `AGENTS.md`'s, the steps of a
CI job the workflow's, the commit scopes `SCOPES` in `tools/docs_lint.py`. Name the file that
owns the list beside it.

**State the limit.** A page that omits its boundary invites over-trust. Say what the thing
does not cover: the search bounds, the side effects no policy reads, a field the output
schema does not guarantee.

**Show the command.** A claim ships with what produced it, so the next reader re-runs it
instead of trusting you.

**No history.** "It used to be X", "this was fixed in Y", "we tried Z" is out of date the day
after it is written. A text states what is, not what was or how it came to be known; where a
past defect is load-bearing, state the property that prevents it. `HISTORY` in
`tools/docs_lint.py` holds a narrow list of those words over every page, every comment and
docstring, and every commit message. `CHANGELOG.md` is a record of releases and is exempt,
as is this page, which spells the words to forbid them.

**No meta.** Do not describe the text, the process, or who did what. No "this section
covers", no "as mentioned above", no agent, tool or author names.

**No marketing.** No "powerful", "robust", "seamless", "comprehensive", "state of the art". A
property either has a number or is not claimed.

**American English.** Every text is American English: pages, comments, docstrings, messages,
names and file names. `-ize` and `-yze` (`serialize`, `analyze`), `-or` (`behavior`), `-er`
(`center`), `-ense` (`license`, `defense`), one `l` before a suffix (`modeled`, `labeled`),
`toward` and `afterward`, `while` and `among`, and a date month first (`Oct 29, 2026`) or as
ISO 8601 (`2026-10-29`). A quotation, a published title and a third party's name keep the
spelling they were published with. `tools/docs_lint.py` refuses a British word in every
tracked file, its path and every commit message.

**One example beats three paragraphs**, and **pair every prohibition with an alternative.**
"Do not pin the count" leaves a reader stuck; "do not pin the count, name the study file that
holds it" does not.

**Cut anything that does not help implement or verify.** Every sentence carries a fact, a
number, or a consequence. Add no section that exists to look complete: a summary restating
the section above it, a recap of what a command prints, a next-steps list nobody asked for.

## Claims

A claim needs evidence in the same sentence or the next one.

- **Measured**: give the number and the command that prints it, with the study file its
  `--check` holds.
- **Inferred**: say so first, not in a trailing caveat. "The mechanism is X" and "X is
  consistent with the data" are different claims.
- **Untested**: say the gap and what would close it. A gap described as a gap is
  information; a gap left implicit is a defect.

Never write a comparative result from one seed without the words "seed N, one sample" in the
same sentence as the number.

## Numbers

Every published number needs a committed command. If a number cannot be grepped out of a
command's output, it is not a result.

Quote a rate with its denominator: 14 of 14 links proven, not "100 %".

A study records counts; a ratio is computed where it is printed, to one decimal place of a
percentage. The counts are the record, and they compare exactly.

## Hot and cold

A page is **hot** when it describes code that moves, **cold** when what it describes barely
does. **Change hot code, fix its page in the same commit.** A page is wrong from the moment
the code lands, and nobody knows which claim broke better than the person who broke it.

| page | temperature |
|---|---|
| [`README.md`](../../README.md) | warm: its module table moves with a module |
| [`AGENTS.md`](../../AGENTS.md) | warm: a gate command added there is a workflow step |
| [the site's home](../index.md) | warm: its example runs in the suite, and its index moves with a page added |
| [command-line.md](../command-line.md) | hot: a command, an option or a file of a library is an edit there |
| [method.md](../method.md) | hot: a rule, a cut or a search change is an edit there |
| [api.md](../api.md) | hot: a public name added, removed or retyped is an edit there |
| [code-mode.md](../code-mode.md) | hot: its example runs in the suite, and a change to the critic, the runner or the proxy is an edit there |
| [results.md](../results.md) | hot: every count moves with its study |
| [recipes.md](../recipes.md) | hot: its examples run in the suite and print what the page shows, so a change to a critique, a plan, a run or a finding is an edit there |
| [changelog.md](../changelog.md) | warm: it serves `CHANGELOG.md`, and its versioning rules move with a release's |
| `mkdocs.yml` | warm: a page added is a line of its `nav` and of its `llms.txt` sections, the second the page's `description:` |
| this page | cold |
| [references.md](../references.md) | cold: a citation added in the code adds its work there |

Cold does not mean unowned. It means the claim outlives a release, so when it is wrong it has
usually been wrong for a long time.

## Markdown that GitHub renders

The renderer is strict and the failures are silent. `tools/docs_lint.py` catches these.

- Never split a token across the math boundary: write the whole symbol in one span, or plain
  text.
- No bare sub- or superscript spans.
- No spacing macros in inline `$...$`. They render literally.
- Keep a numeric range in one span. `$\sim10^4\text{ to }10^5$`, or plain text.
- Do not glue an opening `$` to a preceding `~` or `-`.
- When wrapping prose, never start a continuation line with `+ `, `- ` or `* `.
- No images, anywhere. A committed plot stops tracking the code that produced it and cannot
  be grepped against a study row.

Link with relative paths, except in `README.md`. The README is also the package's page on
PyPI, which resolves no relative link, so it links into this tree by the absolute address of
`main`, `https://github.com/ppigazzini/valgebra-planning/blob/main/` and the path. The lint
fails on a dead link of either kind and on a relative link in the README. The site build checks
the published pages again: `mkdocs.yml` raises each of the builder's link and nav checks to a
warning, which `--strict` fails on. It reports a link from a published page into `docs/dev/`
only as information, so that rule is the lint's.

## Code comments

**The default is no comment.** Code states what it does; a comment is for what the code
cannot state.

Comment when the reason is not recoverable from the line:

- a value that looks arbitrary and is not
- an ordering that matters
- a library default being overridden
- an invariant a later edit would silently break
- a cheaper-looking alternative that is wrong

**Imperative mood, leading with a verb.** "Keep the cell", not "This keeps the cell" or
"Keeps the cell". The same for docstring summaries, which ruff enforces.

**Name the invariant, and what breaks without it:**

```python
# Keep the cell. Dropping one valgebra cannot prove empty would let a program skip values a
# tool may return, and every strong program found over the cells would be unsound.
```

That names the action, what breaks without it, and how far the damage reaches.
`# keep it to be safe` carries none of it.

**No history, no meta.** Not "was a list", not "added for the policy work", not "the
following block does". A comment describes the code as it is, to someone who has never seen
it. Do not restate the signature, narrate the obvious, or leave commented-out code.

Docstrings follow the numpy convention. A module docstring states what the module is
responsible for and what it must not do. A function docstring states the contract, not the
implementation.

## Citing a paper

**Cite a work by its key**: the surnames of its one or two authors, or its first author's
with `et al.`, then the year, as in `Graf and Saidi 1997` and `Cimatti et al. 2003`. The
system a work names may lead the key, as in `(SyPet, Feng et al. 2017)`. The key is the bold
lead of an entry on [references.md](../references.md), the one place a work's authors,
title, venue and identifier are written.

**In the package, a citation names the result by its place.** After the key comes the place
in the work: `(Cimatti et al. 2003, Def. 2.11)`, `(Shi et al. 2025, §4.2, Alg. 2)`. A place is
`§` and a section number, or `Def.`, `Thm.`, `Lem.`, `Prop.`, `Cor.`, `Ex.`, `Alg.` or `Fig.`
and a number, several separated by commas, and the number is the one the copy the references
page lists prints: a conference version and its journal version can number a result apart, so
the entry names the copy read. A key alone leaves a reader to search a whole work for the line
a step rests on. A user page may cite a work whole.

**Every item implementing a published result cites it**, in its docstring, a module or a
function alike, at the clause the result supports and before its punctuation. A step that
departs from the result cites it and names the departure beside it.

**The theory page names the item.** [theory.md](theory.md) states each result the package
rests on as a claim: an `IMPLEMENTED-BY:` line names the items citing it and a `HELD-BY:` line
the tests that fail when it is false. A citation written into the package is written into that
page in the same change.

`tools/docs_lint.py` refuses a key of two surnames or of one with `et al.` that the page does
not list, and an entry no tracked text cites. A key of one surname is not read. In the package
and on the theory page it refuses a citation with no place; an `IMPLEMENTED-BY:` entry naming
no item, or an item citing none of the works its claim cites; an item citing a work that no
claim naming it cites; and a `HELD-BY:` name that is no test. Where the papers are at hand, only
in the author's clone, it refuses a place the copy of its paper does not print.

## Commit messages

A commit states what the change does and what it measures. Same standard as everything else:
fact, no history, no meta. `AGENTS.md` states the rule on trailers, and the commit-msg hook
and `tools/docs_lint.py --commits` hold it with the form below.

```
scope: what the change does

The defect, stated as a property of the code. What the change does about it. The
measurement. What remains open.
```

- **Subject**: `scope: ` and a lowercase summary, no trailing period, 72 characters at most.
  The scope is the module touched, by its file stem, as `planner` for
  `valgebra_planning/planner.py`; a change across modules takes the module whose contract it
  changes. Beside the modules, `SCOPES` in `tools/docs_lint.py` names the others: `study` for
  an example with its driver and counts, `docs`, `tools`, `tests`, `build`, `ci`, `repo`.
- **Body**: wrapped at 88 columns, in authoritative present tense. Give the numbers.
- One logical change per commit. A subject needing "and" is usually two commits.
- No provenance: not who found it, not how, not what was tried first, not how long it stood.
- No references to untracked files, external trackers, or the tooling that produced the
  change.

A defect is described in the present tense as a property:

```
A binding is accepted on each argument alone, so a tool whose input schema relates two
arguments can be called with a pair it refuses. Such a call is accepted only once the whole
argument record is proven inside the input schema; `tests/test_verify.py` pins the refusal.
```

Not: "this was introduced by the previous fix", "found while investigating X", "had been
wrong for four milestones". If a cause is not established, state what is established and
what is open. Do not narrate the search.

## The lint, and what it cannot see

```bash
uv run --no-sync python tools/docs_lint.py
uv run --no-sync python tools/docs_lint.py --commits
```

It fails on the mechanical half of documentation rot: a page GitHub renders wrongly, a dead
link, a British spelling, a history word, a reference into the untracked notes, a citation
and the references page disagreeing, and a commit message off its form or carrying a
trailer. The script's docstring holds every rule; a second copy here would drift.

**Three classes stay out of its reach, and they are the common ones:**

- a real symbol attributed to the **wrong file**;
- a list with the wrong **count or order**, each entry real;
- a behavior described as absent from code that has it.

It cannot tell you a sentence is false. A page can link cleanly, spell every word the
American way and cite every work it names, and still describe code replaced three commits
ago. Prefer the claim that stays true: name the owner and the invariant, name the file that
owns the number, and say what the thing does not cover.

## What not to write

| avoid | write |
|---|---|
| "significantly faster" | "2.68x, 917.2 ms to 342.2 ms" |
| "should work correctly" | the test that shows it does |
| "we discovered that" | the fact |
| "this was broken for months" | the property that makes it correct |
| "obviously", "simply", "just" | nothing; delete |
| "comprehensive", "robust" | the coverage, as a number |
| "note that", "it is worth mentioning" | the note |
