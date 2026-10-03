# Assay scoring engine

Assay turns measurements on different scales into one explained score. This repository contains
only scoring code; signed evidence and receipts belong to the separate Avow project.

## Commands

```bash
uv sync --all-extras
uv run poe gate
uv run poe mutants
```

```bash
cd ts
NODE22=$(npx --yes --package=node@22.13.0 -c 'command -v node')
COREPACK=$(npx --yes --package=corepack@0.34.0 -c 'command -v corepack')
PATH="$(dirname "$NODE22"):$(dirname "$COREPACK"):$PATH"
node --version
corepack pnpm --version
corepack pnpm install --frozen-lockfile
corepack pnpm gate
mkdir -p "${TMPDIR:-/tmp}/assay-pack"
corepack pnpm pack --pack-destination "${TMPDIR:-/tmp}/assay-pack"
node scripts/normalize-package-archive.mjs \
  "${TMPDIR:-/tmp}/assay-pack/gainratio-assay-0.5.0-dev.5.tgz"
```

These commands select Node 22.13.0 and pnpm 11.5.0. The version lines must print
`v22.13.0` and `11.5.0`; ambient tools can produce a different archive.

The local release and security equivalents are executable through the task runner:

```bash
uv run poe release-candidate
uv run poe audit
uv run poe secrets
uv run poe workflow-lint
uv run poe workflow-security
uv run poe benchmark
```

## Layout

- `src/assay/` — Python contracts, normalization, combiners, metrics, and CLI.
- `ts/src/` — dependency-free TypeScript scoring package and tests.
- `testdata/vectors/` — shared Python/TypeScript parity fixtures.
- `tests/` — Python behavior, boundary, packaging, and workflow tests.
- `scripts/mutation_harness.py` — breaks guards deliberately and requires their tests to fail.

## Contracts

- Public inputs are finite IEEE-754 binary64 numbers. Both runtimes canonicalize zero to `+0`.
- Hashes encode every number as big-endian binary64 bits and preserve declared input order.
- `weighted_mean`, `additive`, and `minimum` are separate methods. Additive arithmetic is strictly
  left-to-right; minimum ties select the first declared component.
- `assay.composite` is a Python-only legacy deep import for migration. New code uses the typed
  package-root `compose`; its result has no method or `inputs_hash` and no TypeScript peer.
- Unknown inputs and serialized results are parsed strictly. Errors use stable, value-free
  `assay.*` codes.
- Keep scoring independent of signing, evidence, receipts, ledgers, persistence, and network I/O.

## Workflow

- Use red -> green -> refactor. A behavior change starts with a failing test.
- Dagger owns quality, parity, mutation, examples, benchmarks, exact artifacts, dependency audits,
  workflow security, and snapshot plus full-history secret scans. GitHub only admits pinned events.
- Run both complete language gates after shared-contract changes.
- Keep TypeScript scoring modules above 90% branch, function, and line coverage per file.
- `@gainratio/assay@0.5.0-dev.5` is the authorized prerelease identity. Do not publish, tag, or
  change release settings without explicit approval.
