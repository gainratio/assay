# Assay quickstart

> **TL;DR:** From a clean checkout, one command builds both package artifacts,
> computes the committed Northstar score, and proves Python/TypeScript semantic parity.

Requires Python 3.13, `uv`, Node 22.13 or newer, and Corepack. Neither package is
required from a registry; all commands below build from the current checkout.

## First result

```bash
bash examples/run_composite.sh
```

Expected first and last lines:

```text
Northstar weighted score: 0.92
Parity: Python and TypeScript fields and values match
```

The displayed relative command runs from the checkout root. The script itself resolves
the repository from its own path, so an absolute path such as
`bash /path/to/assay/examples/run_composite.sh` also works elsewhere. It builds the real
wheel and npm tarball, installs them outside the checkout, compares both typed results
with the committed oracle, and removes its temporary files on success or failure.

## Python checkout

Install the local project with the command-line adapter:

```bash
uv sync --extra cli
uv run assay compose --request examples/northstar_score.json
```

Add the scientific calculators only when you need `assay measure`:

```bash
uv sync --extra cli --extra metrics
uv run assay --help
```

The three commands are:

- `assay compose --request REQUEST [--out RESULT]` validates and combines a weighted
  mean, additive, or minimum request.
- `assay measure --request REQUEST [--out REPORT]` runs one typed optional binary,
  ranking, or agreement report.
- `assay explain --result RESULT [--out TEXT]` validates a serialized composition
  result, replays its invariants, and renders the arithmetic.

When `--out` is absent, JSON or text goes to standard output. A requested output is
installed atomically. Inputs must be bounded regular files and cannot alias their
outputs.

## TypeScript checkout

Install the pinned development toolchain, run its complete gate, then build the local
tarball:

```bash
cd ts
NODE22=$(npx --yes --package=node@22.13.0 -c 'command -v node')
COREPACK=$(npx --yes --package=corepack@0.34.0 -c 'command -v corepack')
export PATH="$(dirname "$NODE22"):$(dirname "$COREPACK"):$PATH"
node --version
corepack pnpm --version
corepack pnpm install --frozen-lockfile
corepack pnpm gate
mkdir -p "${TMPDIR:-/tmp}/assay-pack"
corepack pnpm pack --pack-destination "${TMPDIR:-/tmp}/assay-pack"
node scripts/normalize-package-archive.mjs \
  "${TMPDIR:-/tmp}/assay-pack/gainratio-assay-0.5.0-dev.4.tgz"
```

The two version lines must print `v22.13.0` and `11.5.0`.

Install that tarball into a Node 22 application. The package root exports
`parseRequest()` and `compose()`:

```typescript
import { compose, parseRequest } from "@gainratio/assay";

const request = parseRequest({
  method: "additive",
  method_version: "ranking.v1",
  terms: [
    {
      id: "relevance",
      label: "Relevance",
      value: 0.8,
      coefficient: 1,
      operation: "add",
      interval: null,
    },
  ],
  clamp: null,
  intercept: 0,
});

console.log(compose(request).score);
```

## Repository gates

Run the complete Python and TypeScript gates independently:

```bash
uv run poe gate
npx --yes --package=node@22.13.0 --package=corepack@0.34.0 \
  -c 'cd ts && corepack pnpm gate'
```

The Python gate covers formatting, lint, strict types, Grade A complexity, branch
coverage, and behavior. The TypeScript gate covers Biome, strict type checking, branch
coverage, behavior, and its production build.

Read [Methods](docs/METHODS.md) for exact arithmetic and fields,
[Architecture](docs/ARCHITECTURE.md) for package boundaries, and
[Operations](docs/OPERATIONS.md) before handling sensitive inputs.

## Registry identity

> **Status:** `assay-engine` 0.5.0.dev4 and `@gainratio/assay` 0.5.0-dev.4 are the authorized prerelease pair. Check both registries before installing.

After both registries list the exact versions, consumers can use
`pip install assay-engine==0.5.0.dev4` for Python and
`npm install @gainratio/assay@0.5.0-dev.4` for TypeScript. The checkout paths above do
not depend on registry state.
