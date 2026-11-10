---
status: superseded
---

> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# Approve task purpose and review the resulting changes instead of requiring file allowlists

Approved Tasks define requirement scope, affected modules, and acceptance criteria rather than an exhaustive `allowed_paths` list. New files are permitted when needed for the Issue, and relevant runtime assets, unit tests, configuration, dependencies, and schema changes follow the explicit scope rules; documentation changes and agent memory, summaries, and logs must not enter product commits. The coordinator publishes only the changes of a Task that an independent Reviewer has accepted, trading advance file enumeration for review of the actual change and explicit staging evidence.

## Consequences

- A file being inside the repository does not establish that its change is related to the Issue.
- The changed-file inventory produced during execution is evidence for staging and reporting, not a new approval allowlist.
- Reviewer approval includes both acceptance criteria and relevance of the full diff. An unresolved scope question prevents commit/publication of that change.
- Agent artifacts live outside the checkout. The publication path must also detect accidental artifacts and reject them before commit and push.
- Configuration and database-file changes require explicit Task coverage. Deployment, real-database access, infrastructure administration, and secret changes remain excluded.
- Work outside the approved purpose requires a new complete plan and new approval; choosing a different safe build command within the same scope does not.

Implementation details: [Phase 0 implementation plan](../CODING_AGENT_PHASE0_IMPLEMENTATION_PLAN.md).
