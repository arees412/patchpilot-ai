# Security model

PatchPilot assumes that engineering tasks, repository files, model output, patches, tests, scripts,
and command output may be hostile. Its controls reduce exposure and make decisions reviewable.
They do not make arbitrary code safe.

> PatchPilot does not guarantee that arbitrary third-party repositories are safe to execute.
> Sandboxing is a defense layer, not proof that untrusted code is harmless.

## Threats and controls

| Threat | Implemented control | Residual risk |
| --- | --- | --- |
| Prompt injection in repository files | Repository text is data; policy and state rules are programmatic and provider-independent | A model adapter can still make poor proposals, so patch review remains required |
| Malicious tests or scripts | Copied workspace, command policy, timeout, output cap, cancellation; Docker adapter available | The local fixture adapter is not an OS boundary |
| Shell escape and command composition | `shell=False`, tokenized invocation, shell metacharacter denial, nested-shell denial | Allowed compilers and test tools execute repository code |
| Package installation or unknown scripts | `requires_approval`; no dependency install in deterministic CI | Approved installers may execute lifecycle hooks and contact networks |
| Network access | Docker uses `--network none`; local adapter declares network disabled and rejects download tools | Local OS controls are needed to guarantee network isolation |
| Host filesystem access | Temporary copied workspace, confined working directories, safe path resolution | A hostile native process needs a real OS/container boundary |
| Traversal and symlink escape | Reject absolute paths, `..`, `.git`, protected locations, and escaping symlinks | TOCTOU attacks require stronger filesystem isolation |
| Unexpected overwrite | Base-byte SHA-256 precondition and transactional rollback | Hashes prove base equality, not semantic correctness |
| Secret exposure | Environment allowlist, no askpass variables, output redaction, high-confidence changed-content scan, structured audit sanitization | Pattern scanners can miss novel formats and may false-positive |
| Git credentials | No credential variables are injected; `.git` paths are protected; publication methods refuse action | Operator-managed Git tooling exists outside the core |
| Destructive Git or host action | Push, force push, merge, tags, releases, deletion tools, privilege tools, and downloads denied | An incorrectly extended policy could widen the boundary |
| Approval replay | Approval binds task, plan, action, patch hash, state, and optional expiry | Approval identity and operator authentication are local concerns |

## Sandbox profiles

`LocalSandbox` is intentionally described as a trusted-fixture adapter. It copies a repository,
removes common environment secrets, confines the working directory, executes without a shell, and
leaves the source working tree untouched. It is appropriate for this repository's deterministic
fixtures and controlled local projects.

`DockerSandbox` adds network denial, a read-only container root, dropped capabilities,
`no-new-privileges`, PID, memory, and CPU limits, a bounded temporary filesystem, and a single
workspace mount. The workspace mount is writable so a patch can be tested. Operators should pin
and verify images and may need a stronger isolation service for hostile workloads.

## Command decisions

The policy returns `allow`, `deny`, or `requires_approval`. Read-only Git inspection and recognized
validation tools are allowed. Package modification, migrations, local Git mutation, arbitrary
Python, and unknown tools require approval. Git publication and history rewriting, destructive
shell deletion, nested shells, privilege elevation, remote access, and download utilities are
denied. Model output cannot override these decisions.

## File and patch decisions

Paths are relative to the workspace. Absolute paths, parent traversal, `.git`, protected secret
locations, and symlink escapes are rejected. Generated artifacts, binary content, duplicate
operations, unexpected creates, missing targets, stale hashes, empty diffs, and out-of-scope files
fail validation. Deletion is disabled unless the caller opts in and remains a sensitive action.

## Secret handling

Secret scanning reports only path, rule, and a redacted classification. Evidence and audit objects
sanitize sensitive keys and redact private keys, bearer tokens, and credential assignments before
persistence. Raw authorization headers and matched secret values must never be logged. If a real
credential is found, stop, revoke it through the provider, and remove it from reachable history.

## GitHub publication boundary

The core can read host metadata through an adapter and generate a PR draft. It cannot push, merge,
close issues, publish comments, create tags, or create releases. Those are explicit operator
workflows outside the agent. Force push remains denied.

## Reporting vulnerabilities

Follow [SECURITY.md](../SECURITY.md). Do not open a public issue containing a credential, exploit,
or private repository content.
