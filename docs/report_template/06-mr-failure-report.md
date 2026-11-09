# AI Execution Report — RUN-[NUMBER]

**Status:** FAILED  
**Issue:** #[ISSUE_IID]  
**Merge Request:** ![MR_IID]  
**Plan Version:** PLAN-v[NUMBER]  
**Execution Run:** RUN-[NUMBER]  
**Source Branch:** `[EXISTING_SOURCE_BRANCH]`

## 1. Execution Summary
[Short summary of what was completed and what remains broken.]

| Total Tasks | Completed | Failed | Blocked | Skipped |
|---:|---:|---:|---:|---:|
| [N] | [N] | [N] | [N] | [N] |

## 2. Task Results
| Task | Status | Commit SHA | Reason |
|---|---|---|---|
| TASK-001 | COMPLETED | `[sha]` | — |
| TASK-002 | PARTIAL / FAILED | `[sha or —]` | [Error] |
| TASK-003 | BLOCKED | — | Depends on TASK-002 |

## 3. Verification Results
### [Relevant Module]
| Check | Status | Command | Attempts |
|---|---|---|---|
| Build | FAIL | `[command]` | [up to 3 fix attempts] |
| Unit Test | PASS / FAIL / SKIPPED / BLOCKED | `[command or N/A]` | [count / reason] |

**Error summary:** [Actual failing stage, useful error lines, and likely cause; do not paste secrets]  
**Relevant log location:** `[relative log path / artifact link, if available]`  
**Final attempt:** [N of 3]

## 4. Push Result
- **Push:** SUCCESS / FAILED / NOT ATTEMPTED
- **Destination:** Existing MR source branch `[branch]`
- **New branch created:** No
- **Latest pushed commit:** `[sha or N/A]`

## 5. Required Attention
[Concrete next action for reviewer. A follow-up Execute after user changes requires re-plan and approval.]

**Final Result:** FAILED. Do not merge based on this report.
