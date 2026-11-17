r"""Hold every text the tree writes to the rules of `docs/dev/10-writing.md`.

    uv run --no-sync python tools/docs_lint.py                    # every tracked file
    uv run --no-sync python tools/docs_lint.py README.md          # the named files
    uv run --no-sync python tools/docs_lint.py --commits [REV]    # every message REV reaches
    uv run --no-sync python tools/docs_lint.py --commit-msg FILE  # one message, as a hook

The rules, and what each costs a reader when it breaks:

* **Markdown GitHub renders wrongly.** Its inline math renderer fails silently:
  `$^{238}$U` dangles a superscript, `\\!` inside inline `$...$` prints a literal `!`, and
  a range split across two spans renders the second as source text. Read on every tracked
  `.md`, outside fenced blocks and inline code spans, since a page documenting the rules
  quotes the broken forms.
* **A dead link,** on every tracked `.md`: a relative one, or an address of this tree's
  `main` naming a path it does not hold. **A relative link in the README**, which is also
  the package's page on PyPI, where none resolves.
* **A link from the site into the developer set.** `mkdocs.yml` excludes `docs/dev/`, so a
  published page linking there resolves in the tree and answers 404 on the site; a published
  page names such a page by its path instead.
* **The llms.txt manifest.** A page in the site's `nav` that the `llmstxt` sections of
  `mkdocs.yml` do not name fails, and the reverse; so does a manifest line whose summary is not
  its page's `description:`.
* **A British spelling,** in every tracked text file and its path, prose, code and names
  alike, and in a commit message.
* **A word stating what the tree was,** on every tracked page, in every comment and
  docstring of a Python source, on every comment line of a TOML or YAML file, and in a
  commit message. A text states what holds now, and the history is git's. `CHANGELOG.md`
  is a record of releases and is exempt.
* **A reference into the untracked working area,** its directory or the names of the
  notes it holds, in every tracked file but `.gitignore`, and in a commit message: it
  dangles for every reader but its author.
* **A citation the references page does not list,** or a work it lists that no tracked
  text cites. A key of two surnames, or of one with `et al.`, is read; a key of one
  surname is not.
* **A citation in the package or on the theory page with no place,** the section or the
  numbered statement after the key, and, where the paper shelf is at hand, a place its copy
  does not print.
* **The theory page and the package apart:** an `IMPLEMENTED-BY:` entry naming no item of
  the package, or an item citing none of the works its claim cites; an item citing a work
  that no claim naming it cites; a claim with no `IMPLEMENTED-BY:` or `HELD-BY:` line; and a
  `HELD-BY:` name that is no test.
* **A commit message off the form** `docs/dev/10-writing.md` gives, or carrying a trailer:
  any line git parses as one, and an attribution line anywhere.

This script and its test spell the words to forbid them, and are exempt from the rules
that read words. What no rule here reads: a real symbol attributed to the wrong file, a
list with the wrong count, and a sentence that is false.

Exit status is 1 if anything is found, so each mode works as a hook.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "valgebra_planning"
REFERENCES = "docs/11-references.md"
THEORY = "docs/dev/09-theory.md"
#: The text renderings of the paper shelf, which only the author's clone holds.
SHELF = ROOT / "__DEV" / "papers" / "text"

# Each rule is (pattern, description). Applied outside fenced blocks and outside
# inline code spans.
RULES: tuple[tuple[str, str], ...] = (
    (r"\$\{?\^", "bare superscript span -- write the whole symbol in one span"),
    (r"\$\{?_", "bare subscript span -- write the whole symbol in one span"),
    (r"\}\$[A-Za-z]", "math span glued to a following letter (split token)"),
    (r"[A-Za-z]\$\{?[A-Za-z^_]", "letter glued to an opening math span"),
    (r"\\text\{[^}]*\\[_^]", r"escaped \_ or \^ inside \text{} -- use n_{\mathrm{in}}"),
    (r"[~\-\u2013]\$", "opening $ glued to a preceding ~, - or en dash"),
    (r"\$[^$\n]*\\[!,;][^$\n]*\$", r"spacing macro (\! \, \;) in inline math"),
)
FENCE = re.compile(r"^\s*(```|~~~)")
LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)\s]*)?\)")
#: The README is also the package's page on PyPI, which resolves no relative link: it links
#: into this tree by the address of `main`, and that address names a path the tree must hold.
README = ROOT / "README.md"
TREE = re.compile(r"https://github\.com/ppigazzini/valgebra-planning/(?:blob|tree)/main/(.+)")

#: A British word, by family: `-our`, `-ise` with its inflections and nouns, `-yse`,
#: `-re`, `-ence`, `-ogue`, `-ement`, a doubled `l` before a suffix, and a few words of
#: their own. Matched against one whole word, after any prefix: `unrecognised` and
#: `relabelled` are words of it. valgebra's lint holds the same families.
BRITISH = re.compile(
    r"[a-z]*(?:"
    r"(?:behavio|colo|endeavo|favo|flavo|harbo|hono|humo|labo|neighbo|rumo"
    r"|vapo)ur(?:s|ed|ing|er|ers|al|ally|able|ably|ite|ites|ful|less|hood)?"
    r"|(?:amorti|authori|canonicali|capitali|categori|characteri|customi"
    r"|determini|emphasi|finali|formali|generali|initiali|lineari|locali|materiali|maximi"
    r"|memoi|minimi|moderni|normali|optimi|organi|parameteri|parametri"
    r"|parenthesi|prioriti|randomi|rationali|reali|recogni|saniti|seriali|speciali|stabili"
    r"|standardi|summari|symboli|synthesi|tokeni|utili|vectori|visuali)"
    r"s(?:e|es|ed|er|ers|ing|ation|ations|able)"
    r"|(?:analy|cataly|paraly)s(?:e|ed|er|ers|ing)"
    r"|(?:cent|fib|lit|met|theat)re[sd]?"
    r"|(?:defen|licen|offen|preten)ces?"
    r"|(?:analog|catalog)ue[sd]?"
    r"|(?:acknowledg|judg)ements?"
    r"|(?:cancel|channel|dial|fuel|label|level|marshal|model|signal|total"
    r"|travel|tunnel)l(?:ed|ing|er|ers)"
    r"|artefacts?|whilst|amongst|programmes?|grey|learnt|spelt|fulfils?"
    r"|fulfilment)",
    re.IGNORECASE,
)
#: One word of prose, or one word of a name: `build_unrecognised` is two, and
#: `NonNegativeMetres` three.
WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
#: A sentence about what the tree was. Narrow on purpose: `no longer` states a rule a
#: reader applies, and a sweep refusing it would be one nobody could keep.
HISTORY = re.compile(
    r"\b(previously|formerly|historically|originally|used to|at one point"
    r"|in the past|back then|before this change|this was fixed in"
    r"|the old behaviou?r|we had|until now|was tried|went red)\b",
    re.IGNORECASE,
)
#: The untracked working area, and the notes it holds: the plan, the paper shelf's list,
#: and the plan's milestones, `P` and a number.
INTERNAL = re.compile(r"__DEV|\bPLAN\.md\b|\bREFERENCES\.md\b|\bP[0-9]+\b")
#: A commit message that describes itself rather than the system.
META = re.compile(r"\bthis (?:commit|change|patch)\b", re.IGNORECASE)
#: An attribution line, wherever it stands in a message.
ATTRIBUTION = re.compile(
    r"^\s*(?:co-authored-by|signed-off-by|\W*generated (?:with|by)\b)", re.IGNORECASE
)

#: A surname: `Feng`, `D'Antoni`, `Zanella-Béguelin`.
NAME = r"[A-Z][\w'\u2019-]*[a-z]"
#: A citation key read anywhere: two surnames, or one with `et al.`, and the year.
CITATION = re.compile(rf"\b{NAME}(?: et al\.| and {NAME}) (?:19|20)[0-9]{{2}}\b")
#: An entry of the references page: its key, bold, opening a list item.
ENTRY = re.compile(
    rf"^- \*\*({NAME}(?: et al\.| and {NAME})? (?:19|20)[0-9]{{2}})\.\*\*", re.MULTILINE
)
#: A line break inside prose, a comment or a docstring, with what continues it.
BREAK = re.compile(r"\s*\n\s*(?:#+\s*)?")
#: One place in a work: a section, or a numbered statement of a form the writing rule lists.
ONE_PLACE = r"§[0-9]+(?:\.[0-9]+)*|(?:Def|Thm|Lem|Prop|Cor|Ex|Alg|Fig)\. [0-9]+(?:\.[0-9]+)*"
#: The places after a key: a comma, then one place or more, a comma between two.
PLACES = re.compile(rf", (?:{ONE_PLACE})(?:, (?:{ONE_PLACE}))*")
#: One place, read into its parts.
PLACE = re.compile(
    r"§(?P<section>[0-9]+(?:\.[0-9]+)*)"
    r"|(?P<form>Def|Thm|Lem|Prop|Cor|Ex|Alg|Fig)\. (?P<number>[0-9]+(?:\.[0-9]+)*)"
)
#: What a statement's form reads as where a paper prints it.
STATEMENT = {
    "Def": "Definition",
    "Thm": "Theorem",
    "Lem": "Lemma",
    "Prop": "Proposition",
    "Cor": "Corollary",
    "Ex": "Example",
    "Alg": "Algorithm",
    "Fig": "Fig(?:ure|\\.)",
}
#: A claim's tag on the theory page.
TAG = re.compile(r"\*\*\[(LOAD-BEARING|DEVIATION): ([a-z][a-z0-9-]*)\]\*\*")

#: The scopes a subject may name beside each module's stem: a study with its example and
#: driver, and the parts of the tree no module owns.
SCOPES = frozenset({"study", "docs", "tools", "tests", "build", "ci", "repo"})
SUBJECT = re.compile(r"(?P<scope>[a-z]+): (?P<text>.+)")
SUBJECT_WIDTH = 72
BODY_WIDTH = 88

#: The files that spell the words to forbid them.
SPELLERS = frozenset({"tools/docs_lint.py", "tests/test_docs_lint.py"})
HISTORY_EXEMPT = SPELLERS | {"CHANGELOG.md", "docs/dev/10-writing.md"}
INTERNAL_EXEMPT = SPELLERS | {".gitignore"}


def strip_code_spans(line: str) -> str:
    """Blank out inline code spans, by the CommonMark backtick-run rule.

    A run of N backticks opens a span and the next run of *exactly* N closes it.
    A regex cannot express that, and a greedy match splits an inline
    ``` ```math ``` span in the middle, exposing an unrelated ``$`` further along
    the line as unbalanced.
    """
    out: list[str] = []
    i = 0
    while i < len(line):
        if line[i] != "`":
            out.append(line[i])
            i += 1
            continue
        n = 0
        while i + n < len(line) and line[i + n] == "`":
            n += 1
        j = i + n
        while j < len(line):
            if line[j] != "`":
                j += 1
                continue
            m = 0
            while j + m < len(line) and line[j + m] == "`":
                m += 1
            if m == n:
                break
            j += m
        if j >= len(line):  # unclosed run: leave the rest as prose
            out.append(line[i:])
            break
        out.append("``")
        i = j + n
    return "".join(out)


