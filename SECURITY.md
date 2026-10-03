# Security policy

> **TL;DR:** Please report vulnerabilities privately. Do not include private user data, signing
> keys, registry credentials, or other secrets in an issue or proof.

## Supported versions

Assay's Python `0.5.0.dev5` and npm `0.5.0-dev.5` versions are the authorized prerelease pair for
the future stable 0.5.0 line. Install only exact versions that both registries serve with provenance.
Security fixes continue on `main` and may require a newer development release.

## Report a vulnerability

Use GitHub's private security-advisory flow for `hseshadr/assay`. Include the affected interface,
the smallest safe reproduction, expected behavior, observed behavior, and likely impact. If the
private advisory flow is unavailable, contact `harish.seshadri@gmail.com` without attaching secrets
or personal data. You can expect an acknowledgement within seven days.

## Security boundaries

Assay validates and combines caller-provided measurements. It does not prove that an input is true,
sign output, establish provenance, or provide a tamper-evident ledger. Applications remain
responsible for authorization, source-data quality, retention, and any evidence-sealing policy.

PyPI and npm authenticate with short-lived GitHub OIDC identities; no long-lived registry token is
available to the workflow. A manual default-branch Dagger candidate must first prove exact equality
of the peeled release tag, protected `main`, expected SHA, source versions, and hosted Dagger gate.
The npm lane uses the repository- and workflow-bound trusted publisher only for a source-free
`npm publish --provenance --ignore-scripts`. The Python lane uses the official PyPI publisher.

Builds, tests, dependency audits, snapshot and full-history secret scans, benchmarks, artifact
verification, and registry preflight run without publishing authority. The privileged jobs download
only the checksum-bound candidate; they do not check out, build, test, install, or run free-form
shell. Missing registry bytes publish only that lane, exact provenance-bound bytes skip it, and any
mismatch fails closed. The authorized release window must exclude concurrent external publishers
because registries do not expose a compare-and-swap primitive for the final irreversible write.
