# Changelog

## [Unreleased]

### Changed

- The README is rewritten in plain English: one-sentence description, a copy-paste
  Python and JavaScript example with real output, honest limits, and a link to every
  technical doc. Deep material moved to `docs/ARCHITECTURE.md` and `docs/OPERATIONS.md`,
  and a new `docs/GETTING_STARTED.md` takes a developer from clone to first change.
  `tests/test_readme_contract.py` now pins the section order, the doc links, and a
  banned-jargon list. The Python and npm package descriptions equal the new first
  sentence, which changes the pinned npm archive SHA-256 to `eb115cbd…a10d1b3ee`;
  scoring sources are unchanged and nothing was re-published.
- The README follows the portfolio template: a plain-language first screen, a runnable
  60-second example whose real output is the hero, and a `tests/test_readme_contract.py`
  gate that re-runs that example. The Python and npm package descriptions now equal the
  README tagline, which changes the pinned npm archive SHA-256 to `df935452…010dfd9`;
  scoring sources are unchanged and nothing was re-published.
- The scoring-only release candidate now has independent Python 3.13 and Node 22.13.0
  gates, cross-runtime parity, 120 mutation guards, installed-artifact examples, frozen
  benchmarks, reproducible minimal artifacts, and fail-closed registry verification.
- Release publication remains tag-only and provenance-bound. One protected `npm-release`
  environment approves both registry writes. PyPI and npm then use short-lived trusted-publisher
  OIDC identities. Existing byte-identical releases are safe to retry;
  channels are monotonic while the documented exclusive-publisher release window is enforced. After
  registry verification, exact artifacts are mirrored in an immutable GitHub Release.
- Security automation now scans the complete Git history and current tree with Gitleaks
  8.30.1, audits both locked dependency graphs, and checks workflows with actionlint,
  ShellCheck, and zizmor 1.30.1 in pedantic mode.
- `v0.5.0-dev.0` stopped before artifact construction because a source-built actionlint
  binary did not expose the audited release version. No registry bytes were published.
  The `0.5.0.dev1` / `0.5.0-dev.1` candidate installs the official actionlint 1.7.12
  archive behind its independent SHA-256 trust root.
- `v0.5.0-dev.1` built successfully, but both registry lanes rejected their publishing
  configuration before writing package bytes. The `0.5.0.dev2` / `0.5.0-dev.2`
  candidate advances the identity without changing the scoring API.
- `v0.5.0-dev.2` is complete on PyPI, npm, and an immutable GitHub Release. The
  `0.5.0.dev3` / `0.5.0-dev.3` pair advances the immutable identity after the trusted
  release recovery was hardened and duplicate CI orchestration was replaced by native
  Dagger checks. Python and TypeScript runtime scoring sources are unchanged from dev2.
- The split branch now identifies as the scoring-only `assay-engine` distribution,
  version `0.5.0.dev3`, and builds only the `assay` Python package.
- The exact mixed-product `0.4.x` source line is preserved locally at
  `release/avow-0.4.x` commit `3121df1af33a41b457faa2fd1ce84dc823950c39` as migration
  provenance. This task did not push, tag, merge, or publish that maintenance reference.

## [0.4.1] - 2026-08-13

### Security and correctness

- **0.4.1 supersedes 0.4.0 for every Python CLI ledger writer.** In 0.4.0 the
  convenience head file was saved after the ledger append released its process lock.
  Concurrent `assay score` processes could therefore leave that convenience pin behind
  the complete ledger. If an operator treated the stale file as the current external
  pin, truncation detection could be weakened. This patch holds one bounded process
  lock across reading the current head, appending, syncing the ledger, and atomically
  saving the new convenience head. Users of `assay score --ledger` should upgrade to
  0.4.1. The registry artifacts for 0.4.0 remain historical; this repository has no
  trusted-publisher workflow for yanking or deprecating an existing registry version,
  and no maintainer credential was used.
- Ledger lock acquisition now fails after **5.0 seconds** with the stable coded error
  `avow.ledger_lock_timeout`, rather than waiting forever. A head that cannot be
  durably installed raises `avow.ledger_head_write_failed`; it never reports success.
- Invalid persistence configurations fail before writing: lock timeouts must be finite
  and non-negative, and every CLI read/write role must use a filesystem-distinct path.
  This includes exact paths, hard links, symlinks, aliasing parent directories, and
  case/Unicode names the destination volume treats as identical; rejected commands
  preserve existing request, key, receipt, ledger, and head bytes.
- `assay score` stages and installs its receipt inside the same serialization boundary
  as ledger/head persistence. An output failure no longer commits a ledger entry, and
  concurrent writers sharing one output cannot publish an older receipt last. The
  three files remain separate crash commits and recovery stays fail closed.
- `keygen` stages and syncs both owner-only key artifacts, uses race-safe no-overwrite
  claims, and
  refuses to overwrite either destination. A failed second installation rolls back the
  first rather than destroying an existing signing identity. The public single-file key
  helpers and composite receipt output now use synced atomic replacements.
  A replacement followed by directory-sync failure reports an explicitly unknown
  old-or-new complete-file outcome; partial bytes are never installed.
