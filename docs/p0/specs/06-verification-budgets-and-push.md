> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# P0-SPEC-006: M6 — verification, bounded repairs, and existing-branch push

Status: ready-for-agent (local specification).

Dependencies: P0-SPEC-005.

## Problem Statement

Root-only command detection, mandatory test-command configuration, and combined commit/push do not support legacy monorepos or prove that the reviewed code builds. Unbounded or conflated repair behavior also makes failures misleading.

## Solution

Discover or derive safe module-aware validation, install required dependencies, verify reviewed committed snapshots, enforce separate bounded repairs, and push only permitted existing commits with honest results.

## User Stories

1. As a developer, I want to derive Build commands for legacy code, so that missing configured scripts do not require fabricated verification.
2. As a developer, I want to inspect relevant modules and affected dependents, so that web/API monorepos receive appropriate checks.
3. As a developer, I want to adapt safe commands during Execute, so that Plan hints do not freeze an incorrect command.
4. As a developer, I want to install declared dependencies, so that compilation uses a working isolated environment.
5. As a developer, I want to require explicit scope for manifest changes, so that setup cannot silently change product dependencies.
6. As a developer, I want to separate unit tests from integration/deployment scripts, so that generic commands cannot trigger excluded capabilities.
7. As a developer, I want to skip only genuinely absent relevant unit-test suites, so that missing command configuration does not hide existing tests.
8. As a developer, I want to use a relevant Dockerfile build target, so that packaging can validate changed source.
9. As a developer, I want to avoid treating stale-artifact copies as compilation, so that Build evidence corresponds to the Product Change.
10. As a developer, I want to verify the exact reviewed committed snapshot, so that failed dirty edits cannot affect final evidence.
11. As a developer, I want to distinguish code failure, missing evidence, and environment blockers, so that I can choose the correct next action.
12. As a developer, I want to apply bounded independent review and verification fixes, so that retry behavior is predictable.
13. As an operator, I want to enforce invocation and whole-Run deadlines, so that a stalled model or subprocess cannot run indefinitely.
14. As an operator, I want to cancel child work and retain partial logs, so that timeout evidence remains inspectable.
15. As a developer, I want to review and separately commit final verification fixes, so that late corrections cannot bypass review.
16. As a developer, I want to retry push without creating duplicate commits, so that transport failures do not duplicate Product Changes.
17. As a developer, I want to preserve remote work when push is rejected, so that the agent cannot repair conflicts by rewriting history.
18. As a developer, I want to receive reviewed commits even after exhausted Build fixes, so that I can inspect useful work with an accurate failed result.
19. As a developer, I want to see no-change success without an empty push, so that already-satisfied work is reported honestly.

## Implementation Decisions

- Extend existing verification behavior to inspect relevant module guidance, manifests, lockfiles, existing scripts, CI, Dockerfiles, and source layout; distinguish DETECTED and PROPOSED command origins.
- Command hints are not frozen authorization. Derive or adapt commands within approved semantic scope; inspect referenced scripts/targets and reject real DB, migrations against real environments, integration/E2E, deployment, or cross-Project effects.
- Run verification without coordinator credentials. Prefer structured argv and explicit working directory; shell interpretation must be deliberate and inspect the full command/script.
- Dependency installation follows declared sources/lockfiles, generally once per relevant Run/module; repeat only for an actual need. Do not change manifests without explicit Task coverage.
- Use dedicated builder capability for relevant Dockerfile targets, without host daemon access in coding tools or registry pushes. Confirm selected stages validate changed code; unsupported builder setup blocks rather than granting privilege automatically.
- Record command, working directory, toolchain, origin, timestamps, exit code, logs/excerpt, status, and failure category for every check.
- Use PASS for successful execution, FAIL for code failure, SKIPPED for no relevant existing unit-test suite, BLOCKED for environment/timeout, UNVERIFIED for missing credible Build/evidence. Derive commands for existing tests before considering an unresolved check.
- Build is mandatory. Required unit tests must pass; absent tests may skip. Known unrelated old failures do not authorize scope expansion but failing mandatory checks still prevent SUCCESS.
- Verify final reviewed commits in a separate snapshot. Final in-scope fixes re-enter the responsible Task, retain used counters, pass review, and create additional identifiable commits.
- Allow an initial attempt plus three additional fixes independently for Task review, Task verification, and repository verification. MAX_FIX_ATTEMPTS supplies each code-fix limit; push has its own three additional retries.
- Default invocation limits are 60 iterations/1800 seconds, verification commands 1200 seconds, whole Run 7200 seconds including finalization. Supervise wall-clock cancellation/process groups; an LLM request timeout alone is insufficient.
- Before normal push, inspect every introduced commit and the full Run change range relative to its initial source SHA for exact review and prohibited artifacts, not merely the final net diff.
- Retry transient push failures using the same commits. Stop on auth/policy/history conflict; never pull, rebase, merge, reset, or force to fix rejection.
- After exhausted verification, push permitted reviewed commits with FAILED result; never add unreviewed dirty work. No-change verified success uses NOT_NEEDED instead of an empty commit.
- SUCCESS requires required Tasks/checks and necessary push; PARTIAL_SUCCESS requires some completed Tasks plus passing mandatory checks and successful permitted push; missing/failed/blocked mandatory checks or failed push yield FAILED.

## Testing Decisions

- Use the workflow seam with temporary module fixtures and real local Git remotes; assert evidence, result classifications, commit ranges, and remote effects.
- Cover legacy derivation, mixed web/API layouts, existing tests without a configured command, absent suites, disallowed combined scripts, missing toolchains, baseline failures, and dirty failed-edit isolation.
- Prove exact initial-plus-three boundaries with independent counters and retained counts after late fixes; simulate clock/deadline expiry and test actual child cancellation in runtime probes.
- Use real builder probes for selected-stage compile coverage and unsupported setup. Test push response failures against unchanged commit IDs and reject non-fast-forward without rewrite.
- Prior art is existing command detection/check execution and Git publication helpers; no suite currently exists. Test outcomes rather than reproducing detector implementation or exact model prompts.

## Out of Scope

New target test frameworks, target integration/E2E tests, deployment, real DB connections, image registry publication, privileged fallback, cross-Run cache restoration, and automated remote conflict resolution.

## Further Notes

Optional Execute-only baseline checks use the same Run budget. Planning still cannot run Build/Test/setup. Builder and SDK compatibility are implementation probes, not established capabilities.

Source and ADR traceability: see the [spec index](README.md). This specification records accepted requirements, not completed implementation.

