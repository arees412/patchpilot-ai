# Architecture references and integrity

PatchPilot is an independent implementation. The resources below were used to compare architecture
boundaries and operating models. No source code, tests, prompts, README content, commit history,
branding, or benchmark claims from these projects were copied into PatchPilot.

## Software-engineering agents

- [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent): architectural reference
  for a small agent/environment split and controlled command loop.
- [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands): architectural reference for
  extensible agent and runtime boundaries.
- [OpenHands agent architecture](https://github.com/OpenHands/docs/blob/main/sdk/arch/agent.mdx):
  reference for separating agent logic from execution.
- [OpenHands runtime architecture](https://github.com/OpenHands/docs/blob/main/openhands/usage/architecture/runtime.mdx):
  reference for runtime isolation concepts.
- [OpenHands SDK design](https://github.com/OpenHands/docs/blob/main/sdk/arch/design.mdx): reference
  for typed modular SDK composition.

## Primary technical references

- [Docker run reference](https://docs.docker.com/reference/cli/docker/container/run/) and
  [resource constraints](https://docs.docker.com/engine/containers/resource_constraints/):
  container invocation and bounded-resource controls.
- [Docker rootless mode](https://docs.docker.com/engine/security/rootless/): operator-side hardening
  reference, not a guarantee made by PatchPilot.
- [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use):
  least privilege, immutable action pins, and untrusted input considerations.
- [GitHub `pull_request_target` security](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target):
  reference for avoiding privileged execution of untrusted pull-request code.
- [Git diff](https://git-scm.com/docs/git-diff): canonical diff behavior.
- [Tree-sitter](https://tree-sitter.github.io/tree-sitter/): researched as a future parsing option;
  not currently a runtime dependency.
- [pytest usage](https://docs.pytest.org/en/stable/how-to/usage.html): targeted node selection and
  collection behavior.

References describe sources consulted, not incorporated code or endorsement.
