# AGENTS

valgebra-planning plans programs of tool calls and verifies them over
[valgebra](https://github.com/ppigazzini/valgebra)'s sets. A tool's contract, its MCP
`inputSchema` and `outputSchema`, is read by
[valgebra-jsonschema](https://github.com/ppigazzini/valgebra-jsonschema); valgebra decides
every inclusion a program rests on; this package reads the program, searches for it and holds
it to its rules. It is not an agent framework and calls no language model: a program a model
writes is an input here, verified link by link like any other.

**A link is accepted on a proof or not at all.** A proof is valgebra's, or a concrete value's
where one refutes; a relation valgebra cannot decide is `undecided`, and nothing here reads it
as true or false. What a set means is valgebra's, what a keyword means is the companion's, and
what a program means is here. A gap in deciding a set belongs in valgebra and a gap in reading
a schema in the companion; neither is worked around here by a guess.

## Read first

| Changing | Read |
|---|---|
| what a program, a link or a rule means | [the method](docs/method.md) |
| a public name, or a module's `__all__` | [the Python API](docs/api.md) |
| a published count | [the results](docs/results.md), and the study file its command checks |
| a citation | [the works cited](docs/references.md) |
| code that implements a paper's result | [the theory page](docs/dev/theory.md), [citing a paper](docs/dev/writing.md#citing-a-paper) |
| a release, or a dependency's floor | [the releasing page](docs/dev/releasing.md) |
| any prose at all: a page, a comment, a docstring, a commit message | [the writing rules](docs/dev/writing.md) |

## Setup

```bash
uv sync                               # the package and its dev group, into .venv
uv run --no-sync pre-commit install   # the hooks, the commit-msg hook among them
```

## The gate

A change is not done until every command exits 0. Trust exit codes, not log text: a process
can print progress and then fail. If a command cannot run, say so and list what was checked
instead. The job `test` of `.github/workflows/ci.yml` runs the same commands on each push to
`main` or `github_ci` and each pull request into `main`, and `tests/test_build_surfaces.py`
holds the two lists equal: a command added here is a step there in the same change.

```bash
uv run --no-sync ruff check
uv run --no-sync ruff format --check
uv run --no-sync ty check
uv run --no-sync python tools/docs_lint.py
uv run --no-sync python tools/docs_lint.py --commits
uv run --no-sync pytest
uv run --no-sync python tools/case.py examples/parcels --check docs/studies/parcels.json
uv run --no-sync python tools/bench.py --check docs/studies/bench.json
```

**Every `uv run` carries `--no-sync`.** A run that syncs replaces the environment the change
was tested in. Sync deliberately, with `uv sync --locked`.

A change that moves a published count regenerates the study with `--json` in the same commit,
and its message says why the count moved.

The job `docs` builds the site on every push, after `uv sync --locked --group docs`, with
`uv run --no-sync zensical build --strict`: `mkdocs.yml` raises every check the builder makes on
a link and on the nav to a warning, so a dead link, a dead anchor or a page off the nav fails it.

## Layout

- `valgebra_planning/` — the package. `_valgebra.py` is the only module importing valgebra
  or valgebra-jsonschema, and `tests/test_boundaries.py` holds it.
- `examples/` — the tool libraries, tasks, policies, flow rules and observed calls the
  studies read.
- `tools/` — the study drivers, `case.py` and `bench.py`, and the lint, `docs_lint.py`.
- `docs/` — the user guide `mkdocs.yml` builds and the workflow `Docs` publishes, with
  `llms.txt`, at <https://ppigazzini.github.io/valgebra-planning/>; `docs/dev/` holds the
  developer pages, releasing and writing, which are not published, and `docs/studies/` the
  counts the drivers check.
- `tests/` — pytest suites.

Style, naming and formatting are enforced by ruff and ty: follow their output rather than
restating rules here.

## Project rules

These are the invariants tooling cannot enforce. Each pairs a prohibition with what to do
instead.

- **An uncertain change does not land.** If it is not settled that a change is correct, it
  does not go in: not behind a flag, not to be reviewed later. Write it up with the test it
  fails; the write-up is the deliverable.

- **Analysis is not authorization.** A request to review, audit, measure or report is a
  request for a document. It does not license a commit, a dependency bump or a fix made in
  passing: propose the change in the document, and the decision belongs to whoever asked.

- **A run is not a proof.** `valgebra_planning/run.py` checks the contracts on the values it
  draws; `valgebra_planning/verify.py` proves them for every value the contracts admit. A test
  showing a program correct verifies it; a run beside it shows the runner agrees.

- **Keep an undecided cell.** A part of a cell is dropped only where valgebra proves it empty,
  so an undecided cell can cost a program and never admit a wrong one. A study is made smaller
  by pruning what cannot matter, never by dropping what valgebra did not decide.

- **A mined type is an assumption.** The verifier reads none, and a plan names each binding
  that rests on one. A rule that narrows on observations goes to the planner, never into
  `verify.py`.

- **Cross the boundary in one module.** Import valgebra and valgebra-jsonschema only in
  `valgebra_planning/_valgebra.py`: add the name there and import it from there.

- **No number without its command.** Every count on a page is a row of a study file under
  `docs/studies/` that a command's `--check` reproduces, and the command stands beside it. A
  number no study holds is deleted, not footnoted.

- **valplan's code does not enter this tree.** valplan
  ([doi:10.5281/zenodo.23686600](https://doi.org/10.5281/zenodo.23686600)) is the prior work,
  licensed Apache-2.0 alone; this package is MIT OR Apache-2.0 and written anew. Read it and
  cite it; never copy from it. A technique valplan's studies measure enters this package only
  as a change of its own, with its own tests, under
  [valplan's rule for it](https://github.com/ppigazzini/valplan/blob/main/docs/01-method.md#valgebra-planning),
  and this package never imports valplan.

- **A paper is cited, never committed.** A third party's PDF does not enter the tree: the work
  goes on [the works cited](docs/references.md) with its DOI or arXiv identifier, and
  the text cites its key.

- **Write American English, names included.** `behavior`, `normalize`, `modeled`, `labeled`,
  `license`, in prose, comments, docstrings, messages, identifiers and file names. A
  quotation, a published title and a third party's name keep the spelling they were published
  with. `tools/docs_lint.py` refuses a British spelling in every tracked file and every commit
  message.

- **Use plain names that shadow none of a caller's.** An export is not named for a builtin
  (`compile`, `filter`) or for a standard-library name a caller imports bare (`time`, `date`).
  ruff's flake8-builtins rules refuse the builtin half; the standard-library half is a
  reviewer's.

## Commits

**No trailers, no attribution.** A message carries no `Co-Authored-By:`, no `Signed-off-by:`,
no `Generated with` line and no other trailer, and names no tool or agent that took part. The
commit-msg hook and `tools/docs_lint.py --commits` refuse any trailer git parses and any
attribution line.

The form is [the writing rules for commit messages](docs/dev/writing.md#commit-messages): `scope: subject`, a
body wrapped at 88 columns describing the system as it stands after the change, in
authoritative present tense, with its numbers. No history, no meta, and no reference to an
untracked file. The same lint holds the form.
