"""Mutation harness: break a named guard on purpose, and watch the suite go red.

**Why this file exists.** A green test suite is not evidence that a guard guards. A test
that asserts whatever the code happens to return goes green whether the property holds
or not, and the only way to tell those two apart is to break the property and watch the
test fail. This harness does that mechanically, once per claim, so the evidence is
re-runnable by anyone rather than narrated in a commit message.

**How it reads its own results.** The verdict is the pytest **exit code**, never its
output. A harness that greps stdout for "failed" reports green when the runner crashes,
because a crash prints no failures either. pytest's exit codes are unambiguous: ``0``
every test passed, ``1`` a test failed, ``2``/``3``/``4`` interrupted / internal /
usage error, ``5`` nothing was collected. Only ``1`` counts as a guard firing; every
other code is its own named harness failure.

Each mutation runs its named guard tests twice and then restores the file:

1. **before** — unmutated, must exit ``0``. Proves the guard exists, collects and passes.
2. **after** — mutated, must exit ``1``. Proves the guard fails when the property breaks.
3. **restore** — the original bytes are written back and compared, in a ``finally``.

The mutation is read back off disk before the second run, so an edit that silently
failed to apply can never be reported as a guard that held.

The active release set covers the complete Assay Python and TypeScript scoring
surfaces plus the dependency-quarantine policy and the npm `latest` install guard.

Run it::

    uv run poe mutants

It edits tracked files in place, so it refuses to start unless every file it will touch
is clean in git.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TS = _ROOT / "ts"

# pytest's documented exit codes. The verdict is read from these and from nothing else,
# and the vitest runner below maps its own JSON counts onto the same three numbers so
# one report can hold both languages.
_ALL_PASSED = 0
_TESTS_FAILED = 1
_NOTHING_COLLECTED = 5

_PYTEST = "pytest"
_VITEST = "vitest"

_MIN_VECTORS_TO_DROP_ONE = 2
_GENERATED_ROOTS = ("dist/publish-tools", "dist/release", "publish-tools", "release")

_RANKING = "src/assay/ranking.py"
_AGREEMENT = "src/assay/agreement.py"
_METRICS = "src/assay/metrics.py"
_CALIBRATION = "src/assay/calibration.py"
_UNCERTAINTY = "src/assay/uncertainty.py"
_OPTIONAL = "src/assay/_optional.py"
_SETTINGS = "src/assay/settings.py"
_METRIC_VECTORS = "testdata/vectors/metrics.json"
_COMPOSITION_VECTORS = "testdata/vectors/composition.json"
_PNPM_WORKSPACE = "ts/pnpm-workspace.yaml"
_PUBLISHED_VERIFIER = "scripts/verify_published_release.py"
_TS_RANKING = "ts/src/ranking.ts"
_TS_METRICS = "ts/src/metrics.ts"
_TS_NORMALIZE = "ts/src/normalize.ts"
_TS_ADDITIVE = "ts/src/additive.ts"
_TS_MINIMUM = "ts/src/minimum.ts"
_TS_REQUEST_HASH = "ts/src/requestHash.ts"
_TS_CONTRACTS = "ts/src/contracts.ts"

_NODE_VERSION = "v22.13.0"
_PNPM_VERSION = "11.5.0"
_TS_ADDITIVE_ORDER_GUARD = (
    "src/additive.test.ts::adds and subtracts left to right before applying the final policy"
)
_TS_VECTOR_REPLAY_GUARD = (
    "src/compositionVectors.test.ts::executes every named vector with byte-equivalent result JSON"
)


class MutationNotAppliedError(RuntimeError):
    """The edit did not reach the file, so its result would prove nothing."""


class HarnessError(RuntimeError):
    """The runner itself failed, so neither run is evidence of anything."""


@dataclass(frozen=True)
class Mutation:
    """One break, the claim it breaks, and the tests that must notice.

    ``guard`` entries are pytest node ids by default. When ``runner`` is ``vitest``
    they are ``<file>::<test name>`` instead — the same shape, resolved against
    vitest's file filter and ``--testNamePattern``."""

    name: str
    claim: str
    target: str
    guard: tuple[str, ...]
    edit: Callable[[str], str]
    runner: str = field(default=_PYTEST)


def _replace_once(old: str, new: str) -> Callable[[str], str]:
    """Replace exactly one anchor; drift is a hard failure, never a skipped mutant."""

    def edit(text: str) -> str:
        found = text.count(old)
        if found != 1:
            raise MutationNotAppliedError(
                f"anchor occurs {found}x, expected exactly 1: {old.strip()!r}"
            )
        return text.replace(old, new)

    return edit


def _drop_last_metric_vector(text: str) -> str:
    """Delete one shared metric case, so the count the README promises stops being true."""
    vectors = json.loads(text)
    if len(vectors["ranking"]) < _MIN_VECTORS_TO_DROP_ONE:
        raise MutationNotAppliedError("fewer than two ranking cases; nothing to drop")
    vectors["ranking"] = vectors["ranking"][:-1]
    return json.dumps(vectors, indent=2) + "\n"


def _zero_composition_vectors(_text: str) -> str:
    """Remove every composition oracle so non-vacuous replay must fail."""
    return "[]\n"


def _delete_throw(statement: str) -> Callable[[str], str]:
    """Remove a refusal, leaving its ``if`` block empty but syntactically valid.

    The TypeScript counterpart of replacing a Python ``raise`` with ``pass``."""
    return _replace_once(statement, "      // mutated: this refusal was deleted")


