# Assay operational contract

> **TL;DR:** Assay computes locally and keeps no hidden copy of inputs or results.
> Callers own input limits, data sensitivity, output retention, and application policy.

## Runtime boundary

| Surface | What it does | I/O and persistence |
|---|---|---|
| Python base package | Validates, normalizes, composes, and replays scoring requests. | No network, telemetry, subprocess, background worker, cache, or file I/O. Returned values remain under caller control. |
| Python optional metrics | Runs local scientific calculators after bounded validation. | No runtime network or implicit persistence. Third-party numeric libraries run in process. |
| Python command line | Runs `compose`, `measure`, or `explain` for an explicitly supplied file. | Reads one caller path. Writes standard output or one explicit output path. No implicit store. |
| TypeScript package | Validates and composes requests and runs its smaller calculator surface. | No runtime dependencies, network, telemetry, storage, DOM, or worker access. Returned values remain under caller control. |

Package-manager traffic while installing dependencies is outside this runtime boundary.
An application that obtains inputs remotely or sends results elsewhere owns that I/O.

## Data sensitivity and retention

`ScoreResult.components` deliberately retains each raw value so another reader can
replay the arithmetic. Labels, identifiers, raw measurements, intervals, and the
derived request fingerprint may therefore be sensitive. Minimize inputs before calling
Assay and do not place secrets or unnecessary personal data in a request.

The libraries set no retention clock because they have no hidden store. The caller must
choose retention, access, deletion, backup, and logging rules for requests and results.
Stable Assay errors contain codes rather than rejected values, but an application's own
logging still needs the same care.

## Command-line files

The command line accepts JSON request/result files of at most 1,048,576 bytes. Inputs
must be regular files. A requested output cannot alias the input through an identical,
equivalent, or existing same-file path.

Output is staged in the destination directory, flushed, atomically replaced, and the
parent directory is synchronized before success returns. A failure returns a stable
code and does not print the rejected JSON value. Standard output receives one complete
JSON or explanation stream when `--out` is absent.

Former mixed-product commands intentionally return migration codes without touching
caller files. The active scoring commands are `compose`, `measure`, and `explain`.

## Composition capacity

Core weighted-mean, additive, and minimum requests have no fixed component-count ceiling.
Work grows linearly with the number of declared rows, so callers must impose a limit
suited to their latency, memory, and request-size budgets. The command line's 1 MiB
input ceiling is an adapter boundary, not a library composition limit.

All public numbers must be finite IEEE-754 binary64 values. IDs are at most 128
characters and labels at most 256 characters. Extra fields, duplicate IDs, decreasing
scales, invalid intervals, and method-specific shape errors fail before a result is
returned.

## Optional calculator limits

These safety ceilings apply to optional measurement calculators, not to core
composition:

| Resource | Maximum |
|---|---:|
| input items or ranked positions | 1,000,000 |
| bootstrap resamples | 1,000,000 |
| sample × resample work cells | 10,000,000 |
| bootstrap cells processed per batch | 1,000,000 |
| calibration bins | 10,000 |
| agreement scale levels | 10,000 |
| ranking depth | 1,000,000 |
| relevance gain | 1,000,000 |
| bootstrap seed | 2^63 - 1 |

Defaults are 30 minimum samples, 9,999 bootstrap resamples, 0.95 confidence, 15
calibration bins, seed 12,345, and ranking depth 10. Controls may be supplied in a typed
measurement request. The legacy settings API additionally reads `ASSAY_*` environment
variables only when that optional surface is constructed.

Each control, its legacy environment variable, and its default:

| Control | Environment variable | Default | What it changes |
|---|---|---:|---|
| `min_samples` | `ASSAY_MIN_SAMPLES` | 30 | Fewest samples before an interval is reported; below it Assay abstains |
| `bootstrap_resamples` | `ASSAY_BOOTSTRAP_RESAMPLES` | 9,999 | Resamples for bootstrap confidence intervals |
| `confidence_level` | `ASSAY_CONFIDENCE_LEVEL` | 0.95 | Width of those intervals |
| `ece_bins` | `ASSAY_ECE_BINS` | 15 | Calibration bins |
| `bootstrap_seed` | `ASSAY_BOOTSTRAP_SEED` | 12,345 | Seed that makes resampling repeatable |
| `ranking_k` | `ASSAY_RANKING_K` | 10 | Ranking depth ("first page") |