- The ledger continues to lock the data-file descriptor used by 0.4.0, preserving
  mixed-version writer serialization during rolling upgrades. No public append API
  gained a callback or CLI-specific transaction parameter.
- A combined ledger/head append now requires its existing convenience pin to match the
  locked ledger state before writing. A stale, malformed, or missing pin on a non-empty
  ledger raises `avow.ledger_recovery_required`, so a later successful command cannot
  silently absorb an entry whose earlier head update failed.
- CLI validation and domain failures now emit only a stable code and safe schema field
  path; settings are resolved only inside that boundary, so malformed `ASSAY_*`
  environment values cannot leak through import-time tracebacks or help output. The
  installed entry point also redacts parser errors before command dispatch, so rejected
  arbitrary tokens never enter CLI output.
- Append now reads only one bounded tail entry rather than the whole ledger. Read,
  append, and verification enforce 64 MiB, 100,000-entry, and 64 KiB-line limits with
  `avow.ledger_limit_exceeded`; the concurrency benchmark starts from 5,000 entries.
  Canonical ledger lines use exactly one LF terminator: blank lines, CRLF, and partial
  final lines fail closed consistently in both full verification and bounded append,
  which never changes the malformed file.
- A successful append flushes and `fsync`s the complete JSONL line before returning.
  Head saves use a same-directory temporary file, file `fsync`, atomic replacement,
  directory `fsync`, and failure cleanup. The documented RPO is zero relative to a
  returned operation on supported local Unix filesystems. A crash between the ledger
  and head commits fails closed against the older pin; it is never silently repaired.
- Real-process regressions cover concurrent CLI writers, four concurrent library
  writers, a held-lock timeout, immediate process exit after append, replacement and
  sync failures, stale-pin rejection, truncation rejection, cleanup, and deadlock bounds.

### Added

- **A frozen operational contract** (`docs/OPERATIONS.md`) for privacy, data flow,
  egress, plaintext retention, key/head custody, supported filesystems, recovery,
  RPO, and intentionally unpromised automatic repair/RTO.
- **Deterministic release benchmarks enforced locally, in pull-request CI, and before
  trusted PyPI publication.** Fixed workloads predeclare p50/p95/p99 latency and peak
  RSS ceilings for Python signing/verification, TypeScript signing/verification,
  10,000-example classification, and a 200-append/four-process ledger run. The gate is
  `uv run poe benchmark` plus `pnpm --dir ts benchmark`; a missed count, integrity
  check, time, or memory ceiling exits non-zero. Ledger peak RSS is the maximum across
  parent verification and every real append worker, not only the workers.
- Executable privacy guards prove representative Python scoring/signing opens no
  socket and creates no hidden persistence, and scan the TypeScript runtime for browser
  egress/storage primitives.

### Changed

- All 17 legacy Python functions over the repository's 15-line readability ceiling
  were decomposed behind shared typed contracts without changing receipt bytes,
  metric answers, errors, or cross-language vectors. An AST contract now prevents
  regressions across `src/`, `scripts/`, and `benchmarks/`.
- Benchmark tooling is held to the same Ruff, strict mypy, and Xenon Grade A gate as
  runtime source and mutation tooling.
- README now links the operational contract at the runnable quickstart and states the
  0.4.0 supersession without publishing a procedural abuse recipe.
- Documentation now distinguishes encoded ledger-line reinsertion from submitting the
  same signed receipt twice through `append`. The latter creates two valid sequenced
  entries; semantic replay prevention requires a caller-owned nonce/request ID and state.

## [0.4.0] - 2026-08-13

