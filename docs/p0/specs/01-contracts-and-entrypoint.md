# P0-SPEC-001: M1 — contracts and label-driven entrypoint

Status: ready-for-agent (local specification).

Dependencies: None.

## Problem Statement

The existing entrypoint always runs implementation, mixes publication with verification success, and lacks a stable contract for Plan, Execute, invalid state, and configuration errors.

## Solution

Keep the familiar invocation while selecting the appropriate workflow from validated labels. Return explicit typed results and actionable diagnostics so developers can distinguish a plan, a rejected request, a failed Run, and a delivery failure.

## User Stories

1. As a developer, I want to keep required Project and Issue arguments, so that existing basic invocation remains usable.
2. As a developer, I want to select Plan through agent::plan, so that I do not need a new mode flag.
3. As a developer, I want to select Execute through Issue approval and MR implement labels, so that the same invocation supports both modes.
4. As a developer, I want to receive an error for conflicting labels, so that the system cannot guess my intended action.
5. As a developer, I want to preserve unrelated labels, so that agent transitions do not erase Project organization.
6. As a developer, I want to override the Plan inspection branch, so that I can inspect an explicit baseline.
7. As a developer, I want to have branch overrides rejected during Execute, so that the MR remains the source-branch authority.
8. As an operator, I want to receive useful errors for invalid environment values, so that configuration mistakes do not become tracebacks or silently changed limits.
9. As a developer, I want to retain optional setup/build/test hints, so that explicit suggestions remain usable without becoming mandatory legacy configuration.
10. As a developer, I want to have obsolete MR-creation controls rejected, so that the entrypoint cannot imply ownership it no longer has.
11. As a developer, I want to see structured plan and Run outcomes, so that automation can distinguish work from publication.
12. As a developer, I want to never see success for missing Build evidence, so that incomplete validation cannot be mistaken for delivery.
13. As an operator, I want to configure limits through environment values, so that runtime policy stays outside extra CLI flags.
14. As a developer, I want to have malformed plan and Task data rejected, so that unchecked model output cannot drive execution.

## Implementation Decisions

- Separate invocation/settings validation, label routing, and domain result contracts. Load settings inside handled error boundaries.
- Retain Project/Issue, GitLab URL, output settings, optional command hints, and repair-limit override. Add only Plan branch override; remove target-branch and MR title/description/draft creation controls. Add no mode, execute, publish, or maximum-runtime flags.
- Recognized Issue states are agent::plan, agent::in-progress, agent::needs-review, and agent::approved; recognized MR states are agent::implement, agent::in-progress, and agent::needs-review. At most one recognized state is valid on each object.
- Plan requires the Plan trigger. Execute requires the approved Issue and a related open MR marked implement; MR uniqueness and validity are preflight obligations. Missing/conflicting/in-progress states reject rather than resume.
- Define validated IssueContext, ExecutionPlan, TaskResult, ReviewResult, CheckResult, RunResult, and DeliveryResult contracts. Plans require matching identifiers, supported schema, positive versions, unique Task IDs, nonempty scope/acceptance, known dependencies, and an acyclic graph.
- Task results retain plan/Task identity, state, review/change evidence, separate counters, commit SHAs/messages, verification references, and failure/block reasons. Review results retain acceptance/scope findings, requested revisions, and the exact reviewed diff fingerprint.
- Check results retain module/type, command/argv, working directory, origin, status, exit code, timing, log references/excerpts, and failure category. Run results retain unique/display identity, plan/Issue/MR identity, source/initial SHA, timing, Tasks/checks, push outcome, overall result, and next action; delivery results separately retain local evidence, note identity, report/label status, and errors.
- Runtime Task states include PENDING, IN_PROGRESS, REVIEW, REVISION, COMPLETED, FAILED, BLOCKED. Terminal Task reports use COMPLETED, FAILED, BLOCKED, PENDING; partial unfinished work is evidence rather than a second terminal enum.
- Default limits are 60 OpenHands iterations, 1800 seconds per invocation, 1200 seconds per verification command, 7200 seconds per Execute Run, and three additional fix attempts.
- Exit conventions: zero for success plus required finalization, two for invalid invocation/state/preflight, three for completed partial/failed execution, four for publication/final-label failure, one for other infrastructure errors.
- Remove the temporary path that treats absent verification configuration as successful publication. A push outcome and a verification outcome remain distinct.

## Testing Decisions

- Test the CLI/workflow boundary with supplied context, capturing exit codes, diagnostic output, and observed transport calls.
- Cover every trigger/state combination, Plan-only override, obsolete flags, malformed settings, absent Build, invalid schemas, and bounded limits.
- Assert no product edits/publication on rejected invocation and no user/role lookup for approval.
- Prior art is the existing argument parser, graph invocation, and settings loading; there is no existing automated suite. Reuse these observable entrypoints instead of inventing per-validator public interfaces.

## Out of Scope

Runtime containers, GitLab transport implementation, full planning/coding, new identity authorization, source-drift gates, and extra mode/runtime CLI flags.

## Further Notes

This package establishes shared contracts before dependent packages. Interim behavior must stop unsafe publication rather than retain misleading success.

Source and ADR traceability: see the [spec index](README.md). This specification records accepted requirements, not completed implementation.

