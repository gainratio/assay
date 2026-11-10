# Contributing to Assay

Contributions are welcome. Assay has two independent scoring packages that must produce identical numbers for the same inputs: Python (`src/assay/`) and TypeScript (`ts/src/`).

Start with [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) for a step-by-step walkthrough from fresh clone to first change, and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the request-to-result data flow.

## Local setup

Assay pins **Python 3.13** (managed by [`uv`](https://docs.astral.sh/uv/)), **Node 22.13.0**, and **pnpm 11.5.0** so local builds produce the exact same archive bytes as CI:

```bash
NODE22=$(npx --yes --package=node@22.13.0 -c 'command -v node')
COREPACK=$(npx --yes --package=corepack@0.34.0 -c 'command -v corepack')
TOOLS="${TMPDIR:-/tmp}/assay-tools"
npm install --silent --prefix "$TOOLS" pnpm@11.5.0
export PATH="$TOOLS/node_modules/.bin:$(dirname "$NODE22"):$(dirname "$COREPACK"):$PATH"

uv sync --all-extras
pnpm --dir ts install --frozen-lockfile
```

## Checks to run before opening a pull request

Run both language checks and the built-artifact parity script:

```bash
uv run poe gate
(cd ts && pnpm gate)
bash examples/run_composite.sh
```

If your change touches validation, normalization, combiners, or release guards, also run the mutation harness:

```bash
uv run poe mutants
```

## Working rules

- **Test-first (`red -> green -> refactor`).** Start every behavior change with a failing test in `tests/` and/or `ts/src/*.test.ts`.
- **Cross-language parity.** Shared scoring behavior lives in `src/assay/`, `ts/src/`, and the shared vectors under `testdata/vectors/`. Keep both runtimes aligned on IEEE-754 binary64 arithmetic, `+0` canonicalization, input-order hashing, and stable `assay.*` error codes.
- **Strict quality floors.** Keep Python and TypeScript scoring modules above 90% branch, line, and function coverage with strict typing and Grade-A complexity.
- **Scoring-only scope.** Assay performs deterministic calculation with zero network calls and zero persistence.
- **Security issues.** Please report vulnerabilities privately per [`SECURITY.md`](SECURITY.md) rather than opening a public issue.