_MIXED_PRODUCT_MUTATIONS: tuple[Mutation, ...] = (
    # ----------------------------------------------------------------------------------
    # The ranking metrics are trec_eval's arithmetic, reached through ir_measures.
    # Every mutation below breaks the WIRING to that engine, not assay's own maths.
    # ----------------------------------------------------------------------------------
    Mutation(
        name="ranking-order-reaches-trec-eval",
        claim="the ranked ORDER is what trec_eval scores, not merely the retrieved set",
        target=_RANKING,
        guard=(
            "tests/test_ranking.py::test_should_rank_a_better_order_above_a_worse_one_when_the_set_is_identical",
            "tests/test_ranking.py::test_should_score_clearly_below_one_when_the_ranking_is_exactly_reversed",
            "tests/test_ranking.py::test_should_report_the_reciprocal_of_the_first_hit_position",
        ),
        edit=_replace_once(
            "    return {_QUERY: {doc: float(len(ranked) - i) for i, doc in enumerate(ranked)}}",
            "    return {_QUERY: dict.fromkeys(ranked, 1.0)}",
        ),
    ),
    Mutation(
        name="ranking-k-reaches-trec-eval",
        claim="the cut-off k is handed to the engine, not ignored",
        target=_RANKING,
        guard=(
            "tests/test_ranking.py::test_should_count_hits_over_k_when_measuring_precision",
            "tests/test_ranking.py::test_should_charge_for_the_empty_positions_when_k_exceeds_the_list",
        ),
        edit=_replace_once(
            '    return _score(relevant, ranked, _cut("P", k))',
            '    return _score(relevant, ranked, _cut("P", 1))',
        ),
    ),
    Mutation(
        name="ranking-recall-is-not-precision",
        claim="recall@k divides by the relevant set, never by k",
        target=_RANKING,
        guard=(
            "tests/test_ranking.py::test_should_divide_by_the_relevant_set_not_k_when_measuring_recall",
            "tests/test_ranking.py::test_should_separate_precision_from_recall_when_relevant_set_is_larger_than_k",
        ),
        edit=_replace_once(
            '    return _score(relevant, ranked, _cut("R", k))',
            '    return _score(relevant, ranked, _cut("P", k))',
        ),
    ),
    Mutation(
        name="ranking-graded-gains-reach-trec-eval",
        claim="graded relevance is graded — a 3 outranks a 1, it is not collapsed to binary",
        target=_RANKING,
        guard=(
            "tests/test_ranking.py::test_should_reward_putting_the_heavier_grade_first_when_relevance_is_graded",
            "tests/test_ranking.py::test_should_score_clearly_below_one_when_the_ranking_is_exactly_reversed",
        ),
        edit=_replace_once(
            "    return {_QUERY: {doc: int(gain) for doc, gain in relevant.items()}}",
            "    return {_QUERY: dict.fromkeys(relevant, 1)}",
        ),
    ),
    Mutation(
        name="ranking-average-precision-is-trec-eval-map",
        claim="average_precision is trec_eval's AP, not some other rank measure",
        target=_RANKING,
        guard=(
            "tests/test_ranking.py::test_should_average_precision_over_every_hit_position",
            "tests/test_ranking.py::test_should_divide_average_precision_by_the_full_relevant_set",
        ),
        edit=_replace_once(
            '    return _score(relevant, ranked, load_object("ir_measures", "AP"))',
            '    return _score(relevant, ranked, load_object("ir_measures", "RR"))',
        ),
    ),
    Mutation(
        name="ranking-f1-is-the-harmonic-mean",
        claim="F1@k is the harmonic mean of precision and recall, not their average",
        target=_RANKING,
        guard=("tests/test_ranking.py::test_should_take_the_harmonic_mean_when_measuring_f1",),
        edit=_replace_once(
            "    return 2 * precision * recall / (precision + recall)",
            "    return (precision + recall) / 2",
        ),
    ),
    Mutation(
        name="ranking-means-preserve-numpy-rounding",
        claim="ranking aggregates preserve their published binary64 bit patterns",
        target=_RANKING,
        guard=("tests/test_ranking.py::test_should_preserve_numpy_map_rounding_bit_for_bit",),
        edit=_replace_once(
            '    raw = call_dependency(load_callable("numpy", "mean"), values)',
            '    raw = __import__("statistics").fmean(values)',
        ),
    ),
    # ----------------------------------------------------------------------------------
    # The refusals. Every one of these would otherwise return a number nobody should
    # believe, and an untested refusal branch is where this portfolio's holes have been.
    # ----------------------------------------------------------------------------------
    Mutation(
        name="ranking-refuses-an-empty-relevant-set",
        claim="a query with nothing judged relevant is refused, not scored 0.0",
        target=_RANKING,
        guard=("tests/test_ranking.py::test_should_refuse_when_no_document_is_judged_relevant",),
        edit=_replace_once(
            '        raise EmptyRelevantSet("no document is judged relevant for this query")',
            "        pass",
        ),
    ),
    Mutation(
        name="ranking-refuses-a-fractional-gain",
        claim="a relevance grade of 2.5 is refused, never silently rounded",
        target=_RANKING,
        guard=("tests/test_ranking.py::test_should_refuse_a_fractional_relevance_gain",),
        edit=_replace_once(
            "    if gain != int(gain):\n        raise InvalidRankingRequest",
            "    if gain != int(gain):\n        pass",
        ),
    ),
    Mutation(
        name="ranking-refuses-a-nonfinite-gain",
        claim="a non-finite relevance grade is refused before it reaches trec_eval",
        target=_RANKING,
        guard=(
            "tests/test_ranking.py::test_should_refuse_nonfinite_relevance_gain_without_echoing_id",
        ),
        edit=_replace_once(
            "    if not math.isfinite(gain):\n        raise InvalidRankingRequest\n"
            "    if not 0 <= gain <= MAX_RELEVANCE_GAIN:\n        raise InvalidRankingRequest",
            "    if not math.isfinite(gain):\n        pass\n"
            "    if not 0 <= gain <= MAX_RELEVANCE_GAIN:\n        pass",
        ),
    ),
    Mutation(
        name="ranking-refuses-a-duplicate-document",
        claim="a ranked list holding one document twice is refused, not de-duplicated",
        target=_RANKING,
        guard=(
            "tests/test_ranking.py::test_should_refuse_a_ranked_list_holding_the_same_document_twice",
        ),
        edit=_replace_once(
            '        raise InvalidRankingRequest("ranked list holds the same document id twice")',
            "        pass",
        ),
    ),
    # ----------------------------------------------------------------------------------
    # The agreement face. The statistics are sklearn's and scipy's; every mutation below
    # breaks the ORDINAL CONTRACT around them — which band order was used, whether the
    # distance between bands counts, and whether a degenerate input is allowed to pass
    # itself off as a number.
    # ----------------------------------------------------------------------------------
    Mutation(
        name="agreement-declared-band-order-reaches-kappa",
        claim="the CALLER's band order sets the ordinal distances, not an alphabetical guess",
        target=_AGREEMENT,
        guard=(
            "tests/test_agreement.py::test_should_use_the_declared_band_order_and_not_an_alphabetical_one",
            "tests/test_agreement.py::test_should_separate_a_near_miss_from_a_total_miss_when_percent_agreement_cannot",
        ),
        # Dropping `labels` is the single most plausible edit here, and sklearn does not
        # complain: it sorts the band names instead, so weak/moderate/strong silently
        # becomes moderate < strong < weak and the SAME ratings score +2/3 instead of -1/3.
        edit=_replace_once(
            '            labels=list(scale),\n            weights="quadratic",',
            '            weights="quadratic",',
        ),
    ),
    Mutation(
        name="agreement-kappa-is-quadratically-weighted",
        claim="a near miss costs less than a total miss — the scale is ordinal, not nominal",
        target=_AGREEMENT,
        guard=(
            "tests/test_agreement.py::test_should_separate_a_near_miss_from_a_total_miss_when_percent_agreement_cannot",
            "tests/test_agreement.py::test_should_report_worse_than_chance_when_percent_agreement_looks_healthy",
        ),
        # Unweighted kappa scores the two rater pairs in that first test IDENTICALLY
        # (1/3 each), which is precisely the blindness the weighting exists to remove.
        edit=_replace_once(
            '            weights="quadratic",',
            "            weights=None,",
        ),
    ),
    Mutation(
        name="agreement-tau-b-corrects-for-ties",
        claim="tau-b's tie correction is applied, so a 3-band scale is not scored as ranks",
        target=_AGREEMENT,
        guard=(
            "tests/test_agreement.py::test_should_correct_tau_for_ties_the_three_band_scale_forces",
        ),
        edit=_replace_once(
            'raw = _call("scipy.stats", "kendalltau", ordinals_a, ordinals_b, variant="b")',
            'raw = _call("scipy.stats", "kendalltau", ordinals_a, ordinals_b, variant="c")',
        ),
    ),
    Mutation(
        name="agreement-refuses-a-band-off-the-declared-scale",
        claim="a band the scale does not declare is refused, never silently discarded",
        target=_AGREEMENT,
        guard=(
            "tests/test_agreement.py::test_should_refuse_a_band_that_is_not_on_the_declared_scale",
        ),
        edit=_replace_once(
            "        raise InvalidAgreementRequest("
            'f"bands that are not on the declared scale: {unknown}")',
            "        pass",
        ),
    ),
    Mutation(
        name="agreement-refuses-an-item-graded-twice",
        claim="the same item graded twice is refused, so no one item can vote twice",
        target=_AGREEMENT,
        guard=("tests/test_agreement.py::test_should_refuse_the_same_item_graded_twice",),
        edit=_replace_once(
            '        raise InvalidAgreementRequest("the same item is graded twice")',
            "        pass",
        ),
    ),
    Mutation(
        name="agreement-kappa-is-undefined-not-perfect-when-nobody-varied",
        claim="two raters who used one band throughout score UNDEFINED, never 1.0",
        target=_AGREEMENT,
        guard=(
            "tests/test_agreement.py::test_should_report_kappa_undefined_when_both_raters_never_varied",
            "tests/test_agreement.py::test_should_name_the_reason_when_a_statistic_is_undefined",
        ),
        # 1.0 is the flattering answer and the one a careless fix would reach for: they
        # did, after all, agree on every single item.
        edit=_replace_once(
            "    if len(set(rater_a) | set(rater_b)) < _MIN_LEVELS:\n        return None",
            "    if len(set(rater_a) | set(rater_b)) < _MIN_LEVELS:\n        return 1.0",
        ),
    ),
    Mutation(
        name="agreement-means-preserve-numpy-rounding",
        claim="agreement aggregates preserve their published binary64 bit patterns",
        target=_AGREEMENT,
        guard=(
            "tests/test_agreement.py::test_should_preserve_numpy_weighted_mean_rounding_bit_for_bit",
        ),
        edit=_replace_once(
            '    return _finite_float(_call("numpy", "mean", values))',
            '    return __import__("statistics").fmean(values)',
        ),
    ),
    # ----------------------------------------------------------------------------------
    # The confusion counts. A rate hides which way a system fails; these are the four
    # numbers that say, and two of them are trivially swappable without any total moving.
    # ----------------------------------------------------------------------------------
    Mutation(
        name="metrics-confusion-cells-are-read-in-the-right-order",
        claim="a miss is not read back as a false alarm",
        target=_METRICS,
        guard=("tests/test_metrics.py::test_should_count_each_confusion_cell_by_hand",),
        # sklearn ravels the matrix as tn, fp, fn, tp. Swapping the middle pair keeps
        # every total identical and inverts what the system is failing at.
        edit=_replace_once(
            "    true_negatives, false_positives, false_negatives, true_positives = cast(\n",
            "    true_negatives, false_negatives, false_positives, true_positives = cast(\n",
        ),
    ),
    Mutation(
        name="metrics-confusion-fields-keep-public-order",
        claim="the public confusion tuple stays tp, fp, tn, fn",
        target=_METRICS,
        guard=(
            "tests/test_metrics.py::"
            "test_should_preserve_the_published_confusion_count_value_contract",
        ),
        edit=_replace_once(
            "    true_positives: int\n    false_positives: int\n    true_negatives: int",
            "    true_negatives: int\n    false_positives: int\n    true_positives: int",
        ),
    ),
    Mutation(
        name="metrics-threshold-equality-is-positive",
        claim="a score equal to the threshold is classified as positive",
        target=_METRICS,
        guard=(
            "tests/test_metric_vectors.py::"
            "test_classification_vector_replays_to_the_hand_computed_rates",
        ),
        edit=_replace_once(
            "    return [1 if s >= threshold else 0 for s in y_score]",
            "    return [1 if s > threshold else 0 for s in y_score]",
        ),
    ),
    Mutation(
        name="metrics-false-negative-rate-counts-misses",
        claim="the FNR divides misses by real positives — it is not the false-ALARM rate",
        target=_METRICS,
        guard=(
            "tests/test_metrics.py::test_should_report_the_miss_rate_as_the_false_negative_rate",
            "tests/test_metrics.py::test_should_make_the_false_negative_rate_the_exact_complement_of_recall",
        ),
        edit=_replace_once(
            "    return counts.false_negatives / positives",
            "    return counts.false_positives / (counts.false_positives + counts.true_negatives)",
        ),
    ),
    Mutation(
        name="metrics-refuses-a-label-outside-zero-and-one",
        claim="a label outside {0, 1} is refused, never dropped from the counts",
        target=_METRICS,
        guard=("tests/test_metrics.py::test_should_refuse_a_label_outside_zero_and_one",),
        edit=_replace_once(
            "    if outside:\n        raise InvalidScoreRequest",
            "    if outside:\n        pass",
        ),
    ),
    Mutation(
        name="metrics-missing-extra-has-a-stable-code",
        claim="a base install never leaks the missing module name from an optional face",
        target=_OPTIONAL,
        guard=(
            "tests/test_optional_metrics_boundary.py::"
            "test_should_raise_only_stable_redacted_code_when_metrics_extra_is_missing",
        ),
        edit=_replace_once(
            "    try:\n        return import_module(name)\n"
            "    except Exception:\n        return None",
            "    try:\n        return import_module(name)\n    except Exception:\n        raise",
        ),
    ),
    Mutation(
        name="metrics-successful-optional-imports-are-cached",
        claim="a recovered optional callable stays usable after its package disappears",
        target=_OPTIONAL,
        guard=(
            "tests/test_optional_metrics_boundary.py::"
            "test_should_cache_a_successfully_loaded_exact_callable",
        ),
        edit=_replace_once(
            "@cache\ndef load_callable(module_name: str, name: str) -> OptionalCallable:",
            "def load_callable(module_name: str, name: str) -> OptionalCallable:",
        ),
    ),
    Mutation(
        name="calibration-ece-is-population-weighted",
        claim="ECE weights each non-empty bin by its sample population",
        target=_CALIBRATION,
        guard=(
            "tests/test_calibration.py::"
            "test_should_not_average_reliability_gaps_as_equal_sized_bins",
        ),
        edit=_replace_once(
            "    result = sum(b.count / total * abs(b.mean_predicted - b.fraction_positive) "
            "for b in bins)",
            "    result = sum(abs(b.mean_predicted - b.fraction_positive) for b in bins) "
            "/ len(bins)",
        ),
    ),
    Mutation(
        name="calibration-validates-before-sklearn",
        claim="invalid calibration inputs produce Assay's stable value-free code",
        target=_CALIBRATION,
        guard=(
            "tests/test_metric_resource_boundaries.py::"
            "test_should_reject_calibration_bounds_before_calling_sklearn",
        ),
        edit=_replace_once(
            "    _validate(y_true, y_score, n_bins)",
            "    pass",
        ),
    ),
    Mutation(
        name="uncertainty-validates-before-scipy",
        claim="invalid bootstrap bounds produce Assay's stable value-free code",
        target=_UNCERTAINTY,
        guard=(
            "tests/test_uncertainty.py::"
            "test_should_refuse_invalid_interval_bounds_with_stable_code",
        ),
        edit=_replace_once(
            "    _validate(samples, settings)",
            "    pass",
        ),
    ),
    Mutation(
        name="uncertainty-suppresses-dependency-runtime-warnings",
        claim="a rejected huge finite bootstrap emits only Assay's stable error",
        target=_UNCERTAINTY,
        guard=(
            "tests/test_metric_resource_boundaries.py::"
            "test_should_suppress_dependency_warnings_for_a_rejected_bootstrap",
        ),
        edit=_replace_once(
            '        warnings.simplefilter("ignore", RuntimeWarning)',
            '        warnings.simplefilter("always", RuntimeWarning)',
        ),
    ),
    # ----------------------------------------------------------------------------------
    # The literals the docs promise. A constant asserted only against itself is
    # unguarded: `assert settings.min_samples == settings.min_samples` holds at any value.
    # ----------------------------------------------------------------------------------
    Mutation(
        name="documented-sample-floor-is-30",
        claim="the README's honesty floor of 30 samples is the shipped default",
        target=_SETTINGS,
        guard=("tests/test_settings.py::test_should_use_documented_defaults_when_no_env",),
        edit=_replace_once(
            "    min_samples: _SampleCount = 30", "    min_samples: _SampleCount = 3"
        ),
    ),
    Mutation(
        name="documented-confidence-level-is-95pc",
        claim="the README's '95% interval' is a 95% interval",
        target=_SETTINGS,
        guard=("tests/test_settings.py::test_should_use_documented_defaults_when_no_env",),
        edit=_replace_once(
            "    confidence_level: _Confidence = 0.95", "    confidence_level: _Confidence = 0.5"
        ),
    ),
    Mutation(
        name="documented-ranking-cutoff-is-10",
        claim="the documented first-page depth of 10 is the shipped default k",
        target=_SETTINGS,
        guard=(
            "tests/test_documented_constants.py::test_should_match_documented_default_ranking_cutoff",
        ),
        edit=_replace_once("    ranking_k: _RankingK = 10", "    ranking_k: _RankingK = 4"),
    ),
    # ----------------------------------------------------------------------------------
    # Not a claim about assay's maths — a claim about what assay will install. The
    # quarantine below is the guard that turned PR #21 red by refusing a transitive
    # dependency published hours earlier; lowering it is the one-line edit that would
    # retire it, so the suite has to notice that edit.
    # ----------------------------------------------------------------------------------
    Mutation(
        name="npm-release-quarantine-is-24h",
        claim="a package published minutes ago cannot enter the TypeScript lockfile",
        target=_PNPM_WORKSPACE,
        guard=(
            "tests/test_supply_chain_policy.py::test_new_npm_releases_are_quarantined_for_a_day",
        ),
        edit=_replace_once("minimumReleaseAge: 1440", "minimumReleaseAge: 0"),
    ),
    # ----------------------------------------------------------------------------------
    # Not a claim about assay's maths — a claim about what `npm install @edgeproc/assay`
    # gets. The registry made the empty bootstrap stub `latest`; this post-publish check
    # is what refuses to call a release verified while that is still true.
    # ----------------------------------------------------------------------------------
    Mutation(
        name="npm-latest-must-be-installable",
        claim="a publish cannot verify while npm latest names the empty bootstrap placeholder",
        target=_PUBLISHED_VERIFIER,
        guard=(
            "tests/test_release_contract.py::"
            "test_should_refuse_a_publish_that_leaves_latest_on_the_bootstrap_placeholder",
            "tests/test_release_contract.py::"
            "test_should_refuse_latest_pointing_at_a_version_the_registry_does_not_list",
        ),
        edit=_replace_once(
            "    _verify_selected_tag(tags, version, selected, published)\n"
            "    _verify_default_install(payload, tags)\n",
            "    _verify_selected_tag(tags, version, selected, published)\n",
        ),
    ),
    # ----------------------------------------------------------------------------------
    # The TypeScript metrics face. These run under VITEST, not pytest. The claims
    # are the same claims the Python block above makes, because the two faces are
    # pinned to one another — so the guards have to be able to fail in both languages.
    # The verdict comes from vitest's JSON pass/fail counts; see the module docstring
    # for why its exit code cannot be trusted for this.
    # ----------------------------------------------------------------------------------
    Mutation(
        name="ts-ranking-precision-divides-by-k",
        claim="precision@k divides by k, so unfilled positions are charged for",
        target=_TS_RANKING,
        runner=_VITEST,
        guard=("src/ranking.test.ts::charges for the empty positions when k exceeds the list",),
        edit=_replace_once(
            "  return hitsInTopK(relevant, ranked, k) / k;",
            "  return hitsInTopK(relevant, ranked, k) / ranked.length;",
        ),
    ),
    Mutation(
        name="ts-ranking-recall-is-not-precision",
        claim="recall@k divides by the relevant set, never by k",
        target=_TS_RANKING,
        runner=_VITEST,
        guard=("src/ranking.test.ts::divides by the relevant set, never by k",),
        edit=_replace_once(
            "  return hitsInTopK(relevant, ranked, k) / relevantCount(relevant);",
            "  return hitsInTopK(relevant, ranked, k) / k;",
        ),
    ),
    Mutation(
        name="ts-ranking-scores-only-the-top-k-slice",
        claim="the ranked ORDER is what is scored, not merely the retrieved set",
        target=_TS_RANKING,
        runner=_VITEST,
        guard=(
            "src/ranking.test.ts::counts hits over k",
            "src/ranking.test.ts::scores only the top-k slice, so order changes the answer",
        ),
        edit=_replace_once(
            "  return ranked.slice(0, k).filter((doc) => isRelevant(relevant, doc)).length;",
            "  return ranked.filter((doc) => isRelevant(relevant, doc)).length;",
        ),
    ),
    Mutation(
        name="ts-ranking-f1-is-the-harmonic-mean",
        claim="F1@k is the harmonic mean of precision and recall, not their average",
        target=_TS_RANKING,
        runner=_VITEST,
        guard=("src/ranking.test.ts::takes the harmonic mean, not the average",),
        edit=_replace_once(
            "  return (2 * precision * recall) / (precision + recall);",
            "  return (precision + recall) / 2;",
        ),
    ),
    Mutation(
        name="ts-ranking-rr-is-the-first-hit",
        claim="reciprocal rank uses the FIRST relevant hit, not any later one",
        target=_TS_RANKING,
        runner=_VITEST,
        guard=("src/ranking.test.ts::uses the first hit and not the last when there are several",),
        edit=_replace_once(
            "  const position = ranked.findIndex((doc) => isRelevant(relevant, doc));",
            "  const position = ranked.findLastIndex((doc) => isRelevant(relevant, doc));",
        ),
    ),
    Mutation(
        name="ts-ranking-a-zero-gain-is-not-relevant",
        claim="a judged gain of 0 is judged NOT relevant, it is not merely a low grade",
        target=_TS_RANKING,
        runner=_VITEST,
        guard=("src/ranking.test.ts::treats a judged gain of zero as NOT relevant",),
        edit=_replace_once(
            "  return Object.hasOwn(relevant, doc) && (relevant[doc] as number) > 0;",
            "  return Object.hasOwn(relevant, doc) && (relevant[doc] as number) >= 0;",
        ),
    ),
    Mutation(
        name="ts-ranking-refuses-an-empty-relevant-set",
        claim="a query with nothing judged relevant is refused, not scored 0.0",
        target=_TS_RANKING,
        runner=_VITEST,
        guard=("src/ranking.test.ts::refuses when no document is judged relevant",),
        edit=_delete_throw(
            '    throw new EmptyRelevantSet("no document is judged relevant for this query");'
        ),
    ),
    Mutation(
        name="ts-ranking-refuses-a-fractional-gain",
        claim="a relevance grade of 2.5 is refused, never silently rounded",
        target=_TS_RANKING,
        runner=_VITEST,
        guard=("src/ranking.test.ts::refuses a fractional relevance gain rather than rounding it",),
        edit=_delete_throw(
            "      throw new InvalidRankingRequest(\n"
            "        `relevance gain for '${doc}' is not whole: ${gain}`,\n"
            "      );"
        ),
    ),
    Mutation(
        name="ts-ranking-refuses-a-duplicate-document",
        claim="a ranked list holding one document twice is refused, not de-duplicated",
        target=_TS_RANKING,
        runner=_VITEST,
        guard=("src/ranking.test.ts::refuses a ranked list holding the same document twice",),
        edit=_delete_throw(
            "    throw new InvalidRankingRequest(\n"
            '      "ranked list holds the same document id twice",\n'
            "    );"
        ),
    ),
    Mutation(
        name="ts-metrics-threshold-is-inclusive",
        claim="a score exactly equal to the threshold is a POSITIVE prediction",
        target=_TS_METRICS,
        runner=_VITEST,
        guard=("src/metrics.test.ts::treats a score exactly equal to the threshold as positive",),
        edit=_replace_once(
            "    const predictedPositive = (yScore[index] as number) >= threshold;",
            "    const predictedPositive = (yScore[index] as number) > threshold;",
        ),
    ),
    Mutation(
        name="ts-metrics-fpr-is-not-fnr",
        claim="the false-alarm rate and the miss rate are different numbers",
        target=_TS_METRICS,
        runner=_VITEST,
        guard=(
            "src/metrics.test.ts::keeps the false-positive rate distinct from the "
            "false-negative rate",
        ),
        edit=_replace_once(
            "    falsePositiveRate: ratio(falsePositives, falsePositives + trueNegatives),",
            "    falsePositiveRate: ratio(falseNegatives, falseNegatives + truePositives),",
        ),
    ),
    Mutation(
        name="ts-metrics-zero-division-is-zero-not-nan",
        claim="an undefined rate reports 0, matching sklearn's zero_division, never NaN",
        target=_TS_METRICS,
        runner=_VITEST,
        guard=("src/metrics.test.ts::reports zero, not NaN, when nothing was predicted positive",),
        edit=_replace_once(
            "  return denominator === 0 ? 0 : numerator / denominator;",
            "  return numerator / denominator;",
        ),
    ),
    Mutation(
        name="ts-metrics-refuses-a-single-class",
        claim="a one-class input is refused here exactly as it is refused in Python",
        target=_TS_METRICS,
        runner=_VITEST,
        guard=("src/metrics.test.ts::refuses a single-class label set",),
        edit=_delete_throw(
            '    throw new InvalidScoreRequest("need both classes present for AUC metrics");'
        ),
    ),
    Mutation(
        name="ts-metrics-default-threshold-is-half",
        claim="the documented default decision threshold of 0.5 is the shipped one",
        target=_TS_METRICS,
        runner=_VITEST,
        guard=("src/metrics.test.ts::uses the documented default threshold of 0.5",),
        edit=_replace_once(
            "export const DEFAULT_THRESHOLD = 0.5;", "export const DEFAULT_THRESHOLD = 0.9;"
        ),
    ),
    # ----------------------------------------------------------------------------------
    # The cross-language pin itself. The two mutations below are guarded ONLY by the
    # shared-vector suite, so they answer the question that makes the vectors worth
    # having: if TypeScript's answer drifts away from Python's, does the shared file
    # actually notice? A vector suite nobody has watched fail is decoration.
    # ----------------------------------------------------------------------------------
    Mutation(
        name="ts-cells-match-pythons-sklearn",
        claim="a transposed confusion cell is caught by the vectors Python also replays",
        target=_TS_METRICS,
        runner=_VITEST,
        guard=(
            "src/metricVectors.test.ts::vector one_error_of_each_kind: "
            "the four confusion cells match",
        ),
        edit=_replace_once("      falseNegatives += 1;", "      falsePositives += 1;"),
    ),
    Mutation(
        name="ts-ranking-matches-pythons-trec-eval",
        claim="a recall denominator that drifts from trec_eval's is caught by the vectors",
        target=_TS_RANKING,
        runner=_VITEST,
        guard=(
            "src/metricVectors.test.ts::vector graded_gains_and_a_zero_gain_judgment: "
            "every ranking metric matches",
        ),
        edit=_replace_once(
            "  return Object.values(relevant).filter((gain) => gain > 0).length;",
            "  return Object.values(relevant).length;",
        ),
    ),
    Mutation(
        name="ts-ranking-keeps-a-document-called-proto",
        claim="a document id of '__proto__' survives into the judgments, as it does in Python",
        target=_TS_RANKING,
        runner=_VITEST,
        guard=("src/ranking.test.ts::keeps a document literally called __proto__",),
        edit=_replace_once(
            "  const judgments: Record<string, number> = Object.create(null);",
            "  const judgments: Record<string, number> = {};",
        ),
    ),
    Mutation(
        name="documented-metric-vector-count-is-23",
        claim="the README's 7 ranking + 7 ranking-refusal + 5 classification + 4 "
        "classification-refusal shared metric cases are all still shipped",
        target=_METRIC_VECTORS,
        guard=(
            "tests/test_documented_constants.py::test_should_match_documented_metric_vector_counts",
        ),
        edit=_drop_last_metric_vector,
    ),
)