def link_problems(path: Path, line: str) -> list[str]:
    """Return one message per dead link on ``line``, and per relative one in the README."""
    problems: list[str] = []
    for target in LINK.findall(line):
        if target.startswith(("http://", "https://", "mailto:")):
            own = TREE.match(target)
            if own and not (ROOT / own[1]).exists():
                problems.append(f"dead link -> {target}")
        elif path.resolve() == README:
            problems.append(f"relative link in the README, which PyPI cannot follow -> {target}")
        elif not (path.parent / target).exists():
            problems.append(f"dead link -> {target}")
        elif _on_the_site(path) and _unpublished(path.parent / target):
            problems.append(
                f"link leaves the site, where docs/dev/ is not published; name the page by its"
                f" path -> {target}"
            )
    return problems


def _parts(path: Path) -> tuple[str, ...]:
    try:
        return path.resolve().relative_to(ROOT).parts
    except ValueError:
        return ()


def _on_the_site(path: Path) -> bool:
    """Answer whether mkdocs renders the page: under `docs/`, outside `docs/dev/`."""
    parts = _parts(path)
    return len(parts) > 1 and parts[0] == "docs" and parts[1] != "dev"


def _unpublished(path: Path) -> bool:
    """Answer whether the page is in the developer set `mkdocs.yml` excludes."""
    return _parts(path)[:2] == ("docs", "dev")


