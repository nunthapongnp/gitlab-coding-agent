> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../../MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# 02: Route Issue requests by validated labels

**What to build:** Use the same Project/Issue invocation to select Plan or Execute preparation, or receive an actionable rejection before product work.

**Blocked by:** 01: Make workflow invocation testable and stop unsafe publication.

**Status:** draft (pending breakdown review)

- [ ] Plan selection uses Issue agent::plan; Execute selection requires Issue agent::approved and a related open MR marked agent::implement.
- [ ] Reject conflicting recognized Issue/MR labels, including approved plus needs-review, missing triggers, and in-progress requests; preserve unrelated labels.
- [ ] Approval depends only on current label values; no user, role, timestamp, or signed-approval lookup occurs.
- [ ] Support a Plan-only branch override and optional setup/build/test hints; reject branch override in Execute and obsolete branch/MR creation controls.
- [ ] Validate environment settings and expose the agreed default limits and exit/result distinctions; add no mode, execute, publish, or maximum-runtime flags.
- [ ] Application-boundary cases assert correct routing and no editing/publication on rejection; unavailable downstream behavior fails clearly rather than falling back to the old graph.

**Source specifications:** P0-SPEC-001, P0-SPEC-002.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

