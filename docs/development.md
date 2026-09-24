# Development guide

## Setup

PatchPilot requires Python 3.12 or newer, Git, and uv. Node.js is needed for the TypeScript fixture.
Docker is optional and is not required by CI.

```console
uv sync --extra dev
uvx pre-commit install
```

## Local verification

Run the same independent checks used by CI:

```console
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
uv run pytest -q tests/test_policy_and_sandbox.py
uv run patchpilot eval
uv run python -m build
uv run python scripts/check_docs.py
uv run python scripts/check_mermaid.py
uv run python scripts/secret_scan.py
```

Then verify `uv run python -c "import patchpilot"`, `uv run patchpilot --help`, and a clean
`git status --short` after removing ignored build output if desired.

## Code organization

- `models.py` contains immutable boundary types.
- `repository.py` and `retrieval.py` build and query repository intelligence.
- `provider.py`, `planning.py`, and `state_machine.py` own decision contracts.
- `policy.py`, `paths.py`, and `sandbox.py` enforce execution boundaries.
- `patching.py`, `validation.py`, `repair.py`, `review.py`, and `risk.py` create and assess changes.
- `approvals.py`, `git_safety.py`, and `github_adapter.py` govern human and publication boundaries.
- `persistence.py`, `evidence.py`, `observability.py`, and `cancellation.py` preserve run state.
- `orchestrator.py` composes the workflow; `cli.py` exposes it.
- `evaluation.py` defines transparent offline fixtures.

## Adding a command

Add a narrow policy classification and tests before invoking a new executable. Prefer exact token
sequences. Do not add a general shell escape or pass user input through a shell string. Decide
whether the action is safe, approval-gated, or permanently denied and document the residual risk.

## Adding a language

Implement detection, a safe parser or honest fallback, test discovery, ordered validation, fixture
coverage, and documentation. Do not list a language as supported because its extension is merely
inventoried.

## Pull requests

Keep commits coherent, explain security-boundary changes, include real validation output, and avoid
benchmark or production claims without reproducible evidence. The PR template captures the required
review information.