DESCRIPTION = re.compile(r"^description: (.+)$", re.MULTILINE)


def manifest_problems(config: str, pages: Path) -> list[str]:
    """Hold the llms.txt manifest of ``config`` to its nav, both ways, and to each description.

    `mkdocs.yml` names every user page twice: in `nav`, which builds the site, and in the
    `llmstxt` sections, which build `/llms.txt` for a reader that is not a browser. A page added
    to one alone is missing from the half no person opens; a summary is a copy of the page's
    `description:`, and a copy that moves apart describes a page the site no longer has.
    """
    head, marker, tail = config.partition("  - llmstxt:")
    if not marker:
        return ["mkdocs.yml: no llmstxt plugin"]
    nav = set(re.findall(r"^ +- [^:\n]+: ([A-Za-z0-9-]+\.md)$", head, re.MULTILINE))
    lines = dict(re.findall(r"^ +- ([A-Za-z0-9-]+\.md): (.+)$", tail, re.MULTILINE))
    if not nav or not lines:
        return ["mkdocs.yml: the nav or the llms.txt manifest reads as empty"]
    problems = [
        f"mkdocs.yml: {page} is in the {here} but not the {there}"
        for pages_named, here, there in (
            (nav - lines.keys(), "nav", "llms.txt manifest"),
            (lines.keys() - nav, "llms.txt manifest", "nav"),
        )
        for page in sorted(pages_named)
    ]
    for page, summary in sorted(lines.items()):
        source = pages / page
        described = (
            DESCRIPTION.search(source.read_text(encoding="utf-8")) if source.exists() else None
        )
        if described is None or described.group(1).strip() != summary.strip():
            problems.append(f"mkdocs.yml: the llms.txt line for {page} is not its description")
    return problems


