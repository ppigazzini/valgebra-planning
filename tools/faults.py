"""Run an example's booking programs under injected faults; print, write or check the counts.

    uv run python tools/faults.py examples/bookings
    uv run python tools/faults.py examples/bookings --json docs/studies/faults.json
    uv run python tools/faults.py examples/bookings --check docs/studies/faults.json

The example's one task books with ``book``, which is not idempotent, and reads the booking with
``find_booking``. Three programs are verified, then each is run in ``EPISODES`` seeded episodes
for every fault mode and rate: the one planned without the library's postconditions, the one
planned with them, and a hand-written blind retry, the baseline the verifier refutes. A call to
``book`` faults at the rate given, in one of the failure modes of non-atomic tools (Mansoor et al.
2026, §2.3): ``1`` acts and then answers with an error, ``2`` does so and the next read is stale,
``3`` leaves a booking with no label and answers with an error, ``4`` answers with an error
without acting; ``1+4`` draws either, half each. A run counts as a duplicate where more than one
booking was made. Each count is a row of the study file, and ``--check`` fails when this code no
longer produces it.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from valgebra_planning.critic import read_program
from valgebra_planning.library import read_library
from valgebra_planning.planner import plan
from valgebra_planning.program import Block, render
from valgebra_planning.run import ToolError, run
from valgebra_planning.verify import verify

FORMAT = 1
EPISODES = 400
MODES = ("1+4", "2", "3")
RATES = (0.1, 0.3, 0.5)
BLIND = (
    "try:\n"
    "    c1 = book(parcel_id=parcel_id)\n"
    "except ToolError:\n"
    "    try:\n"
    "        c2 = book(parcel_id=parcel_id)\n"
    "    except ToolError:\n"
    "        stop(parcel_id)\n"
    "    return c2.label_id\n"
    "return c1.label_id\n"
)


class _World:
    """The bookings one episode makes, and the faults it draws."""

    def __init__(self, rate: float, modes: str, seed: str) -> None:
        self.rate, self.modes, self.rng = rate, modes, random.Random(seed)
        self.bookings: list[str | None] = []
        self.stale = False

    def book(self, **_: Any) -> Any:
        """Book once, or fault in one of the modes."""
        label = f"lbl_{len(self.bookings) + 1}"
        if self.rng.random() < self.rate:
            mode = self.rng.choice(("1", "4")) if self.modes == "1+4" else self.modes
            if mode in {"1", "2"}:
                self.bookings.append(label)
            elif mode == "3":
                self.bookings.append(None)
            self.stale = mode == "2"
            message = "no answer"
            raise ToolError(message)
        self.bookings.append(label)
        return {"label_id": label}

    def find_booking(self, **_: Any) -> Any:
        """Return the first booking's label, none before one, and none once while stale."""
        if self.stale:
            self.stale = False
            return {}
        labels = [label for label in self.bookings if label is not None]
        return {"label_id": labels[0]} if labels else {}


def study(example: Path) -> tuple[dict[str, Any], list[str]]:
    """Return the study of an example and the text printed for it."""
    library = read_library(example)
    problem = library.problems[0]
    task, tools, posts = problem.task, library.tools, library.postconditions
    programs: dict[str, Block | None] = {
        "planned": plan(tools, task, errors=True).program,
        "planned with its postconditions": plan(
            tools, task, errors=True, postconditions=posts
        ).program,
        "blind retry": read_program(BLIND),
    }
    rows, text = [], [f"## {task.name}"]
    for name, program in programs.items():
        if program is None:
            rows.append({"program": name, "found": False})
            continue
        checked = verify(program, tools, task, errors=True, postconditions=posts)
        text += ["", f"### {name}", "", "```", render(program), "```"]
        for modes in MODES:
            for rate in RATES:
                outcomes = []
                for episode in range(EPISODES):
                    world = _World(rate, modes, f"{modes}-{rate}-{episode}")
                    calls = {"book": world.book, "find_booking": world.find_booking}
                    ended = run(program, tools, task, {"parcel_id": "pcl_1"}, calls)
                    outcomes.append((ended.outcome, len(world.bookings)))
                rows.append(
                    {
                        "program": name,
                        "links": len(checked.links),
                        "proven": checked.count("proven"),
                        "refuted": checked.count("refuted"),
                        "modes": modes,
                        "rate": rate,
                        "episodes": EPISODES,
                        "returned": sum(outcome == "returned" for outcome, _ in outcomes),
                        "stopped": sum(outcome == "stopped" for outcome, _ in outcomes),
                        "duplicates": sum(made > 1 for _, made in outcomes),
                    }
                )
    return {"format": FORMAT, "example": example.name, "rows": rows}, text


def main(argv: list[str]) -> int:
    """Run an example's programs under faults, then print, write or check the study."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("example", type=Path)
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--json", type=Path)
    out.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    result, text = study(args.example)
    print("\n".join(text))
    for row in result["rows"]:
        print(row)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.check:
        published = json.loads(args.check.read_text(encoding="utf-8"))
        if published != result:
            print(f"\nthe counts differ from {args.check}")
            return 1
        print(f"\nno difference against {args.check}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
