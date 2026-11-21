# Changelog

All notable changes to valgebra-planning are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The critique of a field read without the test showing its key, where the link carries a
  record lacking the key or valgebra leaves its presence undecided, names the key test to add:
  `if "note" in c1.parcel:`, and the read in its branch.

### Removed

- `Contract.optional`. The verifier reads a field below any value a program holds where
  valgebra proves it present, so a list of the optional fields along a result's guaranteed
  objects names fewer than it reads, and nothing reads the list.

### Fixed

- The verifier reads any field below a value the program holds, at any depth, where valgebra
  proves every value held there an object holding its key: a loop item's field under a test
  showing its key, a field below an optional one under both tests, a task input's field under
  its test, and an item's field past the third level from the call's result. Each was a value
  the program does not hold.
- A field a record's declared schema leaves optional and its root requires, as an `allOf` may,
  is asked of the root, and never refuted on a value the declared schema admits and no result
  carries. A field whose presence valgebra leaves undecided reads undecided, not refuted.
- A flow rule reads a value as the verifier reads it, a field read below an item included.
- A loop over an array whose `items` refers into a resource with an `$id` of its own reads
  undecided; the verifier raised `Unsupported`.
- A key that is no Python identifier, or is a keyword, as `a-b` or `class`, is written as a
  subscript, `x["a-b"]`, in a rendered program, a link's claim and a critique, so each reads
  back; it was joined with a dot, which no reader reads.
- The argument record of a call whose input schema relates its arguments refutes only on a
  record valgebra proves the values the program holds carry jointly, or where each argument is
  a constant or a whole result or input, no two the same. A record no call makes, as one
  pairing two fields a result keeps equal, was reported as a counterexample.
- A run reading a field the value it holds lacks ends with the read named as a broken check,
  `Refusal.check` `read`, with `enforce` or without; it raised `KeyError`.
- A link on a field whose declared schema another keyword narrows, as an `allOf`, is asked of
  the value the field belongs to: such a field bound inside its argument is proven, and a
  value only the declared schema admits, which no result carries, is never reported as a
  counterexample.

### Security

- A task naming an input as a tool is refused: `verify`, `critique`, `plan`, `run` and
  `read_library` raise `ValueError` naming it. Such an input's value read as that tool's
  result, so a key test on it held the tool's optional fields with the tool's schemas, and a
  flow rule with `only` naming the tool as its source let it reach the sink: a program passing
  a value outside a tool's argument, or breaking the rule, was accepted.
- An argument whose name ends in `?` is read as written in the record a relation-dependent
  input schema and a policy are asked of. valgebra's dict display reads a trailing `?` as an
  optional field of the name without it, so a relation on such an argument, as
  `dependentRequired`, went unchecked, and the verifier proved a call the input schema
  refuses.
- A result slot the walk listing slots reads is kept only where valgebra proves the whole output
  schema's set inside it. The walk resolves a `$ref` from the document's root, so a field inside
  a resource with an `$id` of its own, referring into that resource, read another schema's
  set, and a binding of it was proven inside an argument its values lie outside. A field and
  an item read below a value are held to the same proof.

## [0.0.4] - 2026-11-19

A release of the dependency range: the planner is 0.0.3's, and every published count is the one
0.0.3 recorded, read again on valgebra 0.0.30 and valgebra-jsonschema 0.0.3.

### Changed

- The package requires valgebra 0.0.30 and valgebra-jsonschema 0.0.3 or later, with no upper
  bound on either: a release of either installs beside this package the day it is published,
  with no release of this package.

### Fixed

- The live-server example on the code mode page quotes the interpreter's path, so it runs on
  Windows, and the command line's `--server` and `connect` state how a command is split: as a
  POSIX shell splits it, on Windows as well, so a path holding a backslash or a space is quoted.

## [0.0.3] - 2026-11-18

A cell of many overlapping ranges is decided again, as 0.0.1 decided it, and the cut keeps the
cells 0.0.2 decides.

### Fixed

- A part of a cell is asked first as the meet its cell holds with the new predicate, as 0.0.1
  asked it, and as its kind with every predicate it lies inside or outside only where valgebra
  leaves that meet undecided. Asked in the second spelling alone, as 0.0.2 asks it, a cell of
  many overlapping integer ranges was a meet too wide for valgebra to decide: valplan's numbers
  task of 128 tools cut into 43,458 cells, 42,989 undecided, in 287 s, and cuts into 507, none
  undecided, in 6 s, as on 0.0.1. The MCP reference servers' cut keeps 64 cells, 29 undecided,
  as on 0.0.2.

## [0.0.2] - 2026-11-18

The cut a plan searches is the library's, whatever order it lists its tools in, and valgebra
decides more of it; the user guide is published.