def check_markdown(path: Path, text: str) -> list[str]:
    """Return one message per rendering problem or link problem in a page."""
    problems: list[str] = []
    in_fence = False
    for n, raw in enumerate(text.splitlines(), 1):
        if FENCE.match(raw):
            in_fence = not in_fence
            continue
        problems.extend(f"{n}: {why}" for why in link_problems(path, raw))
        if in_fence:
            continue
        line = strip_code_spans(raw)
        if line.count("$") % 2:
            problems.append(f"{n}: odd number of $ -- the whole line stops rendering")
        problems.extend(
            f"{n}: {why}\n    {raw.strip()[:100]}"
            for pattern, why in RULES
            if re.search(pattern, line)
        )
    return problems


def british_words(line: str) -> list[str]:
    """Give each British word of a line, a name read a word at a time."""
    return [found for found in WORD.findall(line) if BRITISH.fullmatch(found)]


def check_words(text: str, pattern: re.Pattern[str], why: str) -> list[str]:
    """Return one message per match of ``pattern`` in ``text``, by line."""
    return [
        f"{n}: {found.group(0)!r} {why}"
        for n, line in enumerate(text.splitlines(), 1)
        for found in pattern.finditer(line)
    ]


def check_spelling(text: str) -> list[str]:
    """Return one message per British word of ``text``."""
    return [
        f"{n}: spells {word!r} the British way; write the American form"
        for n, line in enumerate(text.splitlines(), 1)
        for word in british_words(line)
    ]


def python_prose(text: str) -> str:
    """Keep the comments and docstrings of a Python source, line for line, blanking the rest.

    A comment and a docstring are prose about the tree that no page carries, and they age
    as a page does. A source that does not parse has none to read; the suite reports it.
    """
    lines = text.splitlines()
    kept: dict[int, str] = {}
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                kept[token.start[0]] = token.string
        tree = ast.parse(text)
    except (SyntaxError, tokenize.TokenError):
        return ""
    documented = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if isinstance(node, documented) and ast.get_docstring(node) is not None:
            first = node.body[0]
            for n in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                kept[n] = lines[n - 1]
    return "\n".join(kept.get(n, "") for n in range(1, len(lines) + 1))


def prose(relative: str, text: str) -> str:
    """Return the part of a tracked file that is prose about the tree, line for line."""
    suffix = Path(relative).suffix
    if suffix == ".md":
        return text
    if suffix == ".py":
        return python_prose(text)
    if suffix in {".toml", ".yml", ".yaml"}:
        return "\n".join(
            line if line.lstrip().startswith("#") else "" for line in text.splitlines()
        )
    return ""


