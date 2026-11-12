from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
import docs_lint

GOOD = (
    "planner: cut cells only for tools a program can reach the goal through\n"
    "\n"
    "A tool is kept only if it can be called and it matters, so the cells stay at 12 from 5\n"
    "predicates at every size.\n"
)


def test_a_british_word_is_refused_in_prose_and_in_a_name() -> None:
    assert docs_lint.british_words("the behaviour is normalised") == ["behaviour", "normalised"]
    assert docs_lint.british_words("build_unrecognised = NonNegativeMetres") == [
        "unrecognised",
        "Metres",
    ]
    assert docs_lint.british_words("the behavior is normalized, modeled and labeled") == []


def test_a_history_word_is_read_in_comments_and_docstrings_only() -> None:
    source = (
        '"""Return the cells, as they previously were."""\nWORDS = "used to"  # formerly a list\n'
    )
    problems = docs_lint.check_file("valgebra_planning/example.py", source)
    assert [p.split("'")[1] for p in problems] == ["previously", "formerly"]


def test_the_untracked_area_is_named_nowhere_but_the_ignore_file() -> None:
    assert docs_lint.check_file("README.md", "see __DEV/PLAN.md, step P7\n")
    assert docs_lint.check_file(".gitignore", "__DEV/\n") == []


def test_a_citation_reads_across_a_line_break_and_a_comment_marker() -> None:
    text = "# planning (Cimatti et\n# al. 2003) and D'Antoni\n# and Veanes 2017"
    assert docs_lint.citations(text) == {"Cimatti et al. 2003", "D'Antoni and Veanes 2017"}


def test_citations_and_the_references_page_are_held_equal() -> None:
    page = "- **Graf and Saidi 1997.** S. Graf, H. Saidi.\n- **Feng et al. 2017.** Y. Feng.\n"
    texts = {
        docs_lint.REFERENCES: page,
        "docs/01-method.md": "abstraction (Graf and Saidi 1997), as in Guo et al. 2022\n",
    }
    problems = docs_lint.check_citations(texts)
    assert len(problems) == 2
    assert "'Guo et al. 2022', which" in problems[0]
    assert "lists 'Feng et al. 2017'" in problems[1]


def test_the_readme_links_into_the_tree_by_address() -> None:
    readme = docs_lint.ROOT / "README.md"
    main = "https://github.com/ppigazzini/valgebra-planning/blob/main/"
    assert docs_lint.check_markdown(readme, f"[m]({main}docs/01-method.md)\n") == []
    [dead] = docs_lint.check_markdown(readme, f"[m]({main}docs/99-none.md)\n")
    assert "dead link" in dead
    [relative] = docs_lint.check_markdown(readme, "[m](docs/01-method.md)\n")
    assert "PyPI" in relative
    page = docs_lint.ROOT / "docs" / "README.md"
    assert docs_lint.check_markdown(page, "[m](01-method.md)\n") == []


def test_a_message_in_the_form_passes() -> None:
    assert docs_lint.check_message(GOOD) == []


def test_every_trailer_and_attribution_is_refused() -> None:
    for line in (
        "Co-Authored-By: Someone <someone@example.com>",
        "Signed-off-by: Someone <someone@example.com>",
        "Reviewed-by: Someone",
    ):
        problems = docs_lint.check_message(f"{GOOD}\n{line}\n")
        assert any("trailer" in p for p in problems), line
    problems = docs_lint.check_message(f"{GOOD}\nGenerated with a tool\n")
    assert any("attribution" in p for p in problems)


def test_the_subject_and_the_body_keep_the_form() -> None:
    long = "planner: " + "x" * 64
    assert any("past 72" in p for p in docs_lint.check_message(long + "\n"))
    assert any("no module" in p for p in docs_lint.check_message("outcomes: a retry\n"))
    assert any("lowercase" in p for p in docs_lint.check_message("planner: A retry\n"))
    assert any("period" in p for p in docs_lint.check_message("planner: a retry.\n"))
    assert any("blank line" in p for p in docs_lint.check_message("planner: a\nbody\n"))
    wide = "planner: a retry\n\n" + "word " * 18 + "\n"
    assert any("past 88" in p for p in docs_lint.check_message(wide))
    assert any("British" in p for p in docs_lint.check_message("planner: a colour\n"))
    assert any("this commit" in p for p in docs_lint.check_message("planner: a\n\nthis commit\n"))


def test_the_hook_reads_the_message_git_keeps(tmp_path: Path) -> None:
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(
        GOOD + "# Please enter the commit message.\n"
        "# ------------------------ >8 ------------------------\n"
        "diff --git a/x b/x\n",
        encoding="utf-8",
    )
    assert docs_lint.message_file(path) == GOOD
