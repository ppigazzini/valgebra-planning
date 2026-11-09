# Agent instructions

valgebra-planning plans and verifies programs of tool calls over
[valgebra](https://github.com/ppigazzini/valgebra)'s sets. A tool's contract is read by
[valgebra-jsonschema](https://github.com/ppigazzini/valgebra-jsonschema); what a set means is
valgebra's, what a keyword means is the companion's, and what a plan means is here. A gap in
deciding a set belongs in valgebra, a gap in reading a schema in the companion.

## Git

- **Never add trailers to commit messages.** No `Co-Authored-By:`, no `Generated with` line,
  no tool attribution of any kind.
- Message style: [`docs/10-writing.md`](docs/10-writing.md#commit-messages).

## Tooling

Everything runs through `uv`, and every `uv run` carries `--no-sync`.

```bash
uv run --no-sync ruff check
uv run --no-sync ruff format --check
uv run --no-sync ty check
uv run --no-sync python tools/check_markdown.py
uv run --no-sync pytest
```

All of them must exit 0 before a commit.

## Conventions

- `docs/10-writing.md` is the standard for pages, comments and commit messages.
- A link of a program is accepted only on a proof, valgebra's or a concrete value's. A
  relation valgebra cannot decide is `undecided`, and nothing here reads it as true or false.
- A set that may hold a value is covered: an undecided cell is kept, so it can cost a program,
  never admit a wrong one.
- `valgebra_planning/_valgebra.py` is the only module that imports valgebra or
  valgebra-jsonschema; `tests/test_boundaries.py` holds it.
