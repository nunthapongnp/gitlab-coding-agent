# Phase 0 ticket breakdown for review

Status: draft; approval of granularity and blocking edges is pending.

Requested publication destination: the tickets subdirectory of the current Phase 0 specs. The one-file-per-ticket drafts are kept here until breakdown review is complete; approved copies will use ready-for-agent status at the requested destination.

## Proposed tickets

| Ticket | Blocked by | Observable delivery |
|---|---|---|
| [01: Make workflow invocation testable and stop unsafe publication](issues/01-make-workflow-invocation-testable-and-stop-unsafe-publication.md) | None | Invoke the existing application with controlled external dependencies and receive truthful failure diagnostics instead of automatic branch/MR creation or success without Build evidence. |
| [02: Route Issue requests by validated labels](issues/02-route-issue-requests-by-validated-labels.md) | 01 | Use the same Project/Issue invocation to select Plan or Execute preparation, or receive an actionable rejection before product work. |
| [03: Inspect a fresh repository in a read-only Docker operation](issues/03-inspect-a-fresh-repository-in-a-read-only-docker-operation.md) | 02 | Launch an Issue operation in Linux containers, inspect a fresh clone through a read-only Planner, and retain its inspection evidence outside the product checkout. |
| [04: Publish the first complete Execution Plan](issues/04-publish-the-first-complete-execution-plan.md) | 03 | Request Plan for a clear Issue and receive one complete Markdown/JSON plan comment and an Issue ready for human review. |
| [05: Regenerate complete plans and block unresolved requirements](issues/05-regenerate-complete-plans-and-block-unresolved-requirements.md) | 04 | Clarify or change an Issue and obtain a new independently usable plan version whose approval is reset; unresolved questions remain non-executable. |
| [06: Validate a user-created MR and fresh Execute checkout](issues/06-validate-a-user-created-mr-and-fresh-execute-checkout.md) | 05 | Prepare execution only for the latest executable Approved Plan and exactly one valid existing MR, with local diagnostics for rejected requests. |
| [07: Start and finalize an Execute Run with one report](issues/07-start-and-finalize-an-execute-run-with-one-report.md) | 06 | After preflight, start a uniquely identified Run and retain a truthful final local/MR report even when execution fails before producing code. |
| [08: Implement and independently review one Task before local commit](issues/08-implement-and-independently-review-one-task-before-local-commit.md) | 07 | Implement one approved Task in a fresh Coding context, obtain an independent read-only review, and preserve only that accepted Product Change as a local Task commit. |
| [09: Verify one Task with native Build and existing unit tests](issues/09-verify-one-task-with-native-build-and-existing-unit-tests.md) | 08 | Run a narrow repository-native verification path in the Run environment, install declared dependencies when needed, and attach actual Build/Test evidence to the Task and final report. |
| [10: Execute dependent Tasks and isolate failed edits](issues/10-execute-dependent-tasks-and-isolate-failed-edits.md) | 09 | Process multiple Tasks sequentially, block failed dependencies, and continue independent Tasks without including failed edits in their work. |
| [11: Derive legacy and multi-module validation commands](issues/11-derive-legacy-and-multi-module-validation-commands.md) | 09 | Verify a legacy or web/API repository even without configured command strings, checking affected modules and recording how each command was chosen. |
| [12: Validate changed source through a dedicated Dockerfile builder](issues/12-validate-changed-source-through-a-dedicated-dockerfile-builder.md) | 09 | Use the repository's relevant Dockerfile target as Build evidence when it actually compiles or validates the changed source, without exposing host daemon control. |
| [13: Apply independent bounded Task review and verification fixes](issues/13-apply-independent-bounded-task-review-and-verification-fixes.md) | 09 | Revise a Task after review or verification failure with separate visible budgets, retaining review-before-commit throughout. |
| [14: Cancel over-budget work and preserve finalization evidence](issues/14-cancel-over-budget-work-and-preserve-finalization-evidence.md) | 09 | Stop stalled model or verification work at the configured limits while preserving partial logs, finalization opportunity, and existing commits. |
| [15: Verify the exact reviewed commit set and review final fixes](issues/15-verify-the-exact-reviewed-commit-set-and-review-final-fixes.md) | 10, 13, 14 | Run final repository validation against precisely the reviewed commits intended for publication, applying bounded in-scope fixes through review and new Task commits. |
| [16: Push only reviewed commits to the existing MR branch](issues/16-push-only-reviewed-commits-to-the-existing-mr-branch.md) | 15 | Publish the reviewed Run commit set through a normal push to the user's existing source branch and report delivery and verification independently. |
| [17: Deduplicate uncertain Plan/report delivery and independent label outcomes](issues/17-deduplicate-uncertain-plan-report-delivery-and-independent-label-outcomes.md) | 07 | Handle lost API responses without duplicate plan versions or final reports and retain clear local delivery evidence when GitLab cannot confirm the result. |
| [18: Demonstrate the complete prototype and document operator use](issues/18-demonstrate-the-complete-prototype-and-document-operator-use.md) | 11, 12, 16, 17 | Run the full disposable-Project happy path from Issue to reviewed MR delivery, with operator instructions and evidence that the accepted prototype limits hold. |