_COMPOSITION_MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        name="request-interval-contains-declared-point",
        claim="every accepted request interval contains its declared point value",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_contracts.py::"
            "test_should_require_request_intervals_to_contain_their_point_at_every_boundary",
        ),
        edit=_replace_once(
            "    if interval is not None and not interval.low <= value <= interval.high:",
            "    if False:",
        ),
    ),
    Mutation(
        name="composition-honors-normalization-direction",
        claim="lower-is-better endpoints reverse before weighted composition",
        target="src/assay/normalize.py",
        guard=(
            "tests/test_weighted_mean.py::test_should_propagate_weighted_interval_endpoints_in_declared_direction",
        ),
        edit=_replace_once(
            "    if scale.direction is Direction.LOWER_IS_BETTER:",
            "    if scale.direction is Direction.HIGHER_IS_BETTER:",
        ),
    ),
    Mutation(
        name="weighted-mean-divides-by-total-weight",
        claim="each declared weight is divided by the total positive weight",
        target="src/assay/weighted_mean.py",
        guard=(
            "tests/test_weighted_mean.py::test_should_normalize_before_dividing_by_total_weight",
        ),
        edit=_replace_once(
            "    return tuple(finite_output(_weight(component) / total) "
            "for component in request.components)",
            "    return tuple(finite_output(_weight(component)) "
            "for component in request.components)",
        ),
    ),
    Mutation(
        name="weighted-interval-reuses-effective-coefficient",
        claim="point and interval endpoints multiply by the same precomputed coefficient",
        target="src/assay/weighted_mean.py",
        guard=(
            "tests/test_weighted_mean.py::"
            "test_should_reuse_effective_coefficient_for_point_and_interval_endpoints",
        ),
        edit=_replace_once(
            "    return interval_or_none(finite_output(low * coefficient), "
            "finite_output(high * coefficient))",
            "    total = _total_weight(request)\n"
            "    return interval_or_none(finite_output(low * _weight(component) / total), "
            "finite_output(high * _weight(component) / total))",
        ),
    ),
    Mutation(
        name="weighted-aggregate-interval-collapses-after-summation",
        claim="equal aggregate binary64 bounds serialize as a deterministic null interval",
        target="src/assay/weighted_mean.py",
        guard=(
            "tests/test_weighted_mean.py::"
            "test_should_collapse_distinct_row_bounds_after_ordered_summation",
        ),
        edit=_replace_once(
            "    return interval_or_none(low, high)",
            "    return Interval(low=low, high=high)",
        ),
    ),
    Mutation(
        name="weighted-result-requires-total-weight",
        claim="the weighted result wire requires the declared total used for replay",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_result_invariants.py::"
            "test_should_require_weight_total_on_weighted_result_json",
        ),
        edit=_replace_once(
            "    weight_total: _PositiveWeight | None",
            "    weight_total: _PositiveWeight | None = None",
        ),
    ),
    Mutation(
        name="weighted-result-keeps-declared-row-weight",
        claim="every weighted explanation exposes its original positive request weight",
        target="src/assay/weighted_mean.py",
        guard=(
            "tests/test_weighted_mean.py::"
            "test_should_compose_when_a_positive_effective_weight_underflows_to_zero",
        ),
        edit=_replace_once(
            "        declared_weight=_weight(component),", "        declared_weight=1.0,"
        ),
    ),
    Mutation(
        name="weighted-result-keeps-total-weight",
        claim="the weighted result exposes the exact declared-order total",
        target="src/assay/weighted_mean.py",
        guard=(
            "tests/test_weighted_mean.py::test_should_normalize_before_dividing_by_total_weight",
        ),
        edit=_replace_once("        weight_total=total,", "        weight_total=1.0,"),
    ),
    Mutation(
        name="weighted-result-rederives-effective-coefficient",
        claim="a weighted coefficient must equal declared weight divided by total weight",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_result_invariants.py::"
            "test_should_reject_forged_one_component_weighted_coefficient",
        ),
        edit=_replace_once(
            "    return row.operation is Operation.ADD and row.coefficient "
            "== _result_number(weight / total)",
            "    return row.operation is Operation.ADD and 0.0 <= row.coefficient <= 1.0",
        ),
    ),
    Mutation(
        name="weighted-result-allows-representable-coefficient-underflow",
        claim="a positive declared weight may produce canonical zero after binary64 division",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_weighted_mean.py::"
            "test_should_compose_when_a_positive_effective_weight_underflows_to_zero",
        ),
        edit=_replace_once(
            "    return row.operation is Operation.ADD and row.coefficient "
            "== _result_number(weight / total)",
            "    return row.operation is Operation.ADD and 0.0 < row.coefficient "
            "== _result_number(weight / total)",
        ),
    ),
    Mutation(
        name="weighted-result-checks-declared-total",
        claim="weight total must equal the declared-order sum of row weights",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_result_invariants.py::test_should_reject_inconsistent_weight_total_on_copy",
        ),
        edit=_replace_once(
            "    _require_result(total == _result_add(_declared_weights(rows)))",
            "    _require_result(total > 0.0)",
        ),
    ),
    Mutation(
        name="additive-result-rejects-weight-total",
        claim="additive results cannot claim weighted-mean total metadata",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_result_invariants.py::"
            "test_should_reject_weight_total_on_nonweighted_results[_additive]",
        ),
        edit=_replace_once(
            "        and result.weight_total is None\n    )\n\n\ndef _minimum_row",
            "    )\n\n\ndef _minimum_row",
        ),
    ),
    Mutation(
        name="additive-row-rejects-declared-weight",
        claim="additive explanation rows cannot claim weighted-mean request weights",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_result_invariants.py::"
            "test_should_reject_declared_row_weight_on_nonweighted_results[_additive]",
        ),
        edit=_replace_once(
            "        row.normalized is None\n"
            "        and row.declared_weight is None\n"
            "        and row.contribution == contribution",
            "        row.normalized is None\n        and row.contribution == contribution",
        ),
    ),
    Mutation(
        name="minimum-result-rejects-weight-total",
        claim="minimum results cannot claim weighted-mean total metadata",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_result_invariants.py::"
            "test_should_reject_weight_total_on_nonweighted_results[_minimum]",
        ),
        edit=_replace_once(
            "    return result.clamp is not None and result.intercept is None "
            "and result.weight_total is None",
            "    return result.clamp is not None and result.intercept is None",
        ),
    ),
    Mutation(
        name="minimum-row-rejects-declared-weight",
        claim="minimum candidate rows cannot claim weighted-mean request weights",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_result_invariants.py::"
            "test_should_reject_declared_row_weight_on_nonweighted_results[_minimum]",
        ),
        edit=_replace_once(
            "    return row.operation is Operation.ADD and row.coefficient == 1.0 "
            "and row.declared_weight is None",
            "    return row.operation is Operation.ADD and row.coefficient == 1.0",
        ),
    ),
    Mutation(
        name="additive-subtraction-keeps-its-sign",
        claim="a subtract term lowers the running total",
        target="src/assay/additive.py",
        guard=(
            "tests/test_additive.py::test_should_preserve_unbounded_negative_consumer_score_when_policy_is_null",
        ),
        edit=_replace_once(
            "    return finite_output(total - contribution)",
            "    return finite_output(total + contribution)",
        ),
    ),
    Mutation(
        name="additive-terms-run-in-declared-order",
        claim="direct IEEE-754 evaluation never reorders additive terms",
        target="src/assay/additive.py",
        guard=(
            "tests/test_additive.py::test_should_apply_intercept_and_terms_strictly_left_to_right_without_division",
        ),
        edit=_replace_once("    for row in rows:", "    for row in reversed(rows):"),
    ),
    Mutation(
        name="additive-clamps-only-the-final-score",
        claim="an intermediate overshoot is not clamped before later terms run",
        target="src/assay/additive.py",
        guard=(
            "tests/test_additive.py::test_should_clamp_only_after_all_terms_and_preserve_explanations",
        ),
        edit=_replace_once(
            "        total = _apply(total, row.contribution, row.operation)",
            "        total = _final(_apply(total, row.contribution, row.operation), request.clamp)",
        ),
    ),
    Mutation(
        name="minimum-is-not-maximum",
        claim="the limiting candidate is the lowest normalized value",
        target="src/assay/minimum.py",
        guard=(
            "tests/test_minimum.py::test_should_select_lowest_normalized_component_and_explain_every_candidate",
        ),
        edit=_replace_once(
            "    selected = min(rows, key=lambda row: row.contribution)",
            "    selected = max(rows, key=lambda row: row.contribution)",
        ),
    ),
    Mutation(
        name="minimum-ties-keep-first-declared-component",
        claim="equal minima never sort by identifier before selection",
        target="src/assay/minimum.py",
        guard=(
            "tests/test_minimum.py::test_should_choose_first_tied_component_without_sorting_identifiers",
        ),
        edit=_replace_once(
            "    selected = min(rows, key=lambda row: row.contribution)",
            "    selected = min(sorted(rows, key=lambda row: row.id), "
            "key=lambda row: row.contribution)",
        ),
    ),
    Mutation(
        name="weighted-explanation-keeps-raw-value",
        claim="weighted explanations report the original native value",
        target="src/assay/weighted_mean.py",
        guard=(
            "tests/test_weighted_mean.py::test_should_normalize_before_dividing_by_total_weight",
        ),
        edit=_replace_once("        raw=component.value,", "        raw=normalized,"),
    ),
    Mutation(
        name="additive-explanation-keeps-exact-contribution",
        claim="additive explanations report raw times coefficient",
        target="src/assay/additive.py",
        guard=(
            "tests/test_additive.py::test_should_preserve_unbounded_negative_consumer_score_when_policy_is_null",
        ),
        edit=_replace_once(
            "        contribution=_contribution(term),",
            "        contribution=term.value,",
        ),
    ),
    Mutation(
        name="subtract-interval-reverses-term-endpoints",
        claim="subtract uses term high for result low and term low for result high",
        target="src/assay/additive.py",
        guard=(
            "tests/test_additive.py::test_should_propagate_add_and_subtract_interval_endpoints",
        ),
        edit=_replace_once(
            "    return finite_output(low - term_high), finite_output(high - term_low)",
            "    return finite_output(low - term_low), finite_output(high - term_high)",
        ),
    ),
    Mutation(
        name="additive-interval-keeps-declared-order",
        claim="interval endpoint arithmetic advances in declared IEEE-754 term order",
        target="src/assay/additive.py",
        guard=(
            "tests/test_additive.py::test_should_propagate_interval_terms_in_declared_ieee_order",
        ),
        edit=_replace_once(
            "    for term in request.terms:",
            "    for term in reversed(request.terms):",
        ),
    ),
    Mutation(
        name="result-wire-keeps-additive-intercept",
        claim="the additive result exposes the exact intercept required for standalone replay",
        target="src/assay/additive.py",
        guard=(
            "tests/test_result_replay.py::"
            "test_should_replay_additive_point_and_interval_from_result_wire_alone",
        ),
        edit=_replace_once("        intercept=validated.intercept,", "        intercept=0.0,"),
    ),
    Mutation(
        name="result-wire-keeps-additive-clamp-policy",
        claim="the additive result exposes its exact final clamp or unbounded policy",
        target="src/assay/additive.py",
        guard=(
            "tests/test_result_replay.py::"
            "test_should_replay_additive_point_and_interval_from_result_wire_alone",
        ),
        edit=_replace_once("        clamp=validated.clamp,", "        clamp=None,"),
    ),
    Mutation(
        name="result-wire-keeps-contribution-intervals",
        claim="each uncertain additive row exposes the bounds needed for standalone replay",
        target="src/assay/additive.py",
        guard=(
            "tests/test_result_replay.py::"
            "test_should_replay_additive_point_and_interval_from_result_wire_alone",
        ),
        edit=_replace_once(
            "        contribution_interval=interval_or_none(*_term_bounds(term)),",
            "        contribution_interval=None,",
        ),
    ),
    Mutation(
        name="score-result-invariants-run-at-the-boundary",
        claim="every ScoreResult construction and parse enforces method-specific replay",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_result_invariants.py::"
            "test_should_replay_additive_score_from_intercept_policy_and_signed_rows",
        ),
        edit=_replace_once(
            "        _require_result_invariants(self)",
            "        _require_result(True)",
        ),
    ),
    Mutation(
        name="weighted-result-interval-contains-point",
        claim="every weighted contribution lies inside its reported contribution interval",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_result_invariants.py::"
            "test_should_reject_point_outside_contribution_interval_from_direct_input"
            "[weighted_mean]",
        ),
        edit=_replace_once(
            "        0.0 <= interval.low <= row.contribution <= interval.high <= maximum\n"
            "        and interval.low < interval.high",
            "        0.0 <= interval.low < interval.high <= maximum",
        ),
    ),
    Mutation(
        name="additive-result-interval-contains-point",
        claim="every additive contribution lies inside its reported contribution interval",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_result_invariants.py::"
            "test_should_reject_point_outside_contribution_interval_from_direct_input[additive]",
        ),
        edit=_replace_once(
            "    return interval is None or interval.low <= row.contribution <= interval.high",
            "    return True",
        ),
    ),
    Mutation(
        name="minimum-result-interval-contains-point",
        claim="every minimum candidate lies inside its reported contribution interval",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_result_invariants.py::"
            "test_should_reject_point_outside_contribution_interval_from_direct_input[minimum]",
        ),
        edit=_replace_once(
            "        0.0 <= interval.low <= row.contribution <= interval.high <= maximum\n"
            "        and interval.low < interval.high",
            "        0.0 <= interval.low < interval.high <= maximum",
        ),
    ),
    Mutation(
        name="minimum-result-selects-first-actual-minimum",
        claim="minimum result validation requires the selected ID to be the first minimum row",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_result_invariants.py::"
            "test_should_require_minimum_to_select_first_declared_lowest_row",
        ),
        edit=_replace_once(
            "    _require_result(result.selected_component_id == selected.id)",
            "    _require_result(result.selected_component_id is not None)",
        ),
    ),
    Mutation(
        name="public-contract-zero-has-positive-sign-bit",
        claim="all accepted numeric zeros canonicalize before JSON and request hashing",
        target="src/assay/contracts.py",
        guard=(
            "tests/test_contracts.py::"
            "test_should_canonicalize_all_contract_zeros_before_json_and_request_hashing",
        ),
        edit=_replace_once(
            "def _canonical_zero(value: float) -> float:\n"
            "    return 0.0 if value == 0.0 else value",
            "def _canonical_zero(value: float) -> float:\n    return value",
        ),
    ),
    Mutation(
        name="inputs-hash-includes-request-method",
        claim="the closed request method discriminator changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_weighted_mean.py::test_should_hash_every_weighted_request_field_class",),
        edit=_replace_once(
            "def _weighted_token(request: WeightedMeanRequest) -> object:\n"
            "    components = tuple(_component_token(item) for item in request.components)\n"
            "    return (\n"
            "        _PREIMAGE_VERSION,\n"
            "        request.method,",
            "def _weighted_token(request: WeightedMeanRequest) -> object:\n"
            "    components = tuple(_component_token(item) for item in request.components)\n"
            "    return (\n"
            "        _PREIMAGE_VERSION,\n"
            '        "minimum",',
        ),
    ),
    Mutation(
        name="inputs-hash-includes-method-version",
        claim="the caller-declared method version changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_weighted_mean.py::test_should_hash_every_weighted_request_field_class",),
        edit=_replace_once(
            "def _weighted_token(request: WeightedMeanRequest) -> object:\n"
            "    components = tuple(_component_token(item) for item in request.components)\n"
            "    return (\n"
            "        _PREIMAGE_VERSION,\n"
            "        request.method,\n"
            "        request.method_version,",
            "def _weighted_token(request: WeightedMeanRequest) -> object:\n"
            "    components = tuple(_component_token(item) for item in request.components)\n"
            "    return (\n"
            "        _PREIMAGE_VERSION,\n"
            "        request.method,\n"
            '        "northstar-v2",',
        ),
    ),
    Mutation(
        name="inputs-hash-includes-weighted-clamp-policy",
        claim="the explicit normalization clamp policy changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_weighted_mean.py::test_should_hash_every_weighted_request_field_class",),
        edit=_replace_once(
            "def _weighted_token(request: WeightedMeanRequest) -> object:\n"
            "    components = tuple(_component_token(item) for item in request.components)\n"
            "    return (\n"
            "        _PREIMAGE_VERSION,\n"
            "        request.method,\n"
            "        request.method_version,\n"
            "        request.clamp.value,",
            "def _weighted_token(request: WeightedMeanRequest) -> object:\n"
            "    components = tuple(_component_token(item) for item in request.components)\n"
            "    return (\n"
            "        _PREIMAGE_VERSION,\n"
            "        request.method,\n"
            "        request.method_version,\n"
            '        "reject",',
        ),
    ),
    Mutation(
        name="inputs-hash-includes-component-id",
        claim="a component's stable identity changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_weighted_mean.py::test_should_hash_every_weighted_request_field_class",),
        edit=_replace_once("        component.id,", '        "quality",'),
    ),
    Mutation(
        name="inputs-hash-includes-component-label",
        claim="display-label changes alter the complete request digest",
        target="src/assay/composite.py",
        guard=("tests/test_weighted_mean.py::test_should_hash_every_weighted_request_field_class",),
        edit=_replace_once("        component.label,", '        "Quality",'),
    ),
    Mutation(
        name="inputs-hash-includes-component-value",
        claim="a component's native value changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_weighted_mean.py::test_should_hash_every_weighted_request_field_class",),
        edit=_replace_once(
            "        _float_token(component.value),",
            "        _float_token(0.25),",
        ),
    ),
    Mutation(
        name="inputs-hash-includes-scale-minimum",
        claim="a native scale minimum changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_weighted_mean.py::test_should_hash_every_weighted_request_field_class",),
        edit=_replace_once(
            "(_float_token(scale.minimum), _float_token(scale.maximum), scale.direction.value)",
            "(_float_token(0.0), _float_token(scale.maximum), scale.direction.value)",
        ),
    ),
    Mutation(
        name="inputs-hash-includes-scale-maximum",
        claim="a native scale maximum changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_weighted_mean.py::test_should_hash_every_weighted_request_field_class",),
        edit=_replace_once(
            "(_float_token(scale.minimum), _float_token(scale.maximum), scale.direction.value)",
            "(_float_token(scale.minimum), _float_token(1.0), scale.direction.value)",
        ),
    ),
    Mutation(
        name="inputs-hash-includes-scale-direction",
        claim="a native scale direction changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_weighted_mean.py::test_should_hash_every_weighted_request_field_class",),
        edit=_replace_once(
            "(_float_token(scale.minimum), _float_token(scale.maximum), scale.direction.value)",
            '(_float_token(scale.minimum), _float_token(scale.maximum), "higher_is_better")',
        ),
    ),
    Mutation(
        name="inputs-hash-includes-component-interval",
        claim="a component uncertainty interval changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_weighted_mean.py::test_should_hash_every_weighted_request_field_class",),
        edit=_replace_once("        _interval_token(component.interval),", "        None,"),
    ),
    Mutation(
        name="inputs-hash-includes-component-weight",
        claim="a component weight changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_weighted_mean.py::test_should_hash_every_weighted_request_field_class",),
        edit=_replace_once("        weight,", "        _float_token(1.0),"),
    ),
    Mutation(
        name="inputs-hash-includes-component-order",
        claim="component declaration order changes the complete digest",
        target="src/assay/composite.py",
        guard=(
            "tests/test_consumer_conformance.py::test_should_replay_every_literal_consumer_result_exactly",
        ),
        edit=_replace_once(
            "def _weighted_token(request: WeightedMeanRequest) -> object:\n"
            "    components = tuple(_component_token(item) for item in request.components)",
            "def _weighted_token(request: WeightedMeanRequest) -> object:\n"
            "    components = tuple(_component_token(item) "
            "for item in reversed(request.components))",
        ),
    ),
    Mutation(
        name="inputs-hash-includes-additive-policy",
        claim="an additive final clamp or unbounded policy changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_additive.py::test_should_hash_every_additive_request_field_class",),
        edit=_replace_once(
            "    policy = None if request.clamp is None else request.clamp.value",
            "    policy = None",
        ),
    ),
    Mutation(
        name="inputs-hash-includes-additive-intercept",
        claim="the additive intercept changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_additive.py::test_should_hash_every_additive_request_field_class",),
        edit=_replace_once(
            "        _float_token(request.intercept),",
            "        _float_token(0.0),",
        ),
    ),
    Mutation(
        name="inputs-hash-includes-term-id",
        claim="an additive term's stable identity changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_additive.py::test_should_hash_every_additive_request_field_class",),
        edit=_replace_once("        term.id,", '        "signal",'),
    ),
    Mutation(
        name="inputs-hash-includes-term-label",
        claim="an additive term's display label changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_additive.py::test_should_hash_every_additive_request_field_class",),
        edit=_replace_once("        term.label,", '        "Signal",'),
    ),
    Mutation(
        name="inputs-hash-includes-term-value",
        claim="an additive term's raw value changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_additive.py::test_should_hash_every_additive_request_field_class",),
        edit=_replace_once("        _float_token(term.value),", "        _float_token(0.25),"),
    ),
    Mutation(
        name="inputs-hash-includes-term-coefficient",
        claim="an additive term's coefficient changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_additive.py::test_should_hash_every_additive_request_field_class",),
        edit=_replace_once(
            "        _float_token(term.coefficient),",
            "        _float_token(0.5),",
        ),
    ),
    Mutation(
        name="inputs-hash-includes-term-operation",
        claim="add versus subtract changes the complete additive request digest",
        target="src/assay/composite.py",
        guard=("tests/test_additive.py::test_should_hash_every_additive_request_field_class",),
        edit=_replace_once("        term.operation.value,", '        "add",'),
    ),
    Mutation(
        name="inputs-hash-includes-term-interval",
        claim="an additive term uncertainty interval changes the complete digest",
        target="src/assay/composite.py",
        guard=("tests/test_additive.py::test_should_hash_every_additive_request_field_class",),
        edit=_replace_once("        _interval_token(term.interval),", "        None,"),
    ),
    Mutation(
        name="inputs-hash-includes-term-order",
        claim="additive term declaration order changes the complete digest",
        target="src/assay/composite.py",
        guard=(
            "tests/test_consumer_conformance.py::test_should_replay_every_literal_consumer_result_exactly",
        ),
        edit=_replace_once(
            "    terms = tuple(_term_token(item) for item in request.terms)",
            "    terms = tuple(_term_token(item) for item in reversed(request.terms))",
        ),
    ),
    Mutation(
        name="composition-vectors-are-non-vacuous",
        claim="an empty composition vector file cannot report successful replay",
        target=_COMPOSITION_VECTORS,
        guard=(
            "tests/test_consumer_conformance.py::test_should_ship_every_named_consumer_oracle_without_personal_data",
        ),
        edit=_zero_composition_vectors,
    ),
)


