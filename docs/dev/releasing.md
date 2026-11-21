# Releasing

How a version reaches PyPI: where it is declared, how it is built, what stands between a
dispatch and an upload, the release order it is part of, what the repository and the indexes
hold for it, the order of the steps, and the checks on what an index serves. `.github/workflows/release.yml` owns the build, the tests
of the built artifacts and the upload; this page owns the order and the checks outside it.

## The version is declared once

`version` in `pyproject.toml` is the only declaration, and `uv.lock` records it for the project,
so a bump runs `uv lock`. The wheel's file name and its metadata carry what `pyproject.toml`
says.

## The build runs locked code

The build job makes the files the publish job uploads, so every Python package it runs comes
from `uv.lock`, each hash required: the backend `[build-system]` names is the group `build`, and
the job passes that group's export to `uv build` as build constraints, with `--require-hashes`.
A backend release reaches the artifacts only through a lock update, which pip-audit reads as it
reads every group. twine, which resolves fresh, checks the metadata in a job of its own, on
copies of the artifacts. hatchling fixes every timestamp, and two builds of one commit give the
same bytes.

## Both dependencies come from PyPI

valgebra and valgebra-jsonschema are each required from the release the studies were read on,
with no ceiling. A ceiling cannot be lifted from a published version, so a capped package would
owe a release of its own to every release of the two, and no resolver would install them
together before that release. A user installs both from PyPI, and the smoke resolves them from
PyPI as a user does, at their newest releases and at the floors `pyproject.toml` names.

A release commit's `pyproject.toml` names no `[tool.uv.sources]`: a source there holds the lock
and the push lanes to a build no user installs, while the smoke reads PyPI's.

## The release order

No package caps the one before it, so a valgebra release installs beside valgebra-jsonschema and
this package the day it is published, and asks no release of either. A release that needs what
an upstream release adds follows it, in this order:

1. valgebra's cut reaches PyPI.
2. valgebra-jsonschema moves its floor to it where it needs it, relocks, reruns its studies and
   releases.
3. This package moves the floors it needs, relocks, reruns its studies and releases.
4. Each consumer moves its pins: valplan relocks, fishtest re-pins.

The `Upstream` workflow of valgebra-jsonschema and of this repository runs each suite and study
on the `main` of what it depends on, each Monday and on a dispatch, so a change that moves a
count shows before valgebra's cut, where a user installing the newest releases would meet it.

## Publishing is a dispatch, and a tag publishes nothing

`release.yml` runs on `workflow_dispatch` alone. Its `publish_target` input selects the index,
`none`, `testpypi` or `pypi`, and `none` builds and tests the artifacts without uploading them:
the dry run, which a dispatch from any branch can make. No workflow listens for a tag; the tag
records a release that happened.

Six conditions stand between a dispatch and an upload, each a job, a step or a job condition in
`release.yml`:

- **The metadata reads as the index reads it.** `twine check --strict` passes on both artifacts.
- **The smoke passes.** Each artifact is installed in a fresh environment, its dependencies
  resolved from PyPI. The wheel installs on every CPython the classifiers name, 3.11 through
  3.15, on x86_64 Linux, Apple silicon macOS and x64 Windows: an install pulls the valgebra
  wheel of its interpreter and platform, and each of those is a build of its own. The sdist
  installs on 3.11 and 3.15 on Linux, and the wheel installs on 3.11 and 3.15 once more with
  each dependency at the floor `pyproject.toml` names (`--resolution lowest-direct`), where
  every other leg installs the newest release each admits. Without the `mcp` extra, every module
  but `servers` and `proxy` imports with every warning an error, from `site-packages`, and the
  command line answers `--help`; with it, every module imports. The suite the sdist carries runs
  on each with the group `test` at the lock's versions, so a file the suite reads and the sdist
  leaves out fails there. A version cannot be replaced on an index once uploaded, only yanked,
  so a broken artifact has to fail before the upload.
