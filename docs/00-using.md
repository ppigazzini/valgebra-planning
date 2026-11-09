# Using valgebra-planning

How to plan the tasks of a tool library, or check a program written for one, from the command
line. What each answer means is [01-method.md](01-method.md)'s.

## A library

A library is a directory (`valgebra_planning/library.py` reads it):

| file | holds |
|---|---|
| `tools.json` | a server's `tools/list` result: `{"tools": [...]}`, each tool its `name`, `inputSchema`, and the `outputSchema` and `annotations` it declares |
| `tasks.json` | `{"tasks": [...]}`, each task a `name`, the JSON Schema of each value the agent starts with in `inputs`, the JSON Schema of the `goal`, and the rules it is held to: `errors` (any tool may fail), `policies` and `flows` by name, `types`, the file of observed calls its semantic types are mined from |
| `policies.json` | `{"policies": [...]}`, each an automaton over calls (`valgebra_planning/policy.py`) |
| `flows.json` | `{"flows": [...]}`, each a flow rule (`valgebra_planning/flows.py`) |
| a file `types` names | `{"calls": [...]}`, each an observed call: `tool`, `arguments`, `result` |

A tool without an `outputSchema` can be called, and nothing it returns can fill another call's
argument: its result is a set no argument is proven to hold. `examples/parcels` is a library
holding every file.

## Plan

```bash
uv run valgebra-planning plan examples/parcels --task "return a parcel"
```

prints the program for each task (every task when no `--task` is given; `--task` repeats), whether
it is strong or weak, how many of its links valgebra proves, each link not proven, each value a
weak program's stops leave uncovered, and each binding resting on a mined type. With `--json` it
prints one JSON document, a list holding a row per task. The exit status is 1 when a task gets no
program.

## Critique a program written elsewhere

```bash
uv run valgebra-planning critique examples/parcels --task "return a parcel" program.py
```

reads `program.py`, the Python an agent writes against typed tool APIs (`valgebra_planning/critic.py`
states what it reads), verifies every link, and prints each link not proven with the value that
refutes it and the test that would make the call safe. The exit status is 0 when the program is
accepted, 1 when it is not, and 2 when the reader refuses its text.

## Limits

- The command line reads files. A live server is not read and no tool is called.
- `critique` holds a program to the contracts. It does not hold it to the rules the task names.