## Working the frontier

Ticket 01 can start immediately. Work only tickets whose listed blockers have completed. After 07, delivery-resilience ticket 17 can proceed independently of coding/verification. After 09, tickets 10-14 can proceed independently until the reviewed-snapshot convergence in 15. Ticket 18 brings native, legacy, Dockerfile, push, and delivery paths together.

These are development dependencies, not permission to run multiple Coding Agents in an Execute Run. Prototype execution remains sequential.

Blocking edges list direct prerequisites only. For example, ticket 17 needs complete Plan and report behavior, but 07 already depends transitively on 05, so 05 is not repeated as a redundant blocker.

## Source coverage

| Source | Draft tickets |
|---|---|
| P0-SPEC-000 | 01, 18 |
| P0-SPEC-001 | 01, 02, 18 |
| P0-SPEC-002 | 02, 04, 05, 06, 07, 17, 18 |
| P0-SPEC-003 | 03, 06, 08, 12, 14, 18 |
| P0-SPEC-004 | 03, 04, 05, 11, 17, 18 |
| P0-SPEC-005 | 06, 08, 09, 10, 13, 15, 16, 18 |
| P0-SPEC-006 | 09, 10, 11, 12, 13, 14, 15, 16, 18 |
| P0-SPEC-007 | 07, 08, 14, 15, 16, 17, 18 |

- [Source specs](../README.md)
- [Accepted implementation plan](../../CODING_AGENT_PHASE0_IMPLEMENTATION_PLAN.md)
- [ADR 0001: fresh workspaces and GitLab records](../../adr/0001-fresh-workspaces-and-gitlab-records.md)
- [ADR 0002: Docker role isolation](../../adr/0002-docker-role-isolation.md)
- [ADR 0003: semantic scope and reviewed publication](../../adr/0003-semantic-task-scope-and-publication.md)
- [Glossary](../../../../GLOSSARY.md)

All tickets inherit the umbrella scope: label-only approval, fresh Runs, no preapproved file allowlists, no parallel coders, no source-drift/historical Task reconciliation, no resume/RAG/deployment/real DB access, and no target integration/E2E tests.

## Ticket conventions

Each ticket has a user-observable delivery, explicit blockers, acceptance checkboxes, source-spec identifiers, and a verification seam. Source identifiers are traceability only; application implementation descriptions do not prescribe concrete file paths or snippets.

01 is an explicit prefactor before feature slices; it is deliberately narrow and does not require a wide expand-contract migration. Subsequent tickets deliver workflow behaviors rather than horizontal schema/client/runtime projects.

Default tests use the existing application/workflow seam with controlled transports and real temporary Git repositories where necessary. Runtime probes establish actual mounts, credentials, locks, cancellation, and builder properties. An opt-in disposable-Project walkthrough provides real final evidence.

Interim tickets must keep incomplete functionality truthful: local commits are not published commits; missing mandatory Build cannot mean success; unsupported downstream steps reject clearly rather than invoking the legacy automatic branch/MR flow.

## Review requested

1. Does 18-ticket granularity fit one fresh context per ticket, or should any slices be smaller/larger?
2. Are the direct blockers genuine prerequisites, or should an edge be removed/added?
3. Which tickets, if any, should be merged or split?

After approval, publish one ready-for-agent Markdown file per ticket at the requested local destination. Do not create external issues or modify a parent issue.

