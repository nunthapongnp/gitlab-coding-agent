# 02: Route Issue requests by validated labels

**What to build:** Use the same Project/Issue invocation to select Plan or Execute preparation, or receive an actionable rejection before product work.

**Blocked by:** 01: Make workflow invocation testable and stop unsafe publication.

**Status:** done — implemented on feature/p0-ticket-02 (5066919..6840a7e; PR to develop pending merge).

- [x] Plan selection uses Issue agent::plan; Execute selection requires Issue agent::approved and a related open MR marked agent::implement.
- [x] Reject conflicting recognized Issue/MR labels, including approved plus needs-review, missing triggers, and in-progress requests; preserve unrelated labels.
- [x] Approval depends only on current label values; no user, role, timestamp, or signed-approval lookup occurs.
- [x] Support a Plan-only branch override and optional setup/build/test hints; reject branch override in Execute and obsolete branch/MR creation controls.
- [x] Validate environment settings and expose the agreed default limits and exit/result distinctions; add no mode, execute, publish, or maximum-runtime flags.
- [x] Application-boundary cases assert correct routing and no editing/publication on rejection; unavailable downstream behavior fails clearly rather than falling back to the old graph.

**Source specifications:** P0-SPEC-001, P0-SPEC-002.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

