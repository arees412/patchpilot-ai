# Deterministic evaluation

The evaluation harness demonstrates control flow with fresh local fixture repositories. It is
offline, deterministic, inspectable, and free of model calls or paid services.

## Scenarios

1. `python-bug-fix` creates an incorrect `add` implementation, verifies retrieval of the intended
   source, applies a hash-checked structured patch in a copy, and runs the targeted pytest file.
2. `typescript-api-change` changes a greeting contract, retrieves the source, runs a declared
   typecheck script, and runs a Node test.
3. `unsafe-request` classifies repository deletion and force push as denied and executes neither.
4. `approval-required` classifies dependency installation as approval-gated and executes nothing.
5. `test-failure-repair` feeds real structured failure state into the repair loop and verifies one
   successful attempt under a maximum of three.

Run all scenarios with:

```console
uv run patchpilot eval
```

The JSON result contains one boolean and plain evidence details per scenario. A failed scenario
does not become a passing claim. The harness does not report SWE-bench, model quality, production
usage, cost, or generalized coding capability.

## Test suite

The pytest suite separately covers repository snapshots and maps, symbols, retrieval, plans,
invalid lifecycle transitions, file and command confinement, dangerous commands, approvals,
timeouts, output bounds, patch transactions, scope and hash failures, test planning, validation
failures, repair bounds, risk, secret handling, PR drafts, persistence, evidence integrity,
cancellation, audit sanitization, CLI commands, and Python/TypeScript end-to-end fixtures.

The symlink-escape test is skipped for ordinary Windows accounts that cannot create symlinks. It
runs on Linux CI.

## Interpreting results

These tests show that the checked-in implementation behaved as asserted in controlled fixtures.
They do not prove security, correctness on arbitrary repositories, model performance, or production
reliability. Risk scores are deterministic rules, not calibrated probabilities.
