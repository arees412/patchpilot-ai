# Security policy

## Supported versions

PatchPilot has not published a release. Security fixes target the current `main` branch.

## Reporting

Please report a suspected vulnerability privately through GitHub's security advisory interface for
this repository. Include the affected commit, a minimal reproduction, impact, and suggested
mitigation when possible.

Do not include live credentials, private source code, or destructive payloads in a public issue.
Do not test against repositories or systems you do not own or have permission to assess.

## Scope statement

PatchPilot's controls are defense layers, not security guarantees. In particular, the local fixture
sandbox is not an OS isolation boundary, allowed test tools may execute repository code, and secret
heuristics cannot identify every credential format. See [the threat model](docs/security.md).
