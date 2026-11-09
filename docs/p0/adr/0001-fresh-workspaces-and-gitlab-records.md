---
status: accepted
---

# Start every run with fresh context and keep work agreements in GitLab

Each Execute Run creates new agent context, clones the existing MR source branch into a new workspace volume, and explores the repository again; it does not restore an earlier conversation, workspace, or task checkpoint. Complete plans, their machine-readable payloads, approval labels, and final reports live in GitLab so a fresh process can load the current work agreement without implementing persistent run state. This trades repeated exploration and dependency downloads for simpler, reproducible prototype behavior; Phase 1 RAG may assist exploration but cannot replace checking the current source or establish approval.

## Consequences

- Reusing a workspace or SDK conversation across runs is outside the prototype contract.
- Repairs within a run reuse that run's working code; separate agent invocations still have separate contexts.
- A shared control volume contains execution locks, while retained logs and failed workspaces are diagnostic evidence rather than resume checkpoints.
- GitLab comments are append-only by application convention; the prototype does not provide cryptographic plan approval or reconstruct historical authorization.
- A process crash can leave unpublished commits and no final MR report. Preserve available evidence and do not perform automatic cleanup or recovery.

Implementation details: [Phase 0 implementation plan](../CODING_AGENT_PHASE0_IMPLEMENTATION_PLAN.md).
