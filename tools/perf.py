"""Count the instructions a call takes, in this tree and in other environments, under cachegrind.

    uv run --no-sync python tools/perf.py
    uv run --no-sync python tools/perf.py --against /tmp/last1 verify:parcels:2 plan:bookings:0
    uv run --no-sync python tools/perf.py --first --against /tmp/last1

A case names a function, an example and one of its tasks, as ``verify:parcels:2``; with none named,
each of ``CASES`` is counted. ``--against`` names an environment holding a release, as
`docs/dev/releasing.md` makes one, and each count is read against the first named. A count is the
difference of two runs of the case, so start-up and the reading of the library cancel: the
instructions of one call among many on one library, or, with ``--first``, of the first call on
contracts just read. The hash seed is fixed, so a count is the machine's and the interpreter's, and
load does not move it: ``--jobs`` runs are made at once, by default half the processors. It sees no
cache and no branch, so a time is read beside it.

``mistakes`` verifies ``MISTAKES`` programs, each the planned one with lines edited, most of them
refuted: what a critic reads most. Needs valgrind, and an interpreter built for no newer processor
than it simulates, as uv's are. Exits 2 where a count cannot be taken.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from valgebra_planning.codemode import stubs
from valgebra_planning.critic import ProgramError, critique, read_program
from valgebra_planning.library import read_library
from valgebra_planning.meaning import findings
from valgebra_planning.planner import plan
from valgebra_planning.program import Block, render
from valgebra_planning.verify import verify

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CASES = (
    *(f"verify:parcels:{task}" for task in range(8)),
    "verify:bookings:0",
    "mistakes:parcels:1",
    "critique:parcels:0",
    "critique:parcels:2",
    "critique:bookings:0",
    "read_program:parcels:0",
    "render:parcels:0",
    "stubs:parcels:0",
    "plan:parcels:0",
    "plan:parcels:2",
    "plan:parcels:3",
    "plan:bookings:0",
    "findings:parcels:0",
    "findings:bookings:0",
)
#: How many programs with lines edited `mistakes` verifies.
MISTAKES = 60
#: The two call counts a count is the difference of, by the function.
CALLS = {"plan": (1, 3), "findings": (2, 6), "mistakes": (1, 2), "render": (20, 60)}
IREFS = re.compile(r"I\s+refs:\s+([\d,]+)")


def edited(source: str, count: int) -> list[str]:
    """Return ``count`` programs, each ``source`` with a line's fields and a line's test edited."""
    rng = random.Random(0)
    lines = source.split("\n")
    fields = sorted(set(re.findall(r"\.([a-z_]+)", source)))
    found = []
    for _ in range(count):
        edit = list(lines)
        at = rng.randrange(len(edit))
        edit[at] = re.sub(r"\.[a-z_]+\b(?!\()", lambda _: f".{rng.choice(fields)}", edit[at])
        at = rng.randrange(len(edit))
        edit[at] = re.sub(r" in \w+\.\w+:", f" in ({rng.randrange(400)}, 'lost'):", edit[at])
        found.append("\n".join(edit))
    return found


def workload(case: str, source: str | None) -> Callable[[], object]:
    """Return the call a case names, on a library read here; ``source`` is the program it reads."""
    function, example, task = case.split(":")
    library = read_library(EXAMPLES / example)
    problem = library.problems[int(task)]
    rules: dict[str, Any] = {
        "policies": problem.policies,
        "flows": problem.flows,
        "errors": problem.errors,
        "postconditions": library.postconditions,
    }
    depth: dict[str, Any] = {} if problem.depth is None else {"depth": problem.depth}

    def read(text: str) -> Any:
        try:
            return read_program(text)
        except ProgramError:
            return None

    programs = [] if source is None else [read(source)]
    if function == "mistakes" and source is not None:
        programs = [found for text in edited(source, MISTAKES) if (found := read(text)) is not None]

    def verified() -> object:
        return [verify(program, library.tools, problem.task, **rules) for program in programs]

    calls: dict[str, Callable[[], object]] = {
        "verify": verified,
        "mistakes": verified,
        "critique": lambda: critique(source or "", library.tools, problem.task, **rules),
        "read_program": lambda: read_program(source or ""),
        "render": lambda: render(programs[0]),
        "stubs": lambda: stubs(library.tools),
        "plan": lambda: plan(library.tools, problem.task, **depth, **rules),
        "program": lambda: plan(library.tools, problem.task, **depth, **rules).program,
        "findings": lambda: findings(library),
    }
    return calls[function]


def program(case: str) -> str | None:
    """Return the text of the program the planner finds for a case's task, if it finds one."""
    _, example, task = case.split(":")
    found = workload(f"program:{example}:{task}", None)()
    return render(found) if isinstance(found, Block) else None


