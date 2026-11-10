> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../p0/MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# AI Execution Plan — PLAN-v[NUMBER]

**Status:** REVIEW REQUIRED  
**Issue:** #[ISSUE_IID]  .
**Repository:** [GROUP/PROJECT]  
**Plan Version:** PLAN-v[NUMBER]

## 1. Requirement Understanding
[Concise restatement of the expected outcome. Assumptions are explicit.]

## 2. Impact Analysis (this repository only)

| Module / Component | Impact | Reason |
|---|---|---|
| [web/search] | [Required / Not required] | [Why] |
| [api/search] | [Required / Not required] | [Why] |

## 3. Execution Tasks

### TASK-001 — [Short title]
- **Status:** PENDING
- **Scope:** [The actual change]
- **Files / Modules:** [Module/component-level locations, not an exhaustive file list]
- **Dependencies:** None
- **Acceptance:** [Measurable expected behavior]
- **Verification:** [Specific test/check/scenario]
- **Implementation Guidance (only if helpful):** [Example business rule, API contract, command, before/after, or reproducible scenario]

### TASK-002 — [Short title]
- **Status:** PENDING
- **Scope:** [The actual change]
- **Files / Modules:** [Module/component]
- **Dependencies:** TASK-001
- **Acceptance:** [Measurable expected behavior]
- **Verification:** [Specific test/check/scenario]

<!-- Additional tasks use stable TASK IDs. For user-made changes, the Agent may mark a task COMPLETED or PARTIAL with evidence from the branch and commits. -->

## 4. Repository Validation

### [Web / API / Relevant module]
| Check | Command | Source | Notes |
|---|---|---|---|
| Build | `[actual or proposed command]` | [Detected / Proposed / Unverified] | [Runtime, working directory] |
| Unit Test | `[command or N/A]` | [Detected / Proposed / Unverified] | [If no command is configured: SKIPPED] |

- **Detected:** Found in `AGENTS.md`, package scripts, Maven/Gradle, Makefile/scripts, or CI configuration.
- **Proposed:** Agent-derived command, explicitly identified as such.
- **Unverified:** Not confirmed to work in the intended environment.
- **Build must pass**; test is mandatory **when a relevant test command is configured**.
- Validate only modules in scope, plus modules demonstrably affected by the change.

## 5. Execution Strategy
- Task order follows dependencies; independent tasks may be parallelized with file ownership controls.
- Shared workspace, no cross-repository changes.
- One identifiable commit for each completed code-changing Task.
- The source branch and MR are created **by the user** before Execute.

## 6. Constraints
- No edits outside the Issue repository; no deployment.
- Do not create or publish branches or create MRs.
- Preserve existing user commits and use the MR source branch.