_TS_COMPOSITION_MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        name="ts-composition-honors-normalization-direction",
        claim="lower-is-better normalization reverses the native scale",
        target=_TS_NORMALIZE,
        runner=_VITEST,
        guard=("src/normalize.test.ts::executes every shared Python normalization vector",),
        edit=_replace_once(
            'scale.direction === "lower_is_better"',
            'scale.direction === "higher_is_better"',
        ),
    ),
    Mutation(
        name="ts-additive-add-is-not-subtract",
        claim="add and subtract terms use distinct ordered arithmetic",
        target=_TS_ADDITIVE,
        runner=_VITEST,
        guard=(_TS_ADDITIVE_ORDER_GUARD,),
        edit=_replace_once(
            'operation === "add" ? total + amount : total - amount',
            'operation === "add" ? total - amount : total + amount',
        ),
    ),
    Mutation(
        name="ts-additive-keeps-declaration-order",
        claim="additive binary64 arithmetic follows declaration order",
        target=_TS_ADDITIVE,
        runner=_VITEST,
        guard=(_TS_ADDITIVE_ORDER_GUARD,),
        edit=_replace_once(
            "  for (const item of rows)\n"
            "    total = apply(total, item.contribution, item.operation);",
            "  for (const item of [...rows].reverse())\n"
            "    total = apply(total, item.contribution, item.operation);",
        ),
    ),
    Mutation(
        name="ts-additive-clamps-only-after-all-terms",
        claim="the final clamp is applied after every ordered contribution",
        target=_TS_ADDITIVE,
        runner=_VITEST,
        guard=(
            "src/compositionVectors.test.ts::clamps additive output only after every "
            "ordered contribution",
        ),
        edit=_replace_once(
            "    total = apply(total, item.contribution, item.operation);",
            "    total = final(apply(total, item.contribution, item.operation), request);",
        ),
    ),
    Mutation(
        name="ts-additive-subtraction-reverses-interval-endpoints",
        claim="subtraction uses the high endpoint for the low aggregate bound",
        target=_TS_ADDITIVE,
        runner=_VITEST,
        guard=(_TS_ADDITIVE_ORDER_GUARD,),
        edit=_replace_once(
            ': [apply(low, termHigh, "subtract"), apply(high, termLow, "subtract")];',
            ': [apply(low, termLow, "subtract"), apply(high, termHigh, "subtract")];',
        ),
    ),
    Mutation(
        name="ts-additive-interval-collapses-after-final-clamp",
        claim="equal final endpoints serialize as a deterministic null interval",
        target=_TS_ADDITIVE,
        runner=_VITEST,
        guard=(
            "src/additive.test.ts::collapses the final interval only after clamping its endpoints",
        ),
        edit=_replace_once(
            "  return result.low === result.high ? null : result;",
            "  return result;",
        ),
    ),
    Mutation(
        name="ts-minimum-ties-select-first-declared",
        claim="minimum ties keep the first declared component",
        target=_TS_MINIMUM,
        runner=_VITEST,
        guard=(
            "src/minimum.test.ts::selects the first declared minimum and propagates "
            "candidate bounds",
        ),
        edit=_replace_once(
            "    if (candidate.contribution < selected.contribution) selected = candidate;",
            "    if (candidate.contribution <= selected.contribution) selected = candidate;",
        ),
    ),
    Mutation(
        name="ts-minimum-selected-score-is-candidate-score",
        claim="minimum score is the selected candidate contribution",
        target=_TS_MINIMUM,
        runner=_VITEST,
        guard=("src/minimum.test.ts::supports mixed deterministic and uncertain candidates",),
        edit=_replace_once(
            "    score: selected.contribution,",
            "    score: rows.at(-1)?.contribution ?? Number.NaN,",
        ),
    ),
    Mutation(
        name="ts-minimum-150k-selection-is-bounded-arity",
        claim="minimum selection never spreads untrusted component counts into a call",
        target=_TS_MINIMUM,
        runner=_VITEST,
        guard=(
            "src/minimum.test.ts::composes a 150k-component accepted request without an "
            "argument-limit failure",
        ),
        edit=_replace_once(
            "  const selected = firstMinimum(rows);",
            "  const selectedScore = Math.min(...rows.map((item) => item.contribution));\n"
            "  const selected = rows.find((item) => item.contribution === selectedScore) "
            "as ExplainedComponent;",
        ),
    ),
    Mutation(
        name="ts-request-hash-includes-term-order",
        claim="additive declaration order changes the request digest",
        target=_TS_REQUEST_HASH,
        runner=_VITEST,
        guard=(_TS_VECTOR_REPLAY_GUARD,),
        edit=_replace_once(
            "    request.terms.map(termToken),",
            "    [...request.terms].reverse().map(termToken),",
        ),
    ),
    Mutation(
        name="ts-request-hash-includes-component-order",
        claim="component declaration order changes the request digest",
        target=_TS_REQUEST_HASH,
        runner=_VITEST,
        guard=(_TS_VECTOR_REPLAY_GUARD,),
        edit=_replace_once(
            "    request.components.map(componentToken),",
            "    [...request.components].reverse().map(componentToken),",
        ),
    ),
    Mutation(
        name="ts-result-requires-selected-minimum-id",
        claim="minimum replay rejects a forged selected component id",
        target=_TS_CONTRACTS,
        runner=_VITEST,
        guard=("src/contractBoundaries.test.ts::rejects additive and minimum replay mutations",),
        edit=_replace_once(
            "    result.selected_component_id !== selected.id ||\n"
            "    result.score !== selected.contribution",
            "    result.score !== selected.contribution",
        ),
    ),
    Mutation(
        name="ts-result-interval-totality-is-checked",
        claim="result replay requires null exactly when aggregate bounds collapse",
        target=_TS_CONTRACTS,
        runner=_VITEST,
        guard=(
            "src/contractBoundaries.test.ts::rejects weighted metadata, row, score, and "
            "interval mutations",
        ),
        edit=_replace_once(
            "  if (expected === null || expected[0] === expected[1]) return actual === null;",
            "  if (expected === null || expected[0] === expected[1]) return true;",
        ),
    ),
    Mutation(
        name="ts-minimum-result-replay-scan-is-bounded-arity",
        claim="minimum result validation scans large component sets without argument spreading",
        target=_TS_CONTRACTS,
        runner=_VITEST,
        guard=(
            "src/minimum.test.ts::keeps composition and result replay minimum scans bounded-arity",
        ),
        edit=_replace_once(
            "  const selected = firstMinimum(result.components);",
            "  const score = Math.min(...result.components.map((row) => row.contribution));\n"
            "  const selected = result.components.find((row) => row.contribution === score) "
            "as ExplainedComponent;",
        ),
    ),
)


