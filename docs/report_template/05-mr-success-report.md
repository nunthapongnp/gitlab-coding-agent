# AI Execution Report — RUN-[NUMBER]

**Status:** SUCCESS  
**Issue:** #[ISSUE_IID]  
**Merge Request:** ![MR_IID]  
**Plan Version:** PLAN-v[NUMBER]  
**Execution Run:** RUN-[NUMBER]  
**Source Branch:** `[EXISTING_SOURCE_BRANCH]`

## 1. Execution Summary
[One-paragraph outcome.]

| Total Tasks | Completed | Failed | Blocked | Already Done by User |
|---:|---:|---:|---:|---:|
| [N] | [N] | 0 | 0 | [N] |

## 2. Task Results

| Task | Status | Commit SHA | Notes |
|---|---|---|---|
| TASK-001 | COMPLETED | `[sha]` | [What changed] |
| TASK-002 | COMPLETED | `[sha]` | [What changed] |
| TASK-003 | COMPLETED | — | [Already implemented by user; reference existing commit if known] |

## 3. Verification Results

### [Relevant Module]
| Check | Status | Command | Notes |
|---|---|---|---|
| Build | PASS | `[command]` | [Runtime/environment] |
| Unit Test | PASS / SKIPPED | `[command or N/A]` | [SKIPPED reason if no command] |

## 4. Retry Summary
[Number of fix attempts, any resolved errors, or 'No retries needed'.]

## 5. Push Result
- **Push:** SUCCESS
- **Destination:** Existing MR source branch `[branch]`
- **New branch created:** No
- **Latest pushed commit:** `[sha]`

## 6. Known Issues / Skipped Checks
[None, or explicit list and reasons. Do not claim all tests passed if some were skipped.]

**Final Result:** All mandatory configured checks passed. Ready for human review; not a guarantee of bug-free behavior.
