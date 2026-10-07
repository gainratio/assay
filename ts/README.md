# Assay for TypeScript

> **TL;DR:** `@gainratio/assay` validates explicit scoring requests, combines them with
> one of three methods, and returns every ordered contribution.

> **Status:** `@gainratio/assay` 0.5.0-dev.6 is the authorized prerelease. Check npm before installing.

The package is dependency-free, ESM-only, and requires Node 22.13 or newer. After npm
lists this exact version, install it with `npm install @gainratio/assay@0.5.0-dev.6`.
The checkout build below works regardless of registry state.

## Build the tarball

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
  "${TMPDIR:-/tmp}/assay-pack/gainratio-assay-0.5.0-dev.6.tgz"
```

The version lines must print `v22.13.0` and `11.5.0`. The final command produces
`gainratio-assay-0.5.0-dev.6.tgz` under `${TMPDIR:-/tmp}/assay-pack`. Install that file
in a separate Node 22 application, then import only from the package root.

## Compose a typed score

```typescript
import { compose, parseRequest } from "@gainratio/assay";

const request = parseRequest({
  method: "weighted_mean",
  method_version: "quality.v1",
  components: [
    {
      id: "quality",
      label: "Quality",
      value: 8,
      scale: { minimum: 0, maximum: 10, direction: "higher_is_better" },
      interval: null,
      weight: 3,
    },
    {
      id: "latency",
      label: "Latency",
      value: 20,
      scale: { minimum: 0, maximum: 100, direction: "lower_is_better" },
      interval: null,
      weight: 1,
    },
  ],
  clamp: "reject",
});

const result = compose(request);
console.log(result.score); // 0.8
```

`parseRequest()` accepts `unknown`, rejects extra fields and invalid values, and returns
the closed `ScoreRequest` union. `compose()` dispatches only `weighted_mean`, `additive`,
or `minimum`. See the repository's
[method reference](https://github.com/gainratio/assay/blob/main/docs/METHODS.md) for every
request rule and result field.

## Parity boundary

The shared vectors require Python and TypeScript to agree on typed field/value
structure, field and component order, finite IEEE-754 binary64 values, the three
composition methods, and exact `inputs_hash` values. They do not require byte-identical
language-native JSON serialization.

The TypeScript package also exposes small binary and ranking calculators. Python's
optional metric surface is broader; complete optional-metric parity is not claimed.
Application bands, thresholds, hard gates, and decisions remain outside this package.

Return to the checkout root and run the repository-wide realistic parity demo:

```bash
bash examples/run_composite.sh
```

## Optional integration

Assay computes scores; Avow seals evidence. They are separate products and neither requires the other.