### Added

- The user guide is published at <https://ppigazzini.github.io/valgebra-planning/>, with
  `llms.txt` and `llms-full.txt` for a reader that is not a browser, and the Python API read
  from the package's docstrings.
- A recipes page does six tasks end to end in Python over one tool library: a model's program
  checked and run, a server answering outside its contract, a policy, a flow rule, a privilege
  update and a policy that cannot fire.

### Changed

- A part of a cell is asked of valgebra as its kind, each predicate it lies inside, and the
  complement of each it lies outside but for one a predicate inside it already excludes,
  where it was the meet built cut by cut. The set is the same, and valgebra decides more of
  them: the MCP reference servers' cut keeps 64 cells, 29 undecided, where it kept 96, 61
  undecided.
- Schemas that compile to one validator, a description, a title or a default apart, are one
  predicate, where each spelling was one: the reference servers' 106 predicates are 37 sets,
  and their cut asks valgebra 507 relations where it asked 3,044.
- The package's `Documentation` link and the README's links go to the published guide. Each
  page's address is its topic, `/command-line/` and `/method/`, and an address the site has
  served before answers with a page that sends the reader on.
- The package requires valgebra 0.0.29 and below 0.0.30, and valgebra-jsonschema 0.0.2 and
  below 0.0.3, whose releases change no answer of valgebra 0.0.28 and valgebra-jsonschema
  0.0.1.

### Fixed

- The cut a plan searches is the same however a library lists its tools: its predicates are
  cut in the order of their names. Split in listing order, the cells valgebra leaves undecided
  moved with that order: the seven MCP reference servers' 106 predicates gave 97, 127 or 130
  cells in three orders, and give 96, 61 of them undecided, in every order now, asking
  valgebra 3,035 relations where the order the census used asked 3,082.

## [0.0.1] - 2026-11-17

### Added

- `read_contract` reads an MCP tool's `inputSchema` and `outputSchema`, through
  valgebra-jsonschema, as argument and result slots, an optional output field held apart behind
  a key test and an output that is not an object as one slot; `read_task` reads a task's inputs
  and goal.
- `plan` searches a strong program of calls and type tests over the cells of every slot, input
  and goal, or a weak one whose stops name the values they leave; a tool the goal cannot be
  reached through is dropped first. `predicates` returns the list the cells are cut from.
- `verify` asks valgebra every link of a program alone, reading neither the cells nor the
  search: each is proven, refuted with a value outside it, or undecided.
- `critique` reads a program written in Python against `stubs`, verifies it and names, for
  each link not proven, the test, the order, the source or the error block it needs; `repair`
  runs that loop with any callable as the model.
- A program is held to tool errors and to retries of read-only or idempotent tools, to
  policies read as automata over calls (`policy`), to flow rules over where a value came from
  (`flows`), to semantic types mined from observed calls (`mining`), and to privilege policies
  in Progent's format (`privilege`). `vacuities` lists the specifications of a library that
  hold vacuously.
- `run` runs a program failing closed on every argument record, policy step and result, and
  `blame` names the cause of a break; `run_on_server` runs it against a live MCP server, a call
  that asks for input failing as a tool error.
- `serve` puts an MCP proxy over stdio in front of a server, holding every call to the
  contracts, policies and flow rules: its contract and policy checks hold whatever wrote the
  call, and its flow rules read provenance by equality of values.
- The command line `valgebra-planning`: `plan`, `critique`, `stubs`, `check`, `privileges`,
  `run` and `proxy`.
- Each public module's names are its `__all__`, listed on the API page.
- The package requires valgebra 0.0.28 and below 0.0.29 and valgebra-jsonschema 0.0.1 and
  below 0.0.2, since a `0.0.x` minor of either is a breaking release here, and Python 3.11 or
  later; the `mcp` extra takes the MCP SDK 2.3 and below 3.
- Measured on this release, each figure in [the results page](https://ppigazzini.github.io/valgebra-planning/results/): on the
  parcel library's eight tasks every link of every program is proven, 3 of 3 to 24 of 24, and
  on synthetic libraries of 8 to 256 tools the cut holds 5 predicates and 12 cells at every
  size.

[Unreleased]: https://github.com/ppigazzini/valgebra-planning/compare/v0.0.4...HEAD
[0.0.4]: https://github.com/ppigazzini/valgebra-planning/compare/v0.0.3...v0.0.4
[0.0.3]: https://github.com/ppigazzini/valgebra-planning/compare/v0.0.2...v0.0.3
[0.0.2]: https://github.com/ppigazzini/valgebra-planning/compare/v0.0.1...v0.0.2
[0.0.1]: https://github.com/ppigazzini/valgebra-planning/releases/tag/v0.0.1