def check_file(relative: str, text: str) -> list[str]:
    """Return every problem one tracked file carries, its path among them."""
    problems: list[str] = []
    if relative not in SPELLERS:
        problems += [f" the path spells {w!r} the British way" for w in british_words(relative)]
        problems += check_spelling(text)
    if relative not in HISTORY_EXEMPT:
        problems += check_words(
            prose(relative, text),
            HISTORY,
            "states what this tree was; state the rule a reader applies instead",
        )
    if relative not in INTERNAL_EXEMPT:
        problems += check_words(
            text, INTERNAL, "names the untracked working area; state the fact itself"
        )
    if relative.endswith(".md"):
        problems += check_markdown(ROOT / relative, text)
    return [f"{relative}:{problem}" for problem in problems]


def citations(text: str) -> set[str]:
    """Return every citation key ``text`` writes, a key broken across lines included."""
    return set(CITATION.findall(BREAK.sub(" ", text)))


def check_citations(texts: dict[str, str]) -> list[str]:
    """Hold the citation keys of the tree and the entries of the references page equal."""
    listed = set(ENTRY.findall(texts.get(REFERENCES, "")))
    cited: dict[str, str] = {}
    for relative, text in sorted(texts.items()):
        if relative in SPELLERS or relative == REFERENCES:
            continue
        if relative.endswith((".md", ".py")):
            for key in sorted(citations(text)):
                cited.setdefault(key, relative)
    unlisted = [
        f"{relative}: cites {key!r}, which {REFERENCES} does not list"
        for key, relative in sorted(cited.items())
        if key not in listed
    ]
    uncited = [
        f"{REFERENCES}: lists {key!r}, which no tracked text cites"
        for key in sorted(listed - set(cited))
    ]
    return unlisted + uncited


