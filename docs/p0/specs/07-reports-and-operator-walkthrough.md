> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# P0-SPEC-007: M7 — final reports and operator walkthrough

Status: ready-for-agent (local specification).

Dependencies: P0-SPEC-004, P0-SPEC-005, P0-SPEC-006.

## Problem Statement

Console output and a late local summary cannot reliably explain failed or partially delivered work. Developers need one truthful MR report and a repeatable way to test the prototype without surrendering merge control.

## Solution

Finalize each started Execute Run with local evidence, one deduplicated append-only MR report when delivery is possible, a needs-review transition, and an operator walkthrough for the manual happy path.

## User Stories

1. As a developer, I want to receive one final MR report per started Run, so that the outcome is readable without progress-comment noise.
2. As a developer, I want to see the Issue, MR, Plan, and Run identities, so that I can trace evidence to the correct attempt.
3. As a developer, I want to see start/end times and duration, so that I can understand execution cost and interruption.
4. As a developer, I want to see Task totals and terminal outcomes, so that completed, failed, and blocked work are distinguishable.
5. As a developer, I want to see every new commit SHA and message, so that I can review the exact delivered changes.
6. As a developer, I want to see actual verification commands and working directories, so that reported results are reproducible.
7. As a developer, I want to see skipped or blocked checks explicitly, so that a success statement does not imply unperformed tests.
8. As a developer, I want to see separate repair and push retry counts, so that failure handling is auditable.
9. As a developer, I want to see push outcome and confirmed remote state, so that local commits are not mistaken for published commits.
10. As a developer, I want to receive a concrete next action, so that the report tells me what needs human attention.
11. As an operator, I want to save structured local results before API delivery, so that a GitLab outage does not erase available evidence.
12. As an operator, I want to deduplicate a report after a lost response, so that the same Run does not create repeated final comments.
13. As a developer, I want to attempt needs-review even if report delivery fails, so that independent finalization work is not skipped.
14. As an operator, I want to retain available artifacts after termination, so that a crash does not trigger destructive cleanup.
15. As a developer, I want to keep preflight rejection outside Run reporting, so that a request that never started is not presented as executed work.
16. As a developer, I want to keep my MR description unchanged, so that agent results do not overwrite my review narrative.
17. As an operator, I want to follow a disposable-Project walkthrough, so that I can evaluate the full prototype safely and repeatably.
18. As a developer, I want to make the final human review and merge decision, so that the agent stops at delivery for review.

## Implementation Decisions

- Use existing report/plan layouts as baseline, explicitly adapting timestamps, UID, commit messages, working directories, retry wording, terminal statuses, and partial/no-change outcomes.
- Write RunResult locally before network publication. Keep DeliveryResult distinct from product/verification outcome and include report and label failures in CLI exit behavior.
- One final append-only MR note includes identity, source/initial SHA, timings, Task evidence, all commits, Build/Test evidence and retry counts, push result, and next action. No per-Task/stage comments or MR description updates.
- Finalization cancels active work, saves available evidence, attempts report delivery, independently attempts MR needs-review, and releases locks even when a previous finalization step fails.
- Deduplicate using Run UID. After uncertain POST, query existing notes; if delivery remains ambiguous and cannot be checked, retain UNKNOWN locally rather than repeatedly posting.
- Report SUCCESS, PARTIAL_SUCCESS, or FAILED according to M6. Push outcomes include SUCCESS, FAILED, NOT_ATTEMPTED, NOT_NEEDED; never imply skipped tests or missing Build passed.
- Allocate display Run numbering from published Issue execution history under the Issue lock, alongside a unique internal UID. Do not promise strict crash-proof sequential allocation or resume earlier Tasks.
- Preflight rejection produces CLI/local diagnostics only. Started Runs receive best-effort final reporting; immediate kill/host restart/unavailable GitLab may prevent a final comment or label update.
- Preserve logs/commits already written; do not automatically resume, delete volumes, or reset workspaces after delivery/process failure.
- Correct stale template terminology, old approval namespace, parallel strategy, cross-version Task matching promises, and misleading three-total-attempt wording to the accepted contract.
- Walkthrough: prepare runtime/disposable Project; create Plan Issue; inspect complete plan; manually replace needs-review with approved; create/link branch/MR and mark implement; invoke again; inspect fresh execution/review/verification/report; human approves/merges.
- Document configured worker/model/toolchain/builder setup, basic invocation, statuses, limitations, retained evidence, and explicit operator cleanup. Future RAG remains an exploration aid, not authority or run state.

## Testing Decisions

- Invoke the workflow with successful, partial, failed, preflight-rejected, and delivery-failed contexts; assert report content, note count, label sets, local evidence, and exit codes.
- Simulate POST success with lost response, unavailable follow-up query, independent label failure, and interruption; assert no duplicate blind repost and no automatic recovery/cleanup.
- Use source-of-truth values in report assertions rather than exact Markdown snapshots that mirror renderer internals.
- Prior art is existing CLI/local result output and report templates, not an existing report test suite. Reuse the application seam and fake transports; run a separate opt-in disposable-Project walkthrough for real runtime evidence.
- The no-target-integration/E2E restriction does not forbid testing the coding agent's own runtime/transport behavior. Ordinary tests must not contact real GitLab or model services.

## Out of Scope

Guaranteed exactly-once delivery after crash, durable number reservation, progress-comment streams, MR description rewriting, automatic approval/merge, and recovery orchestration.

## Further Notes

Phase A is available after contracts/integration/runtime/planning; Phase B completes after execution/verification/finalization. This package supplies final acceptance evidence, not an assertion that the existing implementation already works.

Source and ADR traceability: see the [spec index](README.md). This specification records accepted requirements, not completed implementation.

