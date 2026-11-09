# Phase 0 specification package

This package translates the accepted implementation plan and all three ADRs into English specifications. It is a local delivery to the requested directory; no tracker issue or external triage label has been created. Each specification is marked ready-for-agent locally.

The specifications describe required behavior and testing decisions, not implemented or verified product capabilities.

## Specifications and dependencies

| Spec | Scope | Prerequisites |
|---|---|---|
| [P0-SPEC-000](00-phase0-prototype.md) | Phase 0 prototype contract | None |
| [P0-SPEC-001](01-contracts-and-entrypoint.md) | M1 — contracts and label-driven entrypoint | None |
| [P0-SPEC-002](02-gitlab-records-and-labels.md) | M2 — GitLab records and state transitions | P0-SPEC-001 |
| [P0-SPEC-003](03-fresh-docker-runtime.md) | M3 — fresh Docker runtime and role isolation | P0-SPEC-001 |
| [P0-SPEC-004](04-complete-plan-mode.md) | M4 — read-only complete Plan Mode | P0-SPEC-002, P0-SPEC-003 |
| [P0-SPEC-005](05-reviewed-task-execution.md) | M5 — existing-MR preflight and reviewed sequential Tasks | P0-SPEC-002, P0-SPEC-003, P0-SPEC-004 |
| [P0-SPEC-006](06-verification-budgets-and-push.md) | M6 — verification, bounded repairs, and existing-branch push | P0-SPEC-005 |
| [P0-SPEC-007](07-reports-and-operator-walkthrough.md) | M7 — final reports and operator walkthrough | P0-SPEC-004, P0-SPEC-005, P0-SPEC-006 |

Read the umbrella contract first. Implement M1, then M2/M3 as their dependencies permit, then M4, M5, M6, and M7. Phase A ends at M4; Phase B ends at M7. Dependency ordering does not authorize parallel Coding Agents within an Execute Run.

## Source authority

- [Accepted implementation plan](../CODING_AGENT_PHASE0_IMPLEMENTATION_PLAN.md)
- [Domain glossary](../../../GLOSSARY.md)
- [ADR 0001: fresh workspaces and GitLab records](../adr/0001-fresh-workspaces-and-gitlab-records.md)
- [ADR 0002: Docker role isolation](../adr/0002-docker-role-isolation.md)
- [ADR 0003: semantic Task scope and publication](../adr/0003-semantic-task-scope-and-publication.md)

The accepted plan incorporates the latest interview decisions and supersedes conflicting original overview/template details. These specs preserve that scope; they do not revive deferred requirements.

## Complete source coverage

| Source area | Specifications |
|---|---|
| Intended outcome, agreed contract, exclusions, definition of done | P0-SPEC-000 and all package Out of Scope sections |
| Current behavior gaps and unsafe incomplete-verification publication | P0-SPEC-001, P0-SPEC-002, P0-SPEC-005, P0-SPEC-006 |
| CLI compatibility and label routing | P0-SPEC-001 |
| GitLab pagination, comments, relationships, transitions | P0-SPEC-002 |
| Branch selection | P0-SPEC-004 for Plan; P0-SPEC-005 for Execute |
| Docker roles, fresh volumes, worker transport, model/toolchain setup | P0-SPEC-003 |
| Locks, diagnostic retention, no restart recovery | P0-SPEC-003 and P0-SPEC-007 |
| Plan schema, Markdown/JSON agreement, complete regeneration | P0-SPEC-001 and P0-SPEC-004 |
| Run/Task/check/review/delivery contracts and identities | P0-SPEC-001, P0-SPEC-005, P0-SPEC-006, P0-SPEC-007 |
| Plan graph and clarification-required behavior | P0-SPEC-004 |
| Execute preflight, sequential Task loop, independent review | P0-SPEC-005 |
| Failed Task isolation and exact reviewed publication | P0-SPEC-005 and P0-SPEC-006 |
| Dynamic legacy commands, dependencies, optional Dockerfile Build | P0-SPEC-006 |
| Status classification, overall outcomes, budgets, retries, cancellation | P0-SPEC-001 and P0-SPEC-006 |
| Append-only final reports, delivery ambiguity, finalization | P0-SPEC-007 |
| Template adaptations and operator documentation | P0-SPEC-004 and P0-SPEC-007 |
| Acceptance matrix, runtime probes, manual walkthrough | All Testing Decisions sections and P0-SPEC-007 |
| ADR 0001: fresh context/workspace, GitLab work agreement, RAG deferred | P0-SPEC-000, P0-SPEC-002, P0-SPEC-003, P0-SPEC-004, P0-SPEC-007 |
| ADR 0002: coordinator/worker permissions and Docker lifecycle | P0-SPEC-003, P0-SPEC-005, P0-SPEC-006 |
| ADR 0003: semantic scope, explicit special-file scope, reviewed diff | P0-SPEC-004, P0-SPEC-005, P0-SPEC-006 |

## Shared testing seam

Prefer the existing application invocation/workflow seam as the primary behavioral test surface. Supply realistic arguments and controlled GitLab, role-worker, verification, and clock responses; assert outputs, persisted external effects, local evidence, commit history, and prohibited actions.

Reuse existing GitLab transport and Git-operation seams for controlled external interactions. Use temporary real repositories/remotes where actual staging, history preservation, or push behavior is the property under test. Do not create one public interface per internal helper merely for mocking.

Additional real runtime probes are required for read-only mounts, credential isolation, shared locks, remote worker connectivity, process-group cancellation, and optional builder operation. These are properties that fake transports cannot establish.

There is no existing automated suite to cite as prior art. The code already exposes CLI invocation, graph entry, GitLab transport, command execution, and Git helper behavior, and declares pytest. Tests should exercise those behaviors without asserting private node order, exact prompt text, or renderer internals.

Ordinary tests must not call real GitLab or model services. The opt-in disposable-Project walkthrough validates the real manual happy path. Testing the coding agent's own runtime does not introduce target-repository integration/E2E features.

## Scope reminders

- Approval uses label values only, with no user/role/history checks.
- Every Run starts fresh; retained artifacts are diagnostics, not resumable state.
- One Coding Agent and independent review; parallel execution is deferred.
- No approved file allowlist; stage the exact execution-produced reviewed change inventory.
- Agent-derived legacy commands, dependency installation, and meaningful Dockerfile builds are allowed.
- Unit-test absence may be skipped; missing/blocked mandatory Build cannot produce success.
- Reports and labels are best effort during process/API failure, not crash-proof exactly-once delivery.
- RAG, branch-staleness analysis, human-work adoption, and previous-Run reconciliation remain deferred.