def located(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """Return each citation ``text`` writes with the places after its key, none if it names none."""
    flat = BREAK.sub(" ", text)
    found = []
    for match in CITATION.finditer(flat):
        tail = PLACES.match(flat, match.end())
        found.append((match.group(), tuple(re.findall(ONE_PLACE, tail.group()) if tail else ())))
    return found


def _page_prose(text: str) -> str:
    """Return a page with its code spans blanked: an example of the form cites nothing."""
    return "\n".join(strip_code_spans(line) for line in text.splitlines())


def _package(texts: dict[str, str]) -> dict[str, str]:
    return {
        relative: text
        for relative, text in texts.items()
        if relative.startswith(f"{PACKAGE.name}/") and relative.endswith(".py")
    }


def items(module: str, text: str) -> dict[str, set[str]]:
    """Return each item of a package module the theory page may name, with the works it cites.

    An item is the module, as `cells.py`, or a class or a function in it, as
    `planner.py::_Search.solve`. It cites what its docstring cites, and what a comment inside it
    and in no item nested in it cites.
    """
    tree = ast.parse(text)
    found = {module: citations(ast.get_docstring(tree) or "")}
    spans: list[tuple[int, int, str]] = []

    def walk(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{owner}{child.name}"
                name = f"{module}::{qualified}"
                found[name] = citations(ast.get_docstring(child) or "")
                spans.append((child.lineno, child.end_lineno or child.lineno, name))
                walk(child, f"{qualified}.")

    walk(tree, "")
    blocks: list[tuple[int, int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type != tokenize.COMMENT:
            continue
        line = token.start[0]
        if blocks and blocks[-1][1] == line - 1:
            blocks[-1] = (blocks[-1][0], line, f"{blocks[-1][2]}\n{token.string}")
        else:
            blocks.append((line, line, token.string))
    for line, _, comment in blocks:
        owners = [name for start, end, name in spans if start <= line <= end]
        found[owners[-1] if owners else module] |= citations(comment)
    return found


@dataclass
class Claim:
    """A claim of the theory page: its tag and id, its works, and the items and tests it names."""

    tag: str
    ident: str
    cites: set[str]
    implemented: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)


IMPLEMENTED = "IMPLEMENTED-BY"
HELD = "HELD-BY"


def claims(page: str) -> list[Claim]:
    """Read the claims of the theory page, each with the lines that follow it."""
    found: list[Claim] = []
    prose: list[str] = []
    for paragraph in re.split(r"\n\s*\n", page):
        text = paragraph.strip()
        label, colon, names = text.partition(": ")
        if text.startswith("#"):
            prose = []
        elif colon and label in (IMPLEMENTED, HELD) and found:
            listed = [name.strip() for name in names.split(",") if name.strip()]
            (found[-1].implemented if label == IMPLEMENTED else found[-1].held).extend(listed)
        elif tag := TAG.search(text):
            body = _page_prose("\n".join([*prose, text]))
            found.append(Claim(tag[1], tag[2], citations(body)))
            prose = []
        else:
            prose.append(text)
    return found


def check_theory(texts: dict[str, str]) -> list[str]:
    """Hold the theory page and the package to each other, as `docs/dev/10-writing.md` says."""
    page = texts.get(THEORY)
    if page is None:
        return [f"{THEORY}: missing, so no claim names the code citing a result"]
    package = _package(texts)
    sources = {relative: python_prose(text) for relative, text in package.items()}
    sources[THEORY] = _page_prose(page)
    problems = [
        f"{relative}: cites {key!r} with no place in it"
        for relative, text in sorted(sources.items())
        for key, places in located(text)
        if not places
    ]
    cited: dict[str, set[str]] = {}
    for relative, text in package.items():
        cited.update(items(Path(relative).name, text))
    tests = {
        name
        for relative, text in texts.items()
        if relative.startswith("tests/") and relative.endswith(".py")
        for name in re.findall(r"^(?:async )?def (test_\w+)", text, re.MULTILINE)
    }
    read = claims(page)
    seen: set[str] = set()
    for claim in read:
        where = f"{THEORY}: [{claim.ident}]"
        if claim.ident in seen:
            problems.append(f"{where} is a second claim of that id")
        seen.add(claim.ident)
        problems += [
            f"{where} has no {line}: line"
            for line, names in ((IMPLEMENTED, claim.implemented), (HELD, claim.held))
            if not names
        ]
        for name in claim.implemented:
            if name not in cited:
                problems.append(f"{where} names {name!r}, which is no item of the package")
            elif not cited[name] & claim.cites:
                problems.append(f"{where} names {name!r}, which cites none of its works")
        problems += [
            f"{where} is held by {name!r}, which is no test"
            for name in claim.held
            if name not in tests
        ]
    for name, keys in sorted(cited.items()):
        naming = [claim for claim in read if name in claim.implemented]
        if keys and not naming:
            problems.append(f"{name}: cites {', '.join(sorted(keys))}, and no claim names it")
        problems += [
            f"{name}: cites {key!r}, which no claim naming it cites"
            for key in sorted(keys)
            if naming and not any(key in claim.cites for claim in naming)
        ]
    return problems


def copies(key: str, shelf: Path) -> list[Path]:
    """Return the shelf's renderings of the work a key names, by its first surname and year."""
    surname = unicodedata.normalize("NFKD", key.split(maxsplit=1)[0])
    folded = "".join(c for c in surname if c.isascii() and c.isalpha())
    return sorted(shelf.glob(f"*_{folded}{key[-4:]}_*.txt"))


def states(text: str, place: str) -> bool:
    """Answer whether a paper's text prints a place: the section's heading, or the statement."""
    part = PLACE.fullmatch(place)
    if part is None:
        return False
    if part["section"]:
        pattern = rf"^\s*{re.escape(part['section'])}\.?\s+\S"
    else:
        pattern = rf"^\s*{STATEMENT[part['form']]} {re.escape(part['number'])}(?!\.?[0-9])"
    return re.search(pattern, text, re.MULTILINE) is not None


def check_places(texts: dict[str, str], shelf: Path = SHELF) -> list[str]:
    """Refuse a place no copy of its work on the shelf prints; nothing where there is no shelf."""
    if not shelf.is_dir():
        return []
    sources = {relative: python_prose(text) for relative, text in _package(texts).items()}
    if THEORY in texts:
        sources[THEORY] = _page_prose(texts[THEORY])
    read: dict[Path, str] = {}
    problems = []
    for relative, text in sorted(sources.items()):
        for key, places in located(text):
            found = copies(key, shelf)
            if not found:
                problems.append(f"{relative}: {key!r} has no copy on the shelf to read it in")
                continue
            for copy in found:
                read.setdefault(copy, copy.read_text("utf-8", errors="replace"))
            problems += [
                f"{relative}: ({key}, {place}) is a place no copy of the work prints"
                for place in places
                if not any(states(read[copy], place) for copy in found)
            ]
    return problems


def git(*arguments: str, given: str | None = None) -> str:
    """Return what a git command prints, run at the root."""
    return subprocess.run(  # noqa: S603
        ["git", *arguments],  # noqa: S607
        input=given,
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout


def read(relative: str) -> str | None:
    """Return a tracked file's text, or ``None`` for a binary file, which spells nothing."""
    try:
        return (ROOT / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def check_tree(named: list[str]) -> list[str]:
    """Check the named files, or every tracked file and the citations across them."""
    texts = {
        relative: text
        for relative in named or git("ls-files").splitlines()
        if (text := read(relative)) is not None
    }
    problems = [p for relative, text in texts.items() for p in check_file(relative, text)]
    if not named:
        problems += check_citations(texts)
        problems += check_theory(texts)
        problems += check_places(texts)
        problems += manifest_problems((ROOT / "mkdocs.yml").read_text("utf-8"), ROOT / "docs")
    return problems


def scopes() -> frozenset[str]:
    """Return every scope a subject may name: each module's stem, and `SCOPES`."""
    return SCOPES | {path.stem for path in PACKAGE.glob("[!_]*.py")}


def check_subject(subject: str) -> list[str]:
    """Return what the subject line breaks of the commit form."""
    found = SUBJECT.fullmatch(subject)
    if found is None:
        return ["the subject is not `scope: what the change does`"]
    problems = []
    if found["scope"] not in scopes():
        problems.append(f"scope {found['scope']!r} is no module and none of SCOPES")
    if found["text"][0].isupper():
        problems.append("the subject starts with a capital; it is lowercase after the scope")
    if subject.endswith("."):
        problems.append("the subject ends in a period; it takes none")
    if len(subject) > SUBJECT_WIDTH:
        problems.append(f"the subject is {len(subject)} characters, past {SUBJECT_WIDTH}")
    return problems


def trailers(message: str) -> list[str]:
    """Return every trailer git parses in a message, and every attribution line in it."""
    parsed = git("interpret-trailers", "--parse", given=message).splitlines()
    return [f"carries the trailer {line!r}" for line in parsed] + [
        f"line {n}: carries the attribution {line.strip()!r}"
        for n, line in enumerate(message.splitlines(), 1)
        if ATTRIBUTION.match(line)
    ]


def check_message(message: str) -> list[str]:
    """Return what a commit message breaks of the form and of the rules every text keeps."""
    subject, _, body = message.strip("\n").partition("\n")
    problems = check_subject(subject)
    if body and not body.startswith("\n"):
        problems.append("no blank line after the subject")
    problems += [
        f"line {n}: {len(line)} columns, past {BODY_WIDTH}"
        for n, line in enumerate(body.splitlines(), 2)
        if len(line) > BODY_WIDTH and " " in line.strip()
    ]
    problems += trailers(message)
    words = (
        (HISTORY, "states what the tree was; state the property instead"),
        (INTERNAL, "names the untracked working area"),
        (META, "describes the change; state the system instead"),
    )
    problems += [f"line {p}" for p in check_spelling(message)]
    problems += [f"line {p}" for pattern, why in words for p in check_words(message, pattern, why)]
    return problems


def message_file(path: Path) -> str:
    """Read a message as git hands it to the hook: comment lines dropped, the cut honored."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# ------------------------ >8 ------------------------"):
            break
        if not line.startswith("#"):
            lines.append(line)
    return "\n".join(lines).strip("\n") + "\n"


def check_commits(rev: str) -> list[str]:
    """Check the message of every commit ``rev`` reaches."""
    fields = git("log", "--format=%h%x00%B%x00", rev).split("\0")
    return [
        f"{commit.strip()}: {problem}"
        for commit, message in zip(fields[0::2], fields[1::2], strict=False)
        for problem in check_message(message)
    ]


def main(argv: list[str]) -> int:
    """Run the mode the arguments name, print each problem, and count them."""
    parser = argparse.ArgumentParser(description="Hold every text the tree writes to its rules.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--commits", nargs="?", const="HEAD", metavar="REV")
    mode.add_argument("--commit-msg", type=Path, metavar="FILE")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    if args.commits:
        problems = check_commits(args.commits)
        scanned = f"the commits {args.commits} reaches"
    elif args.commit_msg:
        problems = [f"message: {p}" for p in check_message(message_file(args.commit_msg))]
        scanned = "one commit message"
    else:
        problems = check_tree(args.files)
        scanned = f"{len(args.files)} file(s)" if args.files else "every tracked file"
    for problem in problems:
        print(problem)
    print(f"\n{len(problems)} problem(s) in {scanned}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