The ceilings are validation and resource-safety contracts, not performance promises.
No production latency, throughput, or memory service level is claimed for the current
prerelease artifacts.

## Failure model

- Boundary failures use stable `assay.*` codes and omit caller values.
- Invalid requests return no partial score.
- Missing optional dependencies return a specific extra-missing code.
- Composition is deterministic for the same validated request and runtime arithmetic.
- `interval: null` means the inputs were deterministic; it is not a confidence claim.
- Application policy failures are outside the engine because policy remains with the
  application.

## Release candidate

> **TL;DR:** A release starts only from a manual run on protected `main`. Dagger proves the exact
> tag, commit, hosted gate, source history, and three artifacts before any publishing identity exists.

Do not create a tag, dispatch the candidate, or publish either package without a fresh, explicit
authorization. After authorization, run **Dagger release candidate** from the `main` branch with an
existing `vX.Y.Z` tag. The run fails unless the peeled tag, remote `main`, requested SHA, source
versions, and exactly one successful `Dagger` check all identify the same commit.

Dagger then runs the complete Python, TypeScript, mutation, example, benchmark, security, secret,
and artifact graph against that exact source history. It exports one checksum-bound candidate with
one wheel, one sdist, one npm tarball, registry decisions, and the pinned npm publisher. A pinned
artifact action persists only that directory for one day.

The successful manual candidate triggers two source-free publisher jobs. They download only that
run's exact artifact. The Python lane asks the exact-SHA Dagger module whether PyPI is missing the
candidate, then uses the official PyPI OIDC publisher. The npm lane passes the artifact and typed
GitHub OIDC request values to the exact-SHA Dagger publisher, which rechecks registry channel state
and invokes the checksum-pinned npm CLI with provenance. Neither privileged job checks out source,
installs dependencies, builds, tests, or runs free-form shell. Exact existing bytes plus provenance
skip safely; any mismatch fails closed.

Local developer checks remain available without release authority:

```bash
dagger call ci --commit-sha="$(git rev-parse HEAD)"
dagger call security --commit-sha="$(git rev-parse HEAD)"
dagger call artifacts export --path=dist/release
```

The root [README](../README.md) is embedded in each release artifact. Registry writes are irreversible;
the release operator must keep external publishers idle during the authorized window. GitHub Release
mirroring and historical recovery automation are deliberately outside this dual-registry path.

## Frozen release benchmarks

The benchmark gate measures scoring operations only.
Every report includes the operation count, p50, p95, p99, peak RSS, exact SHA, operating system,
and the exact Python or Node/pnpm toolchain.

| Workload | Frozen count | p99 budget | Peak RSS budget |
|---|---:|---:|---:|
| Python composition batch | 2,000 compositions × 5 samples | 8,000 ms | 512 MiB |
| TypeScript composition batch | 2,000 compositions × 5 samples | 8,000 ms | 512 MiB |
| Python minimum compose + serialized replay | 150000 components | 60,000 ms | 1,536 MiB |
| TypeScript minimum compose + serialized replay | 150000 components | 60,000 ms | 1,536 MiB |
| Python binary measurement | 10000 items, exactly 99 bootstrap resamples | 30,000 ms | 768 MiB |

Every workload reports nearest-rank p50, p95, and p99 across 5 independent child-process samples;
each child's real high-water RSS contributes to the maximum. The 10000-item binary workload uses
99 resamples, or 990,000 bootstrap work cells. It never uses
the library default of 9,999 resamples and remains below Assay's 10,000,000-cell boundary. The
150000-component workload is isolated in child processes because realistic runs can approach 1 GiB
of memory; its budget is deliberately broad enough for `ubuntu-24.04` hosted runners.

Run the same evidence locally with exact Node 22.13.0 and pnpm 11.5.0:

```bash
uv run python -m benchmarks.release
cd ts && pnpm benchmark
```