def instructions(text: str) -> int | None:
    """Return the instructions a run of cachegrind says it executed, or None."""
    found = IREFS.search(text)
    return None if found is None else int(found[1].replace(",", ""))


def executed(python: Path, case: str, calls: int, source: Path) -> int | None:
    """Return the instructions ``python`` executes over ``calls`` calls of a case, or None."""
    command = ["valgrind", "--tool=cachegrind", "--cache-sim=no", "--cachegrind-out-file=/dev/null"]
    # Keep this script's folder and the working directory off the path: the environment's package
    # is the one counted.
    worker = [str(python), "-s", "-P", str(Path(__file__).resolve()), "--run", case, str(calls)]
    run = subprocess.run(  # noqa: S603 - this tool, under the interpreter it was asked for
        [*command, *worker, "--program", str(source)],
        env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"},
        cwd="/",
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return instructions(run.stderr) if run.returncode == 0 else None


def counted(python: Path, case: str, source: Path, *, first: bool) -> int | None:
    """Return the instructions one call of a case takes, or None where none could be counted."""
    low, high = (0, 1) if first else CALLS.get(case.partition(":")[0], (5, 15))
    few = executed(python, case, low, source)
    many = executed(python, case, high, source)
    return None if few is None or many is None else (many - few) // (high - low)


def row(case: str, counts: Sequence[int | None]) -> str:
    """Return a case's line: the first environment's count, and each other's against it."""
    base = counts[0]
    cells = [f"{case:<24}", f"{base:>14,}" if base is not None else f"{'no count':>14}"]
    for count in counts[1:]:
        read = None if base is None or count is None else count / base
        cells.append(f"{'no count' if read is None else f'{read:.2f}':>10}")
    return "".join(cells)


def worker(case: str, calls: int, source: Path) -> None:
    """Run a case ``calls`` times."""
    text = source.read_text(encoding="utf-8")
    call = workload(case, text or None)
    for _ in range(calls):
        call()


def main(argv: list[str]) -> int:
    """Count the cases named, or each, in this tree and in the environments named."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", nargs="*", default=[])
    parser.add_argument("--against", type=Path, action="append", default=[])
    parser.add_argument("--first", action="store_true")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 1) // 2))
    parser.add_argument("--run", nargs=2, metavar=("CASE", "CALLS"), help=argparse.SUPPRESS)
    parser.add_argument("--program", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.run:
        worker(args.run[0], int(args.run[1]), args.program)
        return 0
    if shutil.which("valgrind") is None:
        print("perf: valgrind is not on PATH")
        return 2
    pythons = [*(env.absolute() / "bin" / "python" for env in args.against), Path(sys.executable)]
    names = [*(env.name for env in args.against), "the tree"]
    print(f"{'case':<24}{names[0]:>14}" + "".join(f"{name:>10}" for name in names[1:]))
    cases = args.cases or CASES
    with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(args.jobs) as pool:
        runs = []
        for at, case in enumerate(cases):
            source = Path(folder) / f"{at}.txt"
            source.write_text(program(case) or "", encoding="utf-8")
            runs.append(
                [pool.submit(counted, env, case, source, first=args.first) for env in pythons]
            )
        for case, run in zip(cases, runs, strict=True):
            counts = [future.result() for future in run]
            print(row(case, counts), flush=True)
            if None in counts:
                print(f"perf: {case} could not be counted in every environment")
                pool.shutdown(cancel_futures=True)
                return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
