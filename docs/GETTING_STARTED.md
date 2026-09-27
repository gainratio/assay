# Getting started for developers

This guide takes you from a fresh clone to a green local build and your first change.
Assay has two packages that must agree: Python in `src/assay/` and TypeScript in `ts/src/`.

## 1. Prerequisites

| Tool | Version | How to get it |
| --- | --- | --- |
| Python | 3.13 | Installed by `uv` if missing |
| [uv](https://docs.astral.sh/uv/) | any recent | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js | exactly 22.13.0 | Fetched by `npx` below, no global install needed |
| pnpm | exactly 11.5.0 | Installed into a temp folder below |
| Git | any | Your OS package manager |

The exact Node and pnpm versions matter: the tests compare the built npm package against a
pinned SHA-256, and another version can produce different bytes.

Local traps we hit:

- **A broken Corepack `pnpm` shim.** If `pnpm --version` fails with
  `Cannot find module .../corepack/pnpm/<version>/bin/pnpm.cjs`, your global Corepack cache
  is stale. The setup below puts a real pnpm 11.5.0 first on your `PATH`, which avoids it.
  One test, `test_should_build_the_same_three_reviewed_artifacts_locally`, prefers a Node 22
  from `~/.nvm` if you have one, and that Node's Corepack can hit the same error. We saw
  this on a clean `main` too. CI does not use `~/.nvm`, so it only affects local runs.
- **The Python tests need the TypeScript side too.** Several Python tests type-check the
  docs' TypeScript examples and build the npm package, so `pnpm` must be on your `PATH`
  and `ts/node_modules` must be installed before `uv run poe gate`. Without them you get
  hundreds of failures that look unrelated.
- **Don't share a scratch folder between clones.** Some tests build into temporary
  folders. Give each clone its own directory name.

## 2. Clone and install

```bash
git clone https://github.com/hseshadr/assay
cd assay
NODE22=$(npx --yes --package=node@22.13.0 -c 'command -v node')
COREPACK=$(npx --yes --package=corepack@0.34.0 -c 'command -v corepack')
TOOLS="${TMPDIR:-/tmp}/assay-tools"
npm install --silent --prefix "$TOOLS" pnpm@11.5.0
export PATH="$TOOLS/node_modules/.bin:$(dirname "$NODE22"):$(dirname "$COREPACK"):$PATH"
node --version
pnpm --version
uv sync --all-extras
pnpm --dir ts install --frozen-lockfile
```

Success looks like `v22.13.0` and `11.5.0` from the two version lines, and no errors from
the installs. This took about 15 seconds with warm caches. The `export PATH` line only
lasts for the current terminal, so run the lines from `NODE22=` to `export` again in
each new terminal.

## 3. Run the full check

```bash
uv run poe gate
(cd ts && pnpm gate)
```

The first command is the Python check: lint, formatting, strict types, a complexity
limit (every function Grade A), and all Python tests with at least 90% branch coverage.
It also builds both packages and checks the docs' examples. The second is the TypeScript
check: Biome lint, strict types, tests with coverage, and the build. Success is a coverage
summary and exit code 0 from each.

Timing on a MacBook from a fresh clone: the Python check took about 8.5 minutes (847 tests),
and the TypeScript check about 40 seconds.

CI runs the same checks, plus mutation tests, dependency audits, workflow checks, and
secret scans, inside [Dagger](https://dagger.io) with `dagger call ci`. You can also run
the mutation tests locally with `uv run poe mutants`; each one breaks a guard on purpose
and requires a test to fail.

## 4. Map of the code

| Path | What it is |
| --- | --- |
| `src/assay/contracts.py` | Request and result types, and the strict parser (`parse_request`). |
| `src/assay/compose.py` | Picks the method and builds the explained result. |
| `src/assay/weighted_mean.py`, `additive.py`, `minimum.py` | The three ways to combine measurements. |
| `src/assay/normalize.py` | Puts a value on its 0 to 1 scale. |
| `src/assay/errors.py` | The stable `assay.*` error codes. |
| `src/assay/cli.py`, `_cli_app.py`, `_cli_io.py` | The `assay` command (needs the `cli` extra). |
| `src/assay/measurement.py`, `ranking.py`, `calibration.py`, `agreement.py` | Optional calculators (need the `metrics` extra). |
| `ts/src/` | The TypeScript package. Most modules sit next to a `*.test.ts` file. |
| `testdata/vectors/` | Shared test cases. Both languages replay them and must get the same numbers. |
| `tests/` | Python tests, plus tests that pin the docs, the packages, and the workflows. |
| `scripts/mutation_harness.py` | The mutation tests behind `uv run poe mutants`. |
| `.dagger/src/assay_dagger/main.py` | The CI pipeline. |

## 5. Make your first change

Say you want a clearer rule for how a value is placed on its scale. The work is always
test first.

1. Write the failing Python test in `tests/test_normalize.py`, next to the existing
   cases, and run just that file:

   ```bash
   uv run pytest tests/test_normalize.py -q --no-cov
   ```

   A clean run of this file prints `36 passed` in under a second. Your new test should
   fail, for the reason you expect.

2. Change `src/assay/normalize.py` until the test passes.

3. Make the same change in TypeScript. Add the test to `ts/src/normalize.test.ts`, change
   `ts/src/normalize.ts`, and run just that file:

   ```bash
   cd ts
   pnpm exec vitest run src/normalize.test.ts
   ```

4. If the change affects results that both languages produce, add a case to the matching
   file in `testdata/vectors/` so both test suites replay it.

5. Run the full check from section 3. If you changed a public example, update the doc that
   shows it; the doc tests run or parse every example, and compare the README output.

## 6. Open a pull request

- Branch from `main` with a short prefix that says what kind of change it is:
  `fix/...`, `docs/...`, `ci/...`, `refactor/...`, or `feat/...`.
- Commit messages follow the same style: `fix(ci): ...`, `docs: ...`.
- Push and open a PR against `main`. CI runs one job, **Dagger**, which runs both checks
  above plus the extra checks listed in section 3. It must be green before merge.
- Reviewers look for: a test that failed before your change, the same behavior in Python
  and TypeScript, docs that still match the code, and no new runtime dependencies in the
  core packages.
- Report security problems privately, as described in [SECURITY.md](../SECURITY.md).