def _is_allowed_assay_target(target: str) -> bool:
    """Admit Assay source plus the two exact Assay-owned vector files."""
    vector_targets = frozenset((_METRIC_VECTORS, _COMPOSITION_VECTORS))
    return target.startswith("src/assay/") or target in vector_targets


def _is_active_assay_mutation(mutation: Mutation) -> bool:
    """Keep Assay scoring guards in both runtimes plus the two exact release guards."""
    return (
        mutation.runner == _VITEST
        or _is_allowed_assay_target(mutation.target)
        or mutation.target in (_PNPM_WORKSPACE, _PUBLISHED_VERIFIER)
    )


MUTATIONS = (
    *filter(_is_active_assay_mutation, _MIXED_PRODUCT_MUTATIONS),
    *_COMPOSITION_MUTATIONS,
    *_TS_COMPOSITION_MUTATIONS,
)


@dataclass(frozen=True)
class Result:
    """One mutation's two exit codes, and what they mean."""

    mutation: Mutation
    before: int
    after: int

    @property
    def held(self) -> bool:
        """The guard was green, then went red. Nothing else counts."""
        return self.before == _ALL_PASSED and self.after == _TESTS_FAILED

    @property
    def verdict(self) -> str:
        if self.before != _ALL_PASSED:
            return f"BASELINE NOT GREEN (exit {self.before}) — the guard was not passing to start"
        if self.after == _TESTS_FAILED:
            return "RED — the guard fired"
        if self.after == _ALL_PASSED:
            return "SURVIVED — the guard is blind to this break"
        if self.after == _NOTHING_COLLECTED:
            return "NOTHING COLLECTED — the named guard does not exist"
        return f"CRASHED (exit {self.after}) — not evidence either way"


