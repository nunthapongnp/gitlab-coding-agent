# P0-SPEC-005: M5 — existing-MR preflight and reviewed sequential Tasks

Status: ready-for-agent (local specification).

Dependencies: P0-SPEC-002, P0-SPEC-003, P0-SPEC-004.

## Problem Statement

The current agent changes code before plan approval, creates its own branch/MR, and stages broadly without independent review or dependency-aware Task outcomes.

## Solution

Reject invalid preparation before a Run starts, then implement approved Tasks sequentially on the user's existing MR branch with review bound to each exact change before commit.

## User Stories

1. As a developer, I want to reject missing approval before editing, so that only an Approved Plan can drive work.
2. As a developer, I want to require the latest plan to be executable, so that clarification-required work cannot run.
3. As a developer, I want to validate one same-Project open MR, so that publication cannot target a guessed branch.
4. As a developer, I want to check out my existing source branch, so that the agent preserves my branch ownership.
5. As a developer, I want to stop on dirty initial status, so that uncommitted changes cannot be adopted or stashed.
6. As an operator, I want to revalidate state after acquiring locks, so that the selected context is current before Run start.
7. As a developer, I want to execute Tasks in dependency order, so that prerequisites complete before dependent work.
8. As a developer, I want to review each Task in a separate context, so that implementation is independently checked.
9. As a developer, I want to allow related new files without allowed_paths, so that valid feature work is not rejected by advance enumeration.
10. As a developer, I want to require explicit config/database-file scope, so that special changes remain intentional.
11. As a developer, I want to bind review to the exact commit diff, so that later modifications cannot borrow an earlier approval.
12. As a developer, I want to commit a completed Task immediately, so that reviewed progress is preserved independently.
13. As a developer, I want to block descendants of failed Tasks, so that downstream work does not assume missing prerequisites.
14. As a developer, I want to continue independent Tasks without failed-edit contamination, so that useful work can complete safely.
15. As a developer, I want to retain failed changes for inspection, so that failure evidence is not destroyed to obtain a clean checkout.
16. As a developer, I want to keep all original commits unchanged, so that agent execution cannot rewrite my history.
17. As a developer, I want to request a new Plan for scope expansion, so that implementation cannot invent additional requirements.
18. As a developer, I want to avoid empty commits for already-satisfied work, so that publication remains meaningful.

## Implementation Decisions

- Preflight loads context/latest plan, checks label-only approval and executable state, validates one same-Project open implement MR/source branch, acquires locks, rechecks state, creates the fresh source checkout, and inspects cleanliness/origin/publication access.
- Preflight failure produces CLI/local diagnostics, not an Execute Run or MR report. Run start follows successful preflight and begins the overall deadline and MR in-progress transition.
- Use one Coding Agent at a time with fresh context, an independent read-only Reviewer, and the Task states shared by M1. Never build a parallel scheduler for the prototype.
- Reviewer evaluates acceptance and semantic relevance of all changes, including new, binary, renamed, deleted, staged, unstaged, and untracked content.
- Allow relevant code/assets/tests; require explicit configuration/dependency/schema scope. Reject documentation changes, memory, logs, summaries, other Agent Artifacts, prohibited capabilities, and unrelated edits.
- Before commit, require review of the exact diff fingerprint, construct a staging inventory from reviewed changes, stage only that Task, and verify the index still matches. The inventory is execution evidence, not an approved file allowlist.
- Commit promptly per accepted Task using Issue, Plan, Run, and Task identity plus Run UID evidence. No amend, reset, force, squash, rebase, automatic merge, or unrelated staging.
- After a Task failure, mark dependents blocked; preserve its working-copy/patch/new-file evidence and continue independent Tasks from the same Run's committed history without cleaning the failed copy.
- A reviewed no-change Task may complete with recorded evidence and no new commit; this does not add historical Task adoption or resume.
- Scope expansion requires full re-planning and approval. Dynamic safe verification-command choice alone does not expand scope.

## Testing Decisions

- Exercise Execute at the workflow seam using a temporary real repository and fake role responses; inspect resulting commit history, diffs, labels, and report inputs.
- Assert all preflight failures precede editing; rejected review never commits; post-review diff mutation is rejected; semantic scope permits related new files while excluding artifacts.
- Verify dependent blocking, independent continuation, failed-copy retention, and exact Task-only staging, including filenames needing safe argument handling.
- Prior art is existing Git helpers and SDK invocation. No execution suite exists; avoid tests that only assert private helper calls or graph ordering.

## Out of Scope

Parallelism, identity authorization, plan/source historical drift checks, previous-Run reconciliation, resume, branch/MR creation, and destructive workspace cleanup.

## Further Notes

Implements semantic scope from ADR 0003 and role separation from ADR 0002. Final verification and remote push are completed by M6.

Source and ADR traceability: see the [spec index](README.md). This specification records accepted requirements, not completed implementation.