### Added
- **An agreement face: `assay.agreement`, plus `assay.agreement_score` for a signed
  receipt.** Everything else in the package scores predictions against ground truth. This
  scores two *raters* against each other, when what they emit is a band on an ordered
  scale (weak / moderate / strong) and there is no ground truth to score against — nobody
  knows the true band, only whether two graders landed in the same place.

  Percent agreement is the wrong statistic for that shape, twice over. It is blind to
  *distance*: "strong vs moderate" is a near miss and "strong vs weak" is a total miss,
  and it scores both as simply not-a-match. And it counts agreement that chance alone
  would produce: two graders who both call 90% of everything "weak" match about 80% of the
  time while agreeing about nothing. The module ships `quadratic_kappa`
  (quadratic-weighted Cohen's kappa), `kendall_tau_b`, `percent_agreement` and
  `weighted_agreement` as primitives, and `agreement_report` for a whole item set.

  The arithmetic is **scikit-learn's and scipy's** — `cohen_kappa_score(weights=
  "quadratic")` and `kendalltau(variant="b")`, both already pinned dependencies, both the
  reference implementation their field validates against, and neither needing a line of
  correction at the boundary. Assay contributes what they do not: a band order the caller
  *declares*, a refusal for every input whose answer would be undefined, and the same
  bootstrap-with-an-abstention-floor the classification and ranking faces already use.

  The two statistics answer different questions and the report carries both. Two graders
  who agree on every *ordering* but sit one band apart on the level score tau-b 1.0 and
  kappa 2/3; a report with only one of those numbers is missing the other half.
- **`quadratic_kappa` takes the band order from the caller, never from `sorted()`.**
  `cohen_kappa_score` reads the ordinal distance between two bands off their positions in
  its `labels` argument. Omit that argument and it sorts the band names, so
  weak/moderate/strong silently becomes moderate < strong < weak — and the same ratings
  come back as +2/3 instead of -1/3. Both numbers look entirely plausible. There is a
  mutation for exactly this edit, and `tests/test_agreement.py` pins both values so the
  wrong one cannot pass for the right one.
- **Confusion counts and a named false-negative rate in `assay.metrics`.**
  `precision_recall_fscore_support` was being called with `average="binary"` and its
  support discarded, so the package had recall but no confusion counts at all — and a rate
  hides which way a system fails, because 200 misses with 2 false alarms scores the same
  accuracy as 2 misses with 200 false alarms. `confusion_counts` returns TP / FP / TN / FN
  as named cells and `false_negative_rate` returns the miss rate. It is exactly
  `1 - recall` and it is named anyway: a screening system is judged on its misses, and
  nobody reads a 3% miss rate off a recall of 0.97. Additive — `binary_scores` keeps its
  signature and now also carries `counts` and `false_negative_rate`, and the receipt's
  `ClassificationDetail` carries them too.
- **Refusals that stop scikit-learn from silently dropping rows.** sklearn discards, with
  no warning, any row whose label falls outside the `labels=` argument it was given. Two
  places in this release hand sklearn a `labels=`, so two refusals exist to make that
  safe: a rating naming a band the scale does not declare, and a classification label
  outside `{0, 1}`. Without them the number comes back looking perfectly healthy,
  computed over fewer items than the caller handed in. Both have a mutation.
- **Degenerate rating sets report UNDEFINED, never 1.0.** When both raters put every item
  in the same single band, percent agreement is a truthful 100% and kappa has no
  denominator — chance agreement is also total. `quadratic_kappa` returns `None` there
  rather than the flattering 1.0, and `AgreementReport` carries a
  `kappa_undefined_reason` sentence beside it, because a bare `None` reads as "not
  computed" when the fact is "cannot exist". `kendall_tau_b` does the same whenever
  *either* rater used one band throughout.
- **Ten more mutations, 18 -> 28.** The band order reaching kappa, the quadratic weighting
  (unweighted kappa scores a near-miss grader pair and a total-miss grader pair
  identically — that blindness is the whole reason the weighting exists), tau-b's tie
  correction, both new refusals, the undefined-not-perfect rule, the confusion-cell read
  order, and the FNR's denominator.
- **A new coded error, `InvalidAgreementRequest` (`assay.invalid_agreement_request`).**
- **`@edgeproc/avow` 0.4.0 grows a metrics face: recall@k, precision@k, F1@k, MRR, and
  the binary confusion set.** Until now the TypeScript package exported zero metric
  functions, which meant every TypeScript metric in the portfolio was hand-rolled *by
  construction* — there was nothing to adopt. aml-filter's release gate reads a recall
  number computed by 29 lines of bespoke arithmetic in its own repo; that is the only
  shipped recall figure anywhere in the portfolio, and nothing checked it against a
  reference. `ts/src/ranking.ts` and `ts/src/metrics.ts` are now that reference, and
  they refuse everything Python refuses, with the same `assay.*` codes: an empty ranked
  list, a duplicate document, a fractional relevance grade, a non-positive `k`, a set
  with nothing judged relevant, a single-class label set.
- **`testdata/vectors/metrics.json` — 22 hand-computed cases that BOTH suites replay.**
  This is the part that makes the deliverable real. A TypeScript metrics module without
  cross-language vectors just adds a second place for the number to be wrong: Python
  reaches its answers through `trec_eval` and scikit-learn, TypeScript counts them out
  against the definitions, and two implementations of one rule is exactly the
  arrangement that drifts. `tests/test_metric_vectors.py` and
  `ts/src/metricVectors.test.ts` read the same file, so a divergence fails CI in both
  languages rather than being found later by a human.
  Unlike `canonical.json`, it is **not generated** — those hold bytes nobody could
  author by hand, whereas a generated metric vector would be a transcript of whatever
  the code currently returns, green through the exact bug it exists to catch. Every
  number was computed from the definition (each case carries its arithmetic in a `hand`
  field) and then checked against Python. The confusion cells are pinned without a
  Python confusion-count function existing: the actual-positive count is countable
  straight off `y_true`, and scikit-learn's recall and accuracy then determine all four
  cells uniquely, so the Python replay re-derives them and requires the result to equal
  the cells TypeScript asserts.
- **17 more mutations, 16 of which break TypeScript and run under vitest.** The
  harness previously spoke only pytest. A claim only Python can break is a claim only
  Python defends, and half of the metrics claims now ship in a browser. Two of the new
  mutations are guarded *only* by the shared-vector suite, so they answer the question
  that makes the vectors worth having: when the TypeScript answer is pushed away from
  Python's, does the shared file actually notice.
  **Vitest's verdict is read from its JSON reporter's pass/fail counts, never its exit
  code.** `vitest run -t 'no-such-test'` exits **0**, reporting every test in the file
  as "total" while running none of them — read by exit code, a guard that no longer
  exists reports a green baseline. `numPassedTests` is the only field that says
  something ran, and a reporter that writes no file at all (what a misspelled reporter
  name does, and what once let a sibling harness score twelve crashed runs as green) is
  a harness error rather than a verdict.
- **`test_the_metric_vector_counts_the_readme_promises`**, pinning 6 / 7 / 5 / 4 metric
  cases to the literals the README states, with its own mutation that drops one.
- **A mutation harness, `scripts/mutation_harness.py`, run with `uv run poe mutants`.**
  The ranking face's red runs existed only as prose in a commit message, so no reader
  could re-run them — and a guard nobody can watch fail is not evidence. The harness
  breaks 28 named guards one at a time, requires the suite to go red, and restores the
  file. **Its verdict is the pytest exit code and nothing else**: `0` all passed, `1` a
  test failed, and every other code (`4` usage error, `5` nothing collected) gets its own
  name and fails the run. A harness elsewhere in this portfolio once grepped stdout for
  failures, read a crashed runner as "no failures", and reported all 12 of its mutations
  green. Each mutation is also read back off disk before its verdict is trusted, so an
  edit that silently did not apply cannot be scored as a guard that held.
  It covers: the ranking metrics being `trec_eval`'s arithmetic through `ir_measures`
  rather than a reimplementation (the ranked order, the cut-off `k`, and graded gains all
  proven to reach the engine); every ranking refusal; the envelope re-deriving the payload
  hash and pinning the signer; the ledger's chain / count / signature checks as three
  separate guards; and the numeric literals the README states out loud.
- **A test the harness found missing: `test_should_reject_a_ledger_whose_chain_link_was_rewritten`.**
  Deleting the chain-link check left the whole suite green. The existing splice test
  survived it because a spliced entry also changes the entry count, so the pinned-head
  check caught it first — the chain walk was defended only in depth and had no case that
  isolated it. The new test rewrites an interior entry's `prev_hash` and nothing else:
  the last entry is untouched so the pinned head still matches on count *and* hash, and
  `prev_hash` sits outside the signed receipt so every signature still verifies. Only the
  chain walk can object, and now something checks that it does.
- **`tests/test_documented_constants.py`, pinning literals to the literal.** A constant
  asserted only against its own source is unguarded. `test_vectors.py` asserted `>= 8`
  canonicalization vectors and `>= 1` receipts — loose bounds on shape that stay green
  while three vectors go missing and the README keeps promising 12. The new file pins 9,
  3 and 12, and pins the documented ranking cut-off of 10. Neither existing assertion was
  touched.
- **`.github/dependabot.yml` (github-actions, weekly).** assay had no Dependabot config,
  which is why it was the only repo in the portfolio still on `astral-sh/setup-uv` v8.3.2
  while everything else moved to v9.0.0: a SHA pin does not move on its own and nothing
  was opening the bump PR.
- **A `mutation-gate` CI job**, so the evidence is regenerated on every pull request
  rather than captured once. It is deliberately not a step inside `gate`: `gate` asks
  whether the tests pass, `mutants` asks whether they can fail.

### Fixed
- **A document id of `__proto__` scored 1.0 in Python and 0 in the browser.** Found by
  probing the new face rather than by a test, which is the honest way to say it.
  `binaryJudgments` accumulated into a plain object, and `plain["__proto__"] = 1` does
  not create a property — it invokes `Object.prototype`'s `__proto__` setter, which
  ignores a non-object value. The document silently vanished from the judgments. Python
  has no such rule and keeps the key, so the same input produced precision@1 of **1.0
  server-side and 0.0 browser-side**: no refusal in either language, no rounding
  difference, just two confident and different answers — exactly the defect the shared
  vectors exist to prevent, in the very PR that introduced them. The accumulator now has
  a null prototype, and the `document_id_named_proto` shared vector pins it in both
  languages, with a mutation (`ts-ranking-keeps-a-document-called-proto`) that puts the
  plain object back and watches the guard go red.
- **The mutation harness was scoring a guard `SURVIVED` on stale bytecode.** Found while
  adding the TypeScript guards, by running `poe mutants` repeatedly at an unchanged
  commit: `ranking-k-reaches-trec-eval` failed on two of three runs. CPython decides a
  `.pyc` is current from the source's mtime **and size**, and three of the existing
  mutations are one character for one character — `P @ k` -> `P @ 1`, `R @ k` -> `P @ k`,
  `AP)` -> `RR)`. The size never changes, so a write landing inside the same mtime tick
  as the cached `.pyc` left the next interpreter loading unmutated bytecode: the guard
  ran against code that was never broken, passed, and was reported as blind. The restore
  path had the nastier version of the same bug — the `.pyc` written during a mutated run
  could shadow the restored source, so the *next* mutation's baseline ran mutated code.
  Both writes now drop the cached `.pyc`. 36/36 across four consecutive runs after the
  fix, against 1/3 clean before it.
  This is the harness's own instance of the defect it exists to catch: it reported a
  verdict it had not actually measured.

### Changed
- `ClassificationDetail` in a receipt gains `false_negative_rate` and `confusion`.
  `ReceiptPayload` gains an optional `agreement` detail. The golden cross-language vectors
  are unaffected: they carry their own subject model, not `assay.ReceiptPayload`.

- The `mutation-gate` CI job now installs pnpm and Node 22 alongside uv, because the
  harness it runs breaks guards in both languages. Nothing was weakened: the job gained
  a toolchain, not an exemption.
- `astral-sh/setup-uv` bumped to **v9.0.0** in `ci.yml` and `security-audit.yml`. The
  comment beside each pin names the exact version (`# v9.0.0`), never a floating `# v9` —
  a floating-major comment turned another repo's `main` red the day upstream re-pointed
  its tag.
- The gate now covers `scripts/` too (ruff, ruff-format, mypy `--strict`, xenon A). The
  harness that proves the guards can fail is not exempt from the gate that guards them.
- README `Status` corrected: it still claimed 220 tests and `avow` 0.2.0 as the published
  release. It was 258 tests and 0.3.0 when that correction landed; this release makes it
  298 tests and 0.4.0.

## [0.3.0] - 2026-08-03

Adds the ranking face and renames the envelope's one misnamed error. Pair-versioned with
`@edgeproc/avow` on npm; `@edgeproc/receipt-ui` is versioned separately.

**Why this release exists.** `assay.ranking` merged after the `v0.2.0` tag, so it shipped
in no release at all — PyPI `avow` 0.2.0 has neither the module nor `ir-measures` in its
metadata. The first consumer had to pin `avow` to a git merge commit by full SHA to reach
it, and a URL requirement cannot be resolved to a version, so that consumer's dependency
audit skipped `avow` entirely: *"URL requirements cannot be pinned to a specific package
version."* A CVE against `avow` or its transitive dependencies would have gone unseen for
as long as that pin stood. Publishing is what closes it — the consumer moves back to a
PyPI range, `avow[assay]>=0.3.0`, and its audit sees the package again.

**A note on version strings.** A git checkout of the merge commit reports `0.2.0`, the
same string as the published artifact that does *not* contain this code. The version
string was never the identity here; the release is.

### Added
- **A ranking face: `assay.ranking`, plus `assay.ranking_score` for a signed receipt.**
  Everything in `metrics.py` scores `(y_true, y_score)` pairs, a shape with no notion of
  *position* — a search engine that returns the right product tenth and one that returns
  it first produce identical numbers. The new module scores a
  `(relevance judgments, ranked list)` pair instead: `precision_at_k`, `recall_at_k`,
  `f1_at_k`, `ndcg_at_k` (graded relevance, not just binary), `mrr`,
  `average_precision` / `mean_average_precision`, and `ranking_report` for a whole query
  set. The arithmetic is **`trec_eval`'s, reached through `ir_measures`** — trec_eval is
  the reference implementation the IR field validates its own numbers against, so every
  metric here is the field's definition rather than assay's reading of it. Assay
  contributes the two things trec_eval does not: a Python-native
  `(judgments, ranked list)` contract in place of its qrels/run file pair, and a refusal
  on every input whose answer would be undefined.
- **New runtime dependency `ir-measures>=0.4.3`, under the `avow[assay]` extra** — not
  the base install, which stays the envelope only (pydantic, pynacl, rfc8785) so
  `pip install avow` and micropip in Pyodide never pull the scientific stack. It carries
  exactly one transitive dependency, `pytrec-eval-terrier` — the C++ binding to trec_eval
  itself — whose own dependencies are numpy and scipy, already pinned in the same extra.
- **Why not scikit-learn: the adapter code was the tell.** `ndcg_score` and
  `label_ranking_average_precision_score` are multilabel-**classification** metrics.
  Neither has any notion of a relevant document that was never retrieved, so both had to
  be talked into retrieval semantics at the boundary: nDCG by padding the positions the
  ranker left empty and parking missed documents below every filled one; average
  precision by rescaling LRAP by `|relevant retrieved| / |relevant|` to undo its habit of
  dividing by the labels it was handed — **without which, retrieving 1 of 4 relevant
  documents scored 1.0**. Those were two hand-written semantic corrections wrapped around
  a mismatched engine, and a subtle error in either would have silently corrupted every
  number downstream. Both are deleted; trec_eval has these semantics natively. Maturity
  was never the question — scikit-learn is impeccably mature and was still the wrong
  engine for this field.
- **`ranking_report` carries an interval, or abstains.** Mean nDCG@k comes with a
  bootstrap confidence interval over the per-query values, or an `Abstention` below
  `AssaySettings.min_samples` — the same honesty floor the classification face uses, not
  a second uncertainty story. Per-query rows are always returned alongside the means,
  because the mean is the number that hides a broken query and the rows are the number
  that names it.
- **Two coded refusals: `assay.invalid_ranking_request` and `assay.empty_relevant_set`.**
  An empty relevant set is refused rather than scored 0.0, because 0.0 reads as "the
  ranker found nothing" and would blame the ranker for missing *judgments*. `k <= 0`, an
  empty ranked list, the same document twice in one ranked list or judged twice in one
  query, and a negative gain are all refused for the same reason: the answer would be
  undefined, not merely small.
- **`AssaySettings.ranking_k` (default 10)** — the cut-off is configuration, not a
  literal in the logic, and whichever `k` applied is recorded in the receipt so a
  reported precision@k always says which k it was.

### Changed
- **BREAKING — `ReplayMismatch` is renamed `PayloadHashMismatch`, and its code
  `avow.replay_mismatch` becomes `avow.payload_hash_mismatch`.** The error fired on a
  payload edited behind a stale hash field — a **tamper** failure. It never detected
  replay, and neither does anything else in the envelope. Naming the one error in a
  cryptographic verifier's catalog "Replay" implied a property the package does not have,
  which is the same class of defect as an untrue sentence in a README: a reader who sees
  `avow.replay_mismatch` reasonably concludes replay is handled. It is not.
  `ReplayMismatch` stays as a deprecated alias so `except ReplayMismatch:` /
  `instanceof ReplayMismatch` keeps working; **a caller that branches on the literal
  string `"avow.replay_mismatch"` must be updated.** Removed in 0.4.0. Mirrored in
  `@edgeproc/avow` on npm, since the `code` strings are a cross-language contract.
- **New `assay.replay_refused` (`ReplayRefused`), raised where the scoring face used to
  raise the envelope's error.** `assay.replay` means "recompute this receipt from its
  inputs and check it reproduces" — a scoring concept, not a cryptographic one. A receipt
  recording no determinism settings now fails with an `assay.*` code instead of borrowing
  an `avow.*` one that described a hash mismatch it never measured.

### Documented
- **Freshness is now stated as an explicit limit, in every place a caller reads.**
  `verify_signature` / `verify_receipt` prove *who signed it* and *that it is unmodified*.
  They do **not** prove that a receipt is fresh or has not been presented before. A
  genuine receipt, captured and handed over again unchanged, is byte-identical to the
  original and verifies forever — which is the same determinism that makes offline
  verification years later work at all. This is correct-by-design for a bare signature;
  what was wrong was leaving it unsaid while shipping an error called `ReplayMismatch`.
  Now in the README's `Honest limits`, the tampered-record demo, the error-code table,
  both verifier docstrings, and — most importantly — `ts/README.md`, because the npm
  package ships the envelope **with no ledger at all**, so a browser caller has no
  fallback and must hold replay state itself.
- Two tests pin both halves of the boundary so neither can drift silently:
  `test_a_replayed_receipt_verifies_because_a_signature_carries_no_freshness` (the
  envelope accepts a replay, by design) and
  `test_the_ledger_chain_is_what_refuses_a_replayed_receipt` (the ledger refuses one).
  The second was watched go red with both the chain walk and the head comparison
  disabled; either one alone catches it, which is defence in depth.

## [0.2.0] - 2026-08-01

A breaking release that makes three trust claims literally true. Pair-versioned with
`@edgeproc/avow` on npm; `@edgeproc/receipt-ui` is versioned separately.

### Changed
- **BREAKING — the ledger is a hash chain with the head pinned out-of-band, so it is now
  append-only in the tamper-evident sense.** Each line is a `LedgerEntry` carrying `seq`,
  `prev_hash` and the receipt, so the last entry's hash commits to the entire history.
  `append` returns the ledger's new `LedgerHead` (count + hash);
  `verify_integrity(..., expected_head=...)` and `verify-ledger --head <file>` now require
  it, and `score` writes it to `<ledger>.head`. Verification walks the chain from genesis
  and rejects any ledger that does not end exactly at the pinned head.
  **This closes the gap the previous entry disclosed.** Deleting an entry, truncating the
  file (including emptying it), reordering, replaying, and splicing in a same-signer entry
  from another ledger each returned `OK: ledger verified, N entries intact` and exit `0`
  before; all now fail with `avow.ledger_integrity` and exit `1`. Each attack is a test in
  `tests/test_ledger.py` (the strict-`xfail` markers are gone), and each of the four guards
  has been watched go red with its own check disabled.
  **Tests inverted, loudly:** `test_an_existing_empty_ledger_verifies_as_zero_entries_which_is_a_known_gap`
  asserted the defect as the requirement — that an empty ledger *always* verifies. It is
  now `test_a_fresh_empty_ledger_verifies_only_against_the_empty_head`: an empty file
  passes against the empty head and **fails** against any other, which is what stops an
  erased audit from reading as a fresh one.
  **Honest limit, restated:** this moves the trust requirement from N lines to 32 bytes; it
  does not remove it. A head file beside the ledger is a copy for carrying away, never a
  control against an attacker who can write both. Reading an old-format (unchained) ledger
  now fails with `avow.ledger_entry_malformed`; there is no migration path in 0.x.
  New coded error: `avow.ledger_head_unreadable` (missing or unparseable pin — fails closed
  rather than falling back to the head the file computes for itself).
  New public API: `LedgerHead`, `LedgerEntry`, `EMPTY_HEAD`, `entry_hash`, `read_entries`,
  `current_head`, `save_head`, `read_head`.
- **BREAKING — ledger tamper-evidence is now real.** `avow.verify_integrity` and
  `assay verify-ledger` now require the signer's pinned **public** key and verify each
  entry's **Ed25519 signature**, not just re-derive its content hash. A hash-only check
  was an overclaim: an adversary with no signing key can still recompute an entry's
  public content hash and launder a forged payload past it. The signature — which only
  the private seed can produce — is what makes tampering detectable by anyone holding the
  public key. `verify_integrity(path, receipt_type, *, expected_public_key=...)` and
  `verify-ledger --public-key <file>` are now required arguments.
  **Scope, stated plainly:** this makes *per-entry* tamper-evidence real, and nothing
  more. Whole-entry attacks — deleting, truncating, reordering, replaying, splicing in a
  same-signer entry — remained undetected after this change, because entries were still
  not chained to one another. The chain bullet above is what closed that, in the same
  unreleased 0.2.0; this bullet is kept for the record of what each change actually
  bought.
- **BREAKING — score receipts are self-describing, replay is unconditional.**
  `ReceiptPayload` gains a signed `determinism` field recording the settings that
  determine its numbers (`min_samples`, `bootstrap_resamples`, `confidence_level`,
  `ece_bins`, `bootstrap_seed`). `assay.replay(request, receipt)` drops its `settings`
  parameter and recomputes from the settings recorded **in the receipt**, so a legitimate
  receipt always replays — no ambient environment has to match, and a receipt computed
  under different settings is explicitly different rather than a silent replay failure.
  This changes every classification receipt's `payload_hash`.
- **BREAKING — governed effects are attested atomically.** `writ.EffectSubject` gains an
  `outcome` field (`not_run` / `attempted` / `succeeded` / `failed`); `writ.gate` and
  `governed_gate` gain an optional `emit` sink. On allow, the gate seals an `attempted`
  receipt and emits it **before** running the effect, then seals the `succeeded`/`failed`
  outcome after — so a failed or partial privileged effect always leaves a signed
  attestation of the attempt. Wire `emit` to `avow.ledger.append` for durable capture.

### Fixed
- **`assay.replay` compared the recomputed digest against the receipt's own
  `payload_hash` *field*, never against `receipt.payload`.** A payload edited behind an
  untouched hash field therefore replayed as `True` while `verify` correctly returned
  `False`. `replay` now re-derives the digest from `receipt.payload` — the same
  `payload_digest` the envelope's hash check uses — and additionally requires the stored
  `payload_hash` to agree, so a self-inconsistent receipt never replays.
- `assay.verify` now also catches `CanonicalizationFailed`, so a payload that cannot be
  canonicalized fails closed (`False`) instead of raising through the boolean facade.
- Pinned-key comparison in `verify_signature` is case-insensitive: hex identity is not
  spelling-bound, so an uppercase pinned key and a lowercase embedded key are the same
  signer.
- `avow.ledger.append` takes an exclusive advisory lock (`O_APPEND` + `flock`), so
  concurrent appenders cannot interleave a half-written line.
- `@edgeproc/receipt-ui` `StatusPill` ignores a blank (`""`/whitespace) label override
  and keeps the built-in verdict, so a fail-closed status chip never renders empty.

## [0.1.1] - 2026-07-21

First co-release through the OIDC rail: a `v*` tag fans out to PyPI (`avow`) and npm
(`@edgeproc/avow`) token-free. `@edgeproc/receipt-ui` is versioned separately and is
not part of this release.

### Added
- **`assay verify-ledger`** — re-derives every ledger entry's content hash and fails
  closed with the coded `avow.ledger_integrity` if one was edited on disk. The check
  needs no key: identity is the content hash, so anyone holding the file can run it.
  `avow.read_all` / `avow.verify_integrity` are now exported from `avow` alongside
  `avow.append`, so the ledger's read half is reachable from the public surface.
- **Coded errors for an unusable ledger:** `avow.ledger_unreadable` (missing, not a
  regular file, or permission-denied) and `avow.ledger_entry_malformed` (a line that is
  not a parseable receipt, previously surfaced as a raw pydantic traceback).
- **A wrong signer is coded apart from wrong signature bytes.** Both cases previously
  raised `SignatureInvalid` / `avow.signature_invalid`, so a caller could only tell a
  *provenance* failure from a *tamper* failure by string-matching the English message.
  Now `SignerMismatch` (`avow.signer_mismatch`) is raised when the receipt's embedded
  key is not the pinned signer, and `SignatureBytesInvalid` when the curve check
  rejects. Mirrored identically in `@edgeproc/avow` (the codes are a cross-language
  contract). **Compatibility:** both are subclasses of `SignatureInvalid`, so every
  existing `except SignatureInvalid:` / `instanceof SignatureInvalid` keeps working, and
  `SignatureBytesInvalid` deliberately keeps the published `avow.signature_invalid`
  string. The one behaviour change on upgrade: code matching
  `exc.code == "avow.signature_invalid"` to detect a *pinned-key mismatch* now sees
  `avow.signer_mismatch`. Published 0.1.0 consumers are unaffected until they upgrade.

### Fixed
- **Ledger reads no longer fail open.** `read_all` returned `()` for an absent path, so
  `verify-ledger` pointed at a typo'd or never-written file printed
  `OK: ledger verified, 0 entries intact` and exited `0` — a clean bill of health for a
  file it never opened. Reads now fail closed with `avow.ledger_unreadable`. An
  *existing but empty* ledger remains a pass: that is a legitimate initial state.
- **Coverage measured less code than it reported.** The `exclude_lines` entry `\.\.\.`
  was meant to skip bare `...` stub bodies, but it also matched the ellipsis inside
  `tuple[X, ...]` type annotations — silently excluding every function whose signature
  carried one, including the ledger's own `read_all` and `verify_integrity`. Anchored to
  `^\s*\.\.\.$`; Protocol stubs now carry an explicit `# pragma: no cover`. This
  surfaced 22 previously unmeasured statements (499 -> 521 at the time of the fix).
- **Flaky TypeScript signature test.** The corrupted-signature case overwrote only the
  final byte of an Ed25519 signature; because `S < L ≈ 2²⁵²` that byte is already `0x00`
  roughly 1 in 16 times, so the "corruption" was intermittently a no-op and the valid
  signature verified. It now replaces the whole signature, matching the Python side.
  Measured before: 6.77% failure rate; after: 0 failures in 1,000 runs.
- **Workflow pin guard no longer has two blind spots.** It globbed `*.yml` only (a
  `*.yaml` workflow bypassed the check entirely) and passed vacuously when the scan
  matched nothing at all. It now scans both extensions and asserts a non-zero ref count.

### Changed
- **Branch coverage is now measured** (`--cov-branch`), not just statement coverage.
- **Demo tests assert on captured stdout**, not only on `exit_code == 0`. They re-parse
  the demo's own printed hash, calibration numbers and composite interval, so gutting
  what the demo computes fails the tests rather than passing silently.
- `poe gate-all` runs the Python gate and the TypeScript gate together, mirroring CI's
  two jobs. `poe gate` remains Python-only; `poe gate-ts` is the TypeScript half.
- **CLI failures report their coded cause** (`avow.signature_invalid`,
  `avow.replay_mismatch`, `avow.ledger_integrity`) instead of collapsing to a bare
  pass/fail line, so a caller can branch on the cause without matching message text.
- `keygen --out` and `--ledger` now default from `AssaySettings`
  (`ASSAY_SIGNING_KEY_PATH`, `ASSAY_LEDGER_PATH`) rather than from literals inlined in
  the command signatures.

## [0.1.0] - 2026-07-19
### Changed
- **Repackaged as distribution `avow`** exposing three top-level import packages:
  `avow` (the shared trust envelope), `assay` (scoring face, `import avow`), and
  `writ` (effect face, `import avow`). The dependency edges `assay → avow` and
  `writ → avow` are import edges inside one wheel; `avow` imports neither.
- **Extras split:** base install is the envelope only (pydantic, pydantic-settings,
  pynacl, rfc8785); the scoring science stack moved behind `avow[assay]`
  (scikit-learn, scipy, numpy) and the Typer CLI behind `avow[cli]`. `import assay`
  without the extra raises coded `ScoringExtraMissing`.
- **Envelope error catalog** moved to `avow.errors` with `avow.*` codes under
  `AvowError`; scoring codes stay `assay.*` under `AssayError` (both re-exported from
  `assay.errors` for a single import site). `avow.ledger` is now subject-generic.

### Added
- Cross-language golden vectors (`testdata/vectors/`) generated by
  `tests/gen_vectors.py`, replayed by `tests/test_vectors.py`, to pin RFC 8785 byte
  identity across languages — the same vectors the `@edgeproc/avow` TypeScript package
  replays, so a receipt signed in Python verifies in the browser byte-for-byte.

## [0.0.1] - 2026-07-19
### Added
- Deterministic v0 scoring engine: metrics, calibration (ECE/Brier/reliability),
  bootstrap uncertainty with abstention floor, weighted multi-scale composite.
- Signed, offline-verifiable `ScoreReceipt` (RFC 8785 JCS + SHA-256 + Ed25519),
  built on a payload-agnostic trust envelope that a future effect-face can reuse.
- Append-only content-addressed ledger with integrity check.
- Typer CLI (`keygen`, `score`, `composite`, `verify`) and a demo proving all six
  acceptance cases.