def _pytest(node_ids: Sequence[str]) -> int:
    """Run pytest and return ONLY its exit code — stdout is for the reader, not the verdict.

    ``--no-cov`` because addopts carry a 90% coverage floor: a subset of the suite would
    exit 1 on coverage alone, and "red" would stop meaning "the guard fired"."""
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "--no-cov", "-p", "no:cacheprovider", "-q", *node_ids],
        cwd=_ROOT,
        check=False,
    )
    return completed.returncode


def _typescript() -> int:
    """Run the complete locked TypeScript suite and return its process exit code."""
    completed = subprocess.run(  # noqa: S603
        ["pnpm", "--dir", str(_TS), "test"],  # noqa: S607 - exact pinned CLI verified below
        cwd=_ROOT,
        check=False,
    )
    return completed.returncode


def _tool_version(command: str) -> str:
    completed = subprocess.run(  # noqa: S603
        [command, "--version"], capture_output=True, text=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def _require_toolchain() -> bool:
    node = _tool_version("node")
    pnpm = _tool_version("pnpm")
    if (node, pnpm) == (_NODE_VERSION, _PNPM_VERSION):
        return True
    print(
        f"REFUSING: mutations require Node {_NODE_VERSION} / pnpm {_PNPM_VERSION}; "
        f"got {node} / {pnpm}"
    )
    return False


def _tree_snapshot() -> str:
    """Hash the complete tracked diff and untracked-path inventory."""
    commands = (
        ["git", "diff", "--binary", "HEAD", "--", "."],
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
    )
    digest = hashlib.sha256()
    for command in commands:
        completed = subprocess.run(command, cwd=_ROOT, capture_output=True, check=True)  # noqa: S603
        digest.update(completed.stdout)
    for relative in _generated_inventory(_ROOT):
        digest.update(relative.encode())
        digest.update((_ROOT / relative).read_bytes())
    return digest.hexdigest()


def _generated_inventory(root: Path) -> tuple[str, ...]:
    """List ignored release products without traversing managed dependencies."""
    files = (
        path
        for relative in _GENERATED_ROOTS
        for path in (root / relative).rglob("*")
        if path.is_file()
    )
    return tuple(sorted(path.relative_to(root).as_posix() for path in files))


def _split_guards(guard: Sequence[str]) -> tuple[list[str], str]:
    """``file::test name`` pairs -> (unique files, one alternation pattern).

    The names are regex-escaped because ``--testNamePattern`` is a JavaScript RegExp:
    an unescaped ``(`` in a test title would silently change which tests match."""
    files, names = [], []
    for entry in guard:
        path, _, name = entry.partition("::")
        if path not in files:
            files.append(path)
        names.append(_escape_for_js_regex(name))
    return files, "|".join(names)


def _escape_for_js_regex(name: str) -> str:
    return "".join(f"\\{ch}" if ch in "()[]{}.*+?^$|/\\" else ch for ch in name)


def _vitest_counts(report: Path) -> tuple[int, int]:
    """Read (passed, failed) out of the JSON reporter's output.

    A missing or unparseable file means the runner never produced a report — a
    misspelled reporter name does exactly that, and it is the failure mode that once
    let a sibling harness score twelve crashed runs as green. It raises here."""
    if not report.exists():
        raise HarnessError(f"vitest wrote no JSON report to {report}; no verdict is possible")
    data = json.loads(report.read_text(encoding="utf-8"))
    return int(data["numPassedTests"]), int(data["numFailedTests"])


def _vitest_verdict(passed: int, failed: int) -> int:
    """Map vitest's counts onto pytest's exit codes, so one report holds both.

    ``vitest run -t <no match>`` exits 0 with every test in the file counted as
    "total" and none of them run, so the exit code cannot distinguish a passing guard
    from an absent one. Zero passes and zero failures means nothing ran."""
    if failed > 0:
        return _TESTS_FAILED
    if passed == 0:
        return _NOTHING_COLLECTED
    return _ALL_PASSED


def _vitest_command(files: Sequence[str], pattern: str, report: Path) -> list[str]:
    """Launch vitest from the already locked and installed TypeScript dependency tree."""
    return [
        str(_TS / "node_modules/.bin/vitest"),
        "run",
        *files,
        "--testNamePattern",
        pattern,
        "--reporter=json",
        f"--outputFile={report}",
    ]


def _vitest(guard: Sequence[str]) -> int:
    """Run the named vitest guards and return a pytest-shaped exit code."""
    files, pattern = _split_guards(guard)
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "vitest.json"
        subprocess.run(  # noqa: S603
            _vitest_command(files, pattern, report), cwd=_TS, check=False
        )
        return _vitest_verdict(*_vitest_counts(report))


def _run_guard(mutation: Mutation) -> int:
    return _vitest(mutation.guard) if mutation.runner == _VITEST else _pytest(mutation.guard)


def _invalidate_bytecode(path: Path) -> None:
    """Remove stale bytecode after both mutation and restore writes."""
    if path.suffix != ".py":
        return
    for pyc in (path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"):
        pyc.unlink()


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    _invalidate_bytecode(path)


def _apply(path: Path, mutation: Mutation, original: str) -> None:
    """Mutate the file, then read it back off disk to prove the mutation landed."""
    mutated = mutation.edit(original)
    if mutated == original:
        raise MutationNotAppliedError("the edit produced identical text")
    _write(path, mutated)
    if path.read_text(encoding="utf-8") != mutated:
        raise MutationNotAppliedError(f"{mutation.target} on disk does not hold the mutation")


def _restore(path: Path, original: str) -> None:
    _write(path, original)
    if path.read_text(encoding="utf-8") != original:
        raise MutationNotAppliedError(f"restore failed: {path} does not match its original bytes")


def _check(mutation: Mutation) -> Result:
    path = _ROOT / mutation.target
    original = path.read_text(encoding="utf-8")
    print(f"\n>>> {mutation.name}\n    claim:  {mutation.claim}\n    break:  {mutation.target}")
    before = _run_guard(mutation)
    try:
        _apply(path, mutation, original)
        after = _run_guard(mutation)
    finally:
        _restore(path, original)
    return Result(mutation=mutation, before=before, after=after)


def _targets() -> tuple[str, ...]:
    return tuple(sorted({mutation.target for mutation in MUTATIONS}))


def _require_clean_targets() -> bool:
    """Refuse to edit a file that already has uncommitted work in it."""
    completed = subprocess.run(  # noqa: S603
        ["git", "status", "--porcelain", "--", *_targets()],  # noqa: S607
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return True
    print(f"REFUSING: uncommitted changes in files this harness edits:\n{completed.stdout}")
    return False


def _report(results: Sequence[Result]) -> None:
    print("\n" + "=" * 108)
    print(f"{'mutation':<44}{'before':>8}{'after':>7}   verdict")
    print("-" * 108)
    for result in results:
        print(f"{result.mutation.name:<44}{result.before:>8}{result.after:>7}   {result.verdict}")
    print("=" * 108)


def _restore_note(restored: tuple[int, int], tree_clean: bool) -> str:
    return "green" if restored == (_ALL_PASSED, _ALL_PASSED) and tree_clean else "NOT GREEN"


def _summarise(results: Sequence[Result], restored: tuple[int, int], tree_clean: bool) -> int:
    """Exit non-zero if any guard survived its break, or if restore left the suite red."""
    fired = [result for result in results if result.held]
    print(f"\n{len(fired)}/{len(results)} guards fired when their claim was broken.")
    print(
        f"complete suites after restore: pytest={restored[0]}, pnpm={restored[1]}; "
        f"whole-tree exact={tree_clean} ({_restore_note(restored, tree_clean)})"
    )
    if len(fired) < len(results) or not tree_clean:
        return _TESTS_FAILED
    return max(restored)


def main() -> int:
    if not _require_toolchain() or not _require_clean_targets():
        return _TESTS_FAILED
    initial_tree = _tree_snapshot()
    print("baseline — both complete suites, unmutated")
    baseline = (_pytest(()), _typescript())
    if baseline != (_ALL_PASSED, _ALL_PASSED):
        print(f"REFUSING: baseline suites are not green: pytest={baseline[0]}, pnpm={baseline[1]}")
        return _TESTS_FAILED
    results = [_check(mutation) for mutation in MUTATIONS]
    _report(results)
    print("\ngreen after restore — both complete suites again")
    restored = (_pytest(()), _typescript())
    return _summarise(results, restored, _tree_snapshot() == initial_tree)


if __name__ == "__main__":
    raise SystemExit(main())
