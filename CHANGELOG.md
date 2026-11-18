# Changelog

All notable changes to valgebra-planning are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- A part of a cell is asked of valgebra as its kind, each predicate it lies inside, and the
  complement of each it lies outside but for one a predicate inside it already excludes,
  where it was the meet built cut by cut. The set is the same, and valgebra decides more of
  them: the MCP reference servers' cut keeps 64 cells, 29 undecided, where it kept 96, 61
  undecided.
- Schemas that compile to one validator, a description, a title or a default apart, are one
  predicate, where each spelling was one: the reference servers' 106 predicates are 37 sets,
  and their cut asks valgebra 507 relations where it asked 3,044.

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
- Each public module's names are its `__all__`, listed on `docs/api.md`.
- The package requires valgebra 0.0.28 and below 0.0.29 and valgebra-jsonschema 0.0.1 and
  below 0.0.2, since a `0.0.x` minor of either is a breaking release here, and Python 3.11 or
  later; the `mcp` extra takes the MCP SDK 2.3 and below 3.
- Measured on this release, each figure in [`docs/results.md`](https://github.com/ppigazzini/valgebra-planning/blob/main/docs/results.md): on the
  parcel library's eight tasks every link of every program is proven, 3 of 3 to 24 of 24, and
  on synthetic libraries of 8 to 256 tools the cut holds 5 predicates and 12 cells at every
  size.

[Unreleased]: https://github.com/ppigazzini/valgebra-planning/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/ppigazzini/valgebra-planning/releases/tag/v0.0.1
