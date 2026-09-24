# Decision records

## ADR-001: Independent implementation

PatchPilot is a new repository with original code and history. Public SWE-agent projects informed
architecture research only. This keeps provenance clear and avoids inherited branding, prompts,
tests, claims, and maintenance assumptions.

## ADR-002: Deterministic provider in core and CI

The provider protocol separates repository facts from model reasoning. CI uses only
`DeterministicAgentProvider`, so tests need no credentials, paid services, model availability, or
network calls. Optional external adapters must remain outside the required validation path.

## ADR-003: Network disabled by default

The Docker invocation uses `--network none`, and download tools are denied. Package installation
requires approval. The local fixture adapter cannot independently prove OS-level network denial, so
its documentation is intentionally narrower.

## ADR-004: Git publication disabled by default

The autonomous core can inspect Git and produce a PR draft. Push, merge, force push, tags, releases,
issue closure, and comments are unavailable. Publication is an explicit operator action.

## ADR-005: Bounded repair loops

Repairs default to at most three attempts and consume recorded validation evidence. This prevents
unbounded autonomous loops and makes retry counts observable.

## ADR-006: Patch-scoped approvals

An approval identifies its task, plan, action, and patch hash, with state and optional expiry. Any
changed patch needs a new approval, preventing stale authorization reuse.

## ADR-007: Lightweight persistence

SQLite provides durable local tasks, runs, analyses, plans, transitions, approvals, validation,
audit, and evidence with minimal operations overhead. Distributed scheduling and high availability
are intentionally deferred.

## ADR-008: Native AST plus honest fallback

Python uses its language-native AST. JavaScript and TypeScript use a deterministic conservative
symbol/import fallback suitable for retrieval hints. Full semantic JS/TS parsing is not claimed.

## ADR-009: CLI before API or frontend

The CLI exercises the complete backend contract while keeping the attack surface and maintenance
cost small. An API or UI should be added only for a concrete deployment use case.
