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
        "docs/method.md": "abstraction (Graf and Saidi 1997), as in Guo et al. 2022\n",
    }
    problems = docs_lint.check_citations(texts)
    assert len(problems) == 2
    assert "'Guo et al. 2022', which" in problems[0]
    assert "lists 'Feng et al. 2017'" in problems[1]


def test_a_citation_names_its_place_across_a_line_break() -> None:
    text = (
        "planning (Cimatti et al.\n    2003, Def. 2.11), the order (Shi et al. 2025, §4.2,"
        " Alg. 2), and pruning (Feng et al. 2017)"
    )
    assert docs_lint.located(text) == [
        ("Cimatti et al. 2003", ("Def. 2.11",)),
        ("Shi et al. 2025", ("§4.2", "Alg. 2")),
        ("Feng et al. 2017", ()),
    ]


MODULE = '''"""The cells (Graf and Saidi 1997, §2.2), and vacuity (Kupferman and Vardi 1999)."""


def cut() -> None:
    """Prune what reaches nothing (Feng et al. 2017, §6.3)."""


def plain() -> None:
    # a prefix decides nothing (Bauer et al. 2011, Def. 3.4)
    return None
'''
PAGE = """# The theory

`(Cimatti et al. 2003)` is an example, and cites nothing.

## The cells

**Cells.** Minterms (Graf and Saidi 1997, §2.2). **[LOAD-BEARING: cells]**

IMPLEMENTED-BY: cells.py, cells.py::cut, cells.py::gone

HELD-BY: test_cells, test_gone

**Vacuity.** (Kupferman and Vardi 1999, Def. 2). **[DEVIATION: vacuity]**

IMPLEMENTED-BY: cells.py
"""


def test_the_theory_page_and_the_package_name_each_other() -> None:
    texts = {
        docs_lint.THEORY: PAGE,
        "valgebra_planning/cells.py": MODULE,
        "tests/test_cells.py": "def test_cells() -> None:\n    pass\n",
    }
    where = f"{docs_lint.THEORY}: "
    assert docs_lint.check_theory(texts) == [
        "valgebra_planning/cells.py: cites 'Kupferman and Vardi 1999' with no place in it",
        f"{where}[cells] names 'cells.py::cut', which cites none of its works",
        f"{where}[cells] names 'cells.py::gone', which is no item of the package",
        f"{where}[cells] is held by 'test_gone', which is no test",
        f"{where}[vacuity] has no HELD-BY: line",
        "cells.py::cut: cites 'Feng et al. 2017', which no claim naming it cites",
        "cells.py::plain: cites Bauer et al. 2011, and no claim names it",
    ]


def test_a_place_is_read_where_a_copy_of_its_work_prints_it(tmp_path: Path) -> None:
    (tmp_path / "PLN_Cimatti2003_Plans.txt").write_text(
        "Definition 2.11 (Solutions). Let\n2.2 Domains\n"
    )
    (tmp_path / "VER_Rosu2012_Monitoring.txt").write_text("Definition 12. Let X be\n")
    page = (
        "(Cimatti et al. 2003, Def. 2.11, §2.2), (Cimatti et al. 2003, Def. 2.1),"
        " (Roşu and Chen 2012, Def. 12), (Guo et al. 2022, §4)"
    )
    texts = {docs_lint.THEORY: page}
    where = f"{docs_lint.THEORY}: "
    assert docs_lint.check_places(texts, tmp_path) == [
        f"{where}(Cimatti et al. 2003, Def. 2.1) is a place no copy of the work prints",
        f"{where}'Guo et al. 2022' has no copy on the shelf to read it in",
    ]
    assert docs_lint.check_places(texts, tmp_path / "absent") == []


def test_the_readme_links_into_the_tree_by_address() -> None:
    readme = docs_lint.ROOT / "README.md"
    main = "https://github.com/ppigazzini/valgebra-planning/blob/main/"
    assert docs_lint.check_markdown(readme, f"[m]({main}docs/method.md)\n") == []
    [dead] = docs_lint.check_markdown(readme, f"[m]({main}docs/99-none.md)\n")
    assert "dead link" in dead
    [relative] = docs_lint.check_markdown(readme, "[m](docs/method.md)\n")
    assert "PyPI" in relative
    page = docs_lint.ROOT / "docs" / "README.md"
    assert docs_lint.check_markdown(page, "[m](method.md)\n") == []


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


def test_a_published_page_does_not_link_into_the_developer_set() -> None:
    page = docs_lint.ROOT / "docs" / "command-line.md"
    assert docs_lint.link_problems(page, "see [the release](dev/releasing.md)")
    assert not docs_lint.link_problems(page, "see [the method](method.md)")
    developer = docs_lint.ROOT / "docs" / "dev" / "README.md"
    assert not docs_lint.link_problems(developer, "see [the release](releasing.md)")


def test_a_link_to_the_site_names_a_published_page_and_anchor() -> None:
    site = docs_lint.SITE
    for good in ("", "method/", "method/#limits", "llms.txt"):
        assert not docs_lint.site_link_problems(f"[x]({site}{good})"), good
    for bad in ("nowhere/", "method/#nowhere", "dev/writing/", "01-method/"):
        assert docs_lint.site_link_problems(f"[x]({site}{bad})"), bad


def test_a_link_says_what_the_page_holds() -> None:
    assert docs_lint.link_text_problems("see [method.md](method.md#limits)")
    assert docs_lint.link_text_problems("see [`docs/dev/writing.md`](docs/dev/writing.md)")
    assert not docs_lint.link_text_problems("see [the method](method.md)")
    assert not docs_lint.link_text_problems("see [`llms.txt`](https://example.com/llms.txt)")


def test_the_llms_manifest_is_held_to_the_nav_and_the_descriptions(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\ndescription: The first page.\n---\n")
    (tmp_path / "b.md").write_text("---\ndescription: The second page.\n---\n")
    config = (
        "nav:\n  - A: a.md\n  - B: b.md\n"
        "plugins:\n  - llmstxt:\n      sections:\n        S:\n"
        "          - a.md: The first page.\n          - b.md: The second page.\n"
    )
    assert docs_lint.manifest_problems(config, tmp_path) == []
    missing = config.replace("          - b.md: The second page.\n", "")
    assert docs_lint.manifest_problems(missing, tmp_path) == [
        "mkdocs.yml: b.md is in the nav but not the llms.txt manifest"
    ]
    swapped = config.replace(
        "          - a.md: The first page.\n          - b.md: The second page.\n",
        "          - b.md: The second page.\n          - a.md: The first page.\n",
    )
    assert docs_lint.manifest_problems(swapped, tmp_path) == [
        "mkdocs.yml: the llms.txt manifest lists the pages in another order"
    ]
    moved = config.replace("The second page.\n", "Another page.\n", 1).replace(
        "- b.md: The second page.", "- b.md: Another page."
    )
    assert "the llms.txt line for b.md is not its description" in " ".join(
        docs_lint.manifest_problems(moved, tmp_path)
    )
