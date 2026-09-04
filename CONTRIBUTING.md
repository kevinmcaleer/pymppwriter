# Contributing

Format discoveries are the most valuable contribution. When you find out what a byte does:

1. Verify it two ways (e.g. Project rejects/accepts the file *and* a Project-resaved file shows it).
2. Add it to `docs/FORMAT_NOTES.md`.
3. Open an issue with the **Format finding** template linking the note and the test.

Never commit `.mpp` files written by Microsoft Project or taken from another project's test suite.
Never derive code from decompiled proprietary binaries.

## How to find out what a byte does

These methods, in increasing order of power, produced every finding in
`docs/FORMAT_NOTES.md`. All the tools live in `scripts/`.

1. **Byte-diff Project-written references.** Real `.mpp` files you own are a
   corpus of valid encodings. `scripts/analyze_mpp.py` dumps task records
   field-by-field; `scripts/diff_mpp_tasks.py` and `scripts/diff_mpp.py`
   compare two files through their own field maps.
2. **Probe MPXJ's parser with candidate bytes.** MPXJ's observable read
   behaviour is a fast oracle: write a guess into a copy of the template, read
   it back with `scripts/mpxj_oracle.py`, iterate. This is how the calendar
   day-block layout was decoded without any reference sample. *Caveat:* MPXJ
   reads leniently — MPXJ agreement does **not** prove Project will accept the
   bytes (Project silently ignored two whole calendar dialects MPXJ read
   happily). Always finish with a check in Project itself.
3. **The resave experiment.** Open a generated file in Microsoft Project,
   change *one thing* by hand, Save As, and diff the two files with
   `scripts/diff_mpp.py`. Whatever Project rewrote is the encoding of that one
   thing. This found the planned-work contour, the calendar meta flags and the
   Var2Data length gates.
4. **Stream-transplant bisection.** When a generated file misbehaves and a
   Project-saved one works, transplant streams between them (`load_cfb` /
   `write_cfb` make this a ten-line script) and test each hybrid in Project.
   Each round halves the search space; three hybrids once isolated a
   one-dword Props gate from a ~100 KB haystack.

Check your changes against the golden round-trip: generate a file, resave it
in Project, and run `scripts/roundtrip_check.py generated.mpp resaved.mpp`.

## Releasing to PyPI

`.github/workflows/publish.yml` publishes when a GitHub Release is published.
It uses **Trusted Publishing**, so no API token is stored in this repository —
PyPI accepts a short-lived token minted for this workflow instead.

### One-time setup (on PyPI, not here)

1. Sign in to PyPI → **Your projects** → **Publishing** (for a name that has
   never been published, use **Add a pending publisher**).
2. Fill in:

   | Field | Value |
   |---|---|
   | PyPI project name | `pymppwriter` |
   | Owner | `kevinmcaleer` |
   | Repository name | `pymppwriter` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

3. Repeat on [test.pypi.org](https://test.pypi.org) if you want the dry run.

The environment name has to match the `environment: pypi` in the workflow, or
PyPI rejects the token.

### Cutting a release

1. Bump `version` in `pyproject.toml` (the workflow fails if it does not match
   the tag, so a forgotten bump cannot ship).
2. Commit, then tag and push:

   ```bash
   git tag v0.4.0 && git push origin v0.4.0
   ```

3. Publish a GitHub Release for that tag. The workflow runs the tests, builds
   the sdist and wheel, `twine check`s them and uploads.

Dry run first with **Actions → publish → Run workflow → testpypi**, then

```bash
pip install --index-url https://test.pypi.org/simple/ pymppwriter
```

## Releasing to npm

The repo ships two packages, and the tag prefix decides which one a release
publishes:

| tag | package | index |
|---|---|---|
| `v0.3.1` | `pymppwriter` (Python) | PyPI |
| `js-v0.1.0` | `mppwriter` (`js/`) | npm |

`.github/workflows/npm-publish.yml` uses **npm Trusted Publishing**, so no token
lives here either, and the published package carries provenance.

### One-time setup (on npmjs.com)

1. Sign in → the `mppwriter` package → **Settings** → **Trusted publisher**. For
   a name never published, publish once manually or add the publisher when the
   package is created.
2. Fill in:

   | Field | Value |
   |---|---|
   | Organization or user | `kevinmcaleer` |
   | Repository | `pymppwriter` |
   | Workflow filename | `npm-publish.yml` |

### Cutting a release

1. Bump `version` in `js/package.json` (the workflow fails on a mismatch).
2. Tag and push, then publish the GitHub Release for that tag:

   ```bash
   git tag js-v0.1.0 && git push origin js-v0.1.0
   ```

**Actions → npm-publish → Run workflow** does a dry run: it installs, typechecks,
tests, builds and prints `npm pack --dry-run` without publishing.

### Consuming it

Once a version is on PyPI, dependants pin the release rather than a git URL —
which is what lets a Docker build install it from the index and cache the
layer. In NoodlePlanner, `packages/noodle-core/pyproject.toml`:

```toml
mpp = ["pymppwriter>=0.3"]
```

then `uv lock --upgrade-package pymppwriter`.
