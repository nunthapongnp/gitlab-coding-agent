> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# P0-SPEC-000: Phase 0 prototype contract

Status: ready-for-agent (local specification).

Dependencies: None.

## Problem Statement

Developers need to test whether a GitLab Issue can become a useful Execution Plan and a reviewed Product Change without handing branch ownership or merge decisions to an agent. The current prototype edits directly and creates branches/MRs, lacks approval and planning, and can report success without complete verification.

## Solution

Provide a manual happy path from New Issue through Plan, label approval, a user-created source branch/MR, implementation, and human review/merge. GitLab retains the work agreement; each Execute Run independently explores a new clone in Docker, implements Tasks sequentially, and reports evidence rather than inferred success.

## User Stories

1. As a developer, I want to request planning through an Issue label, so that I can keep the existing CLI invocation.
2. As a developer, I want to receive a complete Execution Plan, so that I can assess the intended Product Change before implementation.
3. As a developer, I want to read Markdown and machine-readable plan data together, so that the human and agent use the same agreement.
4. As a developer, I want to see unresolved requirement questions, so that ambiguous work does not silently become executable.
5. As a developer, I want to approve through label values alone, so that I can test the prototype without identity configuration.
6. As a developer, I want to have conflicting agent labels rejected, so that the system does not guess which mode I intended.
7. As a developer, I want to create my own source branch and MR, so that I retain repository workflow ownership.
8. As a developer, I want to have exactly one open same-Project MR selected, so that changes reach the intended existing branch.
9. As a developer, I want to keep the MR description and target unchanged, so that the agent does not rewrite my review context.
10. As a developer, I want to start each Run with fresh context, a clone, and a volume, so that previous execution state does not influence the new attempt.
11. As a developer, I want to have the repository explored again each Run, so that implementation follows current source content.
12. As an operator, I want to retain GitLab plans and reports across fresh Runs, so that work agreements survive disposable execution.
13. As an operator, I want to use shared locks on one Docker host, so that duplicate attempts do not edit the same Issue or branch concurrently.
14. As a developer, I want to have Tasks processed sequentially by dependency, so that the prototype is understandable before parallel execution is introduced.
15. As a developer, I want to receive independent review before every Task commit, so that scope and acceptance are checked before publication.
16. As a developer, I want to allow related new source, asset, and test files, so that features are not constrained by an advance file list.
17. As a developer, I want to exclude documentation changes and Agent Artifacts from commits, so that origin receives only permitted Product Changes.
18. As a developer, I want to require explicit scope for configuration and database-file changes, so that broad issue text does not authorize unrelated modifications.
19. As a developer, I want to let the agent derive legacy build commands, so that missing configured commands do not prevent exploration of a valid Build method.
20. As a developer, I want to permit dependency installation, so that the changed product can be compiled and checked in its own environment.
21. As a developer, I want to use a relevant Dockerfile Build, so that repository-native packaging can validate the changed source.
22. As a developer, I want to require Build evidence before success, so that a successful push does not conceal broken compilation.
23. As a developer, I want to run existing unit tests and truthfully skip absent suites, so that verification reflects the repository without introducing test infrastructure.
24. As a developer, I want to retain bounded independent repair counters, so that one failure category does not silently consume or reset another category's budget.
25. As a developer, I want to see independent Tasks continue after a failed Task, so that useful reviewed work can still be delivered.
26. As a developer, I want to verify only the reviewed commit set at the end, so that failed uncommitted edits cannot distort publication evidence.
27. As a developer, I want to preserve original commits and use normal push, so that the agent cannot overwrite my history.
28. As a developer, I want to receive one final append-only Execution Report, so that I can assess the whole attempt in one place.
29. As an operator, I want to retain available diagnostics after interruption, so that failures remain inspectable without automatic recovery.
30. As a developer, I want to regenerate a complete plan and approve it again, so that requirement changes create an independently usable agreement.
31. As a developer, I want to retain final approval and merge control, so that the prototype assists rather than completes the human governance step.
32. As an operator, I want to defer RAG, parallelism, and restart recovery, so that I can evaluate the core workflow first.

## Implementation Decisions

- Use the glossary meanings of Execution Plan, Approved Plan, Task, Execute Run, Product Change, Agent Artifact, and Execution Report.
- Approval is label-only. Do not add identity, role, approval-time, cryptographic-content, or historical authorization gates.
- On the Issue, the user removes agent::needs-review before adding agent::approved. Conflicting recognized states on either Issue or MR reject the invocation; unrelated labels are preserved.
- Use separate Plan and Execute LangGraph workflows and an existing label-driven CLI entrypoint. The agent does not create branches/MRs, modify MR metadata, approve, or merge.
- Keep three architectural decisions: fresh workspaces plus GitLab records; Docker role isolation; semantic Task scope plus reviewed publication.
- One active Coding Agent works sequentially. Reviewer is a separate read-only context. Within-run repairs retain current working code; different Runs never restore prior contexts or workspaces.
- Plan regeneration reads all comments and publishes a full new version, invalidating approval. Task identity is version-local.
- Build is mandatory; unit tests use existing relevant suites. Dynamic safe command selection is allowed without reapproval if Task scope is unchanged.
- Outcome is SUCCESS only when required Tasks/checks and necessary push succeed; PARTIAL_SUCCESS requires some completed work, mandatory checks passing, and successful delivery of permitted commits; otherwise apply FAILED according to verification/push outcomes.
- Read earlier report numbering only for identity allocation, never as a Task-resume ledger. Use a unique internal Run UID alongside the human Run number.
- Deliver Phase A through M1-M4 and Phase B through M5-M7. Downstream specs detail obligations without expanding prototype scope.

## Testing Decisions

- Primary seam: invoke the application workflow with realistic arguments and injectable GitLab/worker transports; assert outputs, external state transitions, commits, and reports rather than graph-node order.
- Exercise the complete happy path with fake external transports and a temporary real Git repository; test each package's negative cases at the same boundary where practical.
- Use real Docker probes for filesystem access modes, credential isolation, locks, cancellation, and optional builder operation. Mock tests cannot prove these properties.
- Good tests assert user-visible outcomes and forbidden side effects; avoid tests duplicating helper logic or asserting prompts verbatim.
- No automated suite currently exists. Existing CLI invocation, graph entrypoint, GitLab transport, and Git operations supply starting seams; pytest is already declared.
- A separate opt-in walkthrough uses a disposable GitLab Project and configured model. It is not a target-repository integration/E2E feature.

## Out of Scope

Parallel coders; distributed execution; branch-staleness analysis; human-work adoption and cross-Run reconciliation; resumable checkpoints; automatic recovery/cleanup; incremental feedback reconciliation; cross-version Task matching; RAG; multi-repository work; deployment; real DB access; target-project integration/E2E tests; new target test infrastructure; user-facing web/API; automatic branch/MR creation or merge.

## Further Notes

This is the umbrella acceptance contract. Package specs are local ready-for-agent documents, not published tracker issues. Label-only approval and best-effort reporting are deliberate prototype limits.

Source and ADR traceability: see the [spec index](README.md). This specification records accepted requirements, not completed implementation.

