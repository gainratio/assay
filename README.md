# Assay

A Python and TypeScript library that combines several measurements into one score and shows exactly how it got there.

**Try it: `pip install assay-engine`** (Python 3.13 or newer), then run the example below.

[![CI](https://github.com/hseshadr/assay/actions/workflows/dagger.yml/badge.svg)](https://github.com/hseshadr/assay/actions/workflows/dagger.yml)
[![License](https://img.shields.io/github/license/hseshadr/assay)](https://github.com/hseshadr/assay/blob/main/LICENSE)
[![Version](https://img.shields.io/github/v/tag/hseshadr/assay?include_prereleases&sort=semver&label=version)](https://github.com/hseshadr/assay/blob/main/CHANGELOG.md)

Lots of apps rank or grade things: laptops, vendors, job applicants, software releases.
The score usually comes from a few lines of arithmetic that mix different units (hours,
dollars, kilograms), where sometimes lower is better. When someone asks "why did this one
get 0.66?", the answer is buried in code, and if the same score is also computed in a
second language, the two versions slowly drift apart.

Assay does that arithmetic for you. You declare each measurement with its own scale and
importance, pick one of three ways to combine them, and get back the score plus every
step of the math: each measurement's place on its scale, its share, and what it added.
The Python and TypeScript packages give the same numbers for the same input. It is a
plain calculation library. It makes no network calls and keeps nothing.

**Technical docs:** [Architecture](https://github.com/hseshadr/assay/blob/main/docs/ARCHITECTURE.md) · [Getting started for developers](https://github.com/hseshadr/assay/blob/main/docs/GETTING_STARTED.md) · [Methods and result fields](https://github.com/hseshadr/assay/blob/main/docs/METHODS.md) · [Operations and limits](https://github.com/hseshadr/assay/blob/main/docs/OPERATIONS.md)

## Try it

1. Install the Python package (Python 3.13 or newer):

```bash
pip install assay-engine
```

2. Save this as `laptop.py` and run `python laptop.py`. It scores a laptop on battery
   life (higher is better), price and weight (lower is better). Battery and price count
   twice as much as weight.

```python
from assay import compose, parse_request

def measure(name, value, worst, best, importance):
    direction = "higher_is_better" if best > worst else "lower_is_better"
    scale = {"minimum": min(worst, best), "maximum": max(worst, best), "direction": direction}
    return {"id": name, "label": name, "value": value, "scale": scale, "interval": None, "weight": importance}

laptop = [measure("battery_hours", 10, worst=0, best=20, importance=2),
          measure("price_usd", 900, worst=2400, best=400, importance=2),
          measure("weight_kg", 1.4, worst=3.0, best=1.0, importance=1)]
result = compose(parse_request({"method": "weighted_mean", "method_version": "laptop.v1",
                                "clamp": "reject", "components": laptop}))
for row in result.components:
    print(f"{row.id:<14}{row.raw:>5g} -> {row.normalized:.2f} of best × {row.coefficient:.0%} share = {row.contribution:.2f}")
print(f"score: {result.score:.2f} out of 1")
print(result.inputs_hash)
```

Real output:

```text
battery_hours    10 -> 0.50 of best × 40% share = 0.20
price_usd       900 -> 0.75 of best × 40% share = 0.30
weight_kg       1.4 -> 0.80 of best × 20% share = 0.16
score: 0.66 out of 1
sha256:e73545373ecea6c81c9e21d70965e29c13a777378cc3e2ad1e917e399766f2c5
```

Each measurement is placed between its worst and best value (0 is worst, 1 is best),
multiplied by its share of the total importance (2 out of 5 is 40%), and the results are
added up. The last line is a fingerprint of the exact input. Save it with the score and
you can later check that a replay used the same input.

3. Optional: run the same score in JavaScript (Node 22.13 or newer). Use the `@next`
   tag, because npm's default tag still points at an empty placeholder.

```bash
npm install @edgeproc/assay@next
```

Save this as `laptop.mjs` and run `node laptop.mjs`:

```javascript
import { compose, parseRequest } from "@edgeproc/assay";

const measure = (name, value, worst, best, importance) => ({
  id: name, label: name, value, interval: null, weight: importance,
  scale: { minimum: Math.min(worst, best), maximum: Math.max(worst, best),
           direction: best > worst ? "higher_is_better" : "lower_is_better" },
});
const result = compose(parseRequest({
  method: "weighted_mean", method_version: "laptop.v1", clamp: "reject",
  components: [measure("battery_hours", 10, 0, 20, 2),
               measure("price_usd", 900, 2400, 400, 2),
               measure("weight_kg", 1.4, 3.0, 1.0, 1)],
}));
console.log(`score: ${result.score.toFixed(2)} out of 1`);
console.log(result.inputs_hash);
```

Real output. The score and the fingerprint match the Python run:

```text
score: 0.66 out of 1
sha256:e73545373ecea6c81c9e21d70965e29c13a777378cc3e2ad1e917e399766f2c5
```

More examples are in [`examples/`](https://github.com/hseshadr/assay/tree/main/examples)
and the [quickstart](https://github.com/hseshadr/assay/blob/main/QUICKSTART.md).

## How it works

Your code sends a request: the method, a version label you choose for your formula, and
each measurement with its scale. Assay checks the request strictly first. Unknown fields,
missing values, numbers outside their scale, and duplicate names are refused with a short
error code such as `assay.out_of_range`, and no score comes back. It then combines the
measurements in the order you listed them, using one of three methods: a weighted
average, a plain sum with your own coefficients, or "the weakest measurement decides".
The result keeps every input and every intermediate number, so anyone can redo the math.
The Python and TypeScript packages are written separately and checked against the same
shared test cases.

## What it does not do

- **It does not decide what is fair or where to draw the line.** Your app chooses the
  measurements, the weights, and what counts as a pass. Assay only does the arithmetic
  you declared.
- **It does not check that your inputs are true.** It checks that they are well formed.
- **It does not learn.** There is no machine learning; the weights are the ones you wrote.
- **The fingerprint is not a security feature.** Anyone can compute it, so it cannot
  prove who made a result or that nobody changed it.
- **Results contain your raw inputs.** Treat a saved result as carefully as the data that
  went into it.
- **It is a prerelease.** The API may still change before a stable 0.5.0.
- Assay only computes scores. Sealing evidence about a result is a separate project, Avow, and neither package imports or requires the other.

## When to use something else

| If you need | Use |
| --- | --- |
| A tiny formula nobody will ever question | A few lines of your own code |
| People, not programs, doing the scoring | A spreadsheet |
| Weights learned from data | A machine-learning model |
| Someone else to run and tune the scoring | A hosted scoring service |
| A score computed inside your app that you can explain, replay, and match across Python and TypeScript | Assay |

## Install

> **Status:** prerelease. The current versions are `assay-engine` 0.5.0.dev3 on PyPI and `@edgeproc/assay` 0.5.0-dev.3 on npm. There is no stable release yet.

```bash
pip install assay-engine==0.5.0.dev3
npm install @edgeproc/assay@0.5.0-dev.3
```

On PyPI the package is called `assay-engine`, but you import it as `assay`. Plain
`pip install assay-engine` gets the prerelease today because no stable version exists.
On npm, `npm install @edgeproc/assay` without a version installs an empty placeholder, so
always give the version or the `@next` tag.

Extras for Python:

- `pip install "assay-engine[cli]"` adds the `assay` command (`assay compose`,
  `assay explain`, `assay measure`). Without it, the command prints
  `FAIL: assay.cli_extra_missing`.
- `pip install "assay-engine[metrics]"` adds optional calculators for classification,
  ranking, calibration, and agreement reports.

Older Python code may use the Python-only deep import `assay.composite` (`SubScore` and
`composite(...)`). It has no TypeScript version and no fingerprint, so new code should
use `parse_request()` and `compose()` from the package root.

## Develop

You need Python 3.13 with [uv](https://docs.astral.sh/uv/), and Node 22.13.0 with pnpm
11.5.0 for the TypeScript package. The
[getting started guide](https://github.com/hseshadr/assay/blob/main/docs/GETTING_STARTED.md)
walks through setup, the code layout, and a first change.

```bash
git clone https://github.com/hseshadr/assay
cd assay
uv sync --all-extras
uv run poe gate
```

That runs lint, formatting, strict types, complexity limits, and the Python tests with
at least 90% branch coverage. To build both real packages and check that Python and
TypeScript give the same numbers:

```bash
bash examples/run_composite.sh
```

## More detail

- [Getting started for developers](https://github.com/hseshadr/assay/blob/main/docs/GETTING_STARTED.md): setup, code map, first change, and pull requests.
- [Architecture](https://github.com/hseshadr/assay/blob/main/docs/ARCHITECTURE.md): the two packages, how a request flows, what the checks cover, and a worked cross-language example.
- [Explore the interactive architecture map](https://github.com/hseshadr/assay/blob/main/docs/architecture/index.html).
- [Methods](https://github.com/hseshadr/assay/blob/main/docs/METHODS.md): the exact math for each method, uncertainty ranges, and every result field.
- [Operations](https://github.com/hseshadr/assay/blob/main/docs/OPERATIONS.md): files the command line reads and writes, size limits, calculator settings, and the release process.
- [Quickstart](https://github.com/hseshadr/assay/blob/main/QUICKSTART.md): the command line and building both packages from a checkout.
- [TypeScript package](https://github.com/hseshadr/assay/blob/main/ts/README.md): the npm package on its own.
- [CHANGELOG](https://github.com/hseshadr/assay/blob/main/CHANGELOG.md): what changed in each version.
- [SECURITY.md](https://github.com/hseshadr/assay/blob/main/SECURITY.md): how to report a vulnerability privately.
- Bugs and feature requests: [GitHub Issues](https://github.com/hseshadr/assay/issues).

## License

MIT. See [LICENSE](https://github.com/hseshadr/assay/blob/main/LICENSE).