- **`confirm_version` equals the built version**, and an empty input aborts.
- **The ref is `main`**, and each environment admits `main` alone
  ([below](#what-the-repository-and-the-indexes-hold)).
- **`ci.yml` passed on the commit.** A push run of it on `main`, at this very commit,
  concluded in success; a failed, canceled or unfinished run, none at all, or an API that does
  not answer refuses.
- **The version is absent from the index.** Only a definitive 404 for it proceeds: a 200 is a
  re-publish, and any other answer leaves the question open. A version the index names as its
  latest is refused as well.

The upload is trusted publishing: PyPI and TestPyPI each hold a publisher naming this repository,
`release.yml` and the deployment environment named after the index, and the publish job runs in
that environment, so no token is stored anywhere. Each upload carries PEP 740 attestations,
signed with the same token.

## What the repository and the indexes hold

The condition on the ref is a line of `release.yml`, and a dispatch from a branch runs that
branch's copy of the file, which can drop it. Each environment therefore admits a deployment from
`main` alone, a rule of the repository's settings that no branch edits, and the publisher on the
index names the environment, so the rule holds for every copy of the workflow:

```bash
for env in testpypi pypi; do
  gh api -X PUT repos/ppigazzini/valgebra-planning/environments/$env \
    -F 'deployment_branch_policy[protected_branches]=false' \
    -F 'deployment_branch_policy[custom_branch_policies]=true'
  gh api -X POST repos/ppigazzini/valgebra-planning/environments/$env/deployment-branch-policies \
    -f name=main -f type=branch
done
```

The first upload of the name creates the project from a pending publisher, entered on each index
under the account's publishing settings, with the same four fields: the project name, the owner
and repository, `release.yml` and the environment.

## The order

1. **Move the floors where the release needs them**, each to a release on PyPI: the floors in
   `pyproject.toml`, the lock and every study move in a commit of their own, with [the gate](../../AGENTS.md#the-gate)
   green, before the bump.
2. **Bump, in one commit.** `version` in `pyproject.toml`, `uv lock`, and the changelog: the
   `Unreleased` entries roll into a section for the version, dated the day of the commit, with its
   compare and tag links. It lands on `main`, and the push runs `ci.yml` on the tree about to
   be published; the dispatches wait until it passes. The run's `api` job lists each public name
   removed or changed since the last tag and fails on one no entry under Changed or Removed
   names, so the bump commit records each before any dispatch:
   `uv run --no-sync python tools/api_changes.py` runs it locally.
3. **Dispatch to TestPyPI**, with the version confirmed:

   ```bash
   gh workflow run release.yml -f publish_target=testpypi -f confirm_version=X.Y.Z
   ```

4. **Check what TestPyPI serves** ([below](#checking-an-index)).
5. **Dispatch to PyPI**, `-f publish_target=pypi`, the same version.
6. **Check what PyPI serves.**
7. **Tag the published commit**, annotated:

   ```bash
   git tag -a vX.Y.Z -m "valgebra-planning X.Y.Z" <commit>
   git push origin vX.Y.Z
   ```

## Checking an index

The smoke proves each artifact. It cannot prove what the index serves: the resolution, the file an
installer picks and the metadata it reads all come after the upload, and steps 4 and 6 are the only
ones that read them.

TestPyPI serves whatever anyone uploaded under a name, so only this package comes from it, without
its dependencies, and those come from PyPI at the lock's versions, the `mcp` extra's among them, in
an install of their own. From the repository root:

```bash
uv venv /tmp/vp
uv export --locked --no-dev --extra mcp --no-emit-project -o /tmp/vp-deps.txt
VIRTUAL_ENV=/tmp/vp uv pip install -r /tmp/vp-deps.txt
VIRTUAL_ENV=/tmp/vp uv pip install --no-deps --index-url https://test.pypi.org/simple/ "valgebra-planning[mcp]==X.Y.Z"
VIRTUAL_ENV=/tmp/vp uv pip check
```

`uv pip check` holds what was installed to the uploaded metadata's requirements. From PyPI the
install is the user's, `VIRTUAL_ENV=/tmp/vp uv pip install "valgebra-planning[mcp]==X.Y.Z"`, in a
fresh environment. Then the import, the command line and the suite, with the test group at the
lock's versions, all from outside the tree:

```bash
uv export --locked --only-group test --no-emit-project -o /tmp/vp-test.txt
VIRTUAL_ENV=/tmp/vp uv pip install -r /tmp/vp-test.txt
tree="$PWD"; cd /tmp
/tmp/vp/bin/python -W error -c 'import valgebra_planning as m, valgebra_planning.servers, valgebra_planning.verify; assert "site-packages" in m.__file__, m.__file__'
/tmp/vp/bin/valgebra-planning --help
/tmp/vp/bin/pytest -p no:cacheprovider "$tree/tests"
cd "$tree"
```

`python -c` and `python -m pytest` put the working directory first on the path, and from the
repository root either imports the checkout's package in place of the installed one; so does each
command line test, which starts the command line with `python -c` in a process of its own. The
import asserts where it read the package from, and the suite runs through the environment's
`pytest` script, which puts no directory of the tree on the path.

**The layout stays flat, as valgebra-jsonschema's is.** A src layout keeps a test from importing
the checkout in place of the installed package, which the paragraph above already rules out for
every check of an artifact. It would move every path of the tree for no check left open, and set
this package's layout apart from the companion released beside it.

**The simple index lags the JSON API, and the simple index is what an installer reads.** A
same-day check passes `--refresh-package valgebra-planning`, since uv caches an index's answers,
and "no such version" is read against the index itself before it is believed:

```bash
curl -sS https://test.pypi.org/simple/valgebra-planning/ \
  -H "Accept: application/vnd.pypi.simple.v1+json" \
  | python3 -c "import json, sys; print(json.load(sys.stdin)['versions'])"
```

## What this does not cover

- **The push lanes past Linux, 3.11 and 3.15.** CPython 3.12 to 3.14, macOS and Windows are
  read by the smoke alone, when a release is dispatched.
- **Free-threaded CPython and PyPy.** No job runs them and no classifier claims them.
- **valgebra's other wheels.** Linux on aarch64 and musl, macOS on x86_64 and Windows on arm64
  run no leg here: valgebra's own release smoke installs each of those wheels on its platform and
  runs valgebra's suite on it.
- **The floors between releases.** The smoke's floor legs install them when a release is
  dispatched; the push lanes run the lock's versions, which a lock update can move past them.
- **A published version that is wrong.** It cannot be replaced, only yanked, and the workflow
  refuses a version the index serves. The remedy is the next version, which is why step 4 comes
  before step 5.
