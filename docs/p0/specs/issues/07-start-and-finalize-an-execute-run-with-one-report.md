> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../../MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# 07: Start and finalize an Execute Run with one report

**What to build:** After preflight, start a uniquely identified Run and retain a truthful final local/MR report even when execution fails before producing code.

**Blocked by:** 06: Validate a user-created MR and fresh Execute checkout.

**Status:** draft (pending breakdown review)

- [ ] Assign a unique internal Run UID and a display Run number from published report history under the Issue lock without resuming prior Tasks.
- [ ] Start the Run clock after preflight and transition the MR to in-progress while retaining Issue approval.
- [ ] Record typed Run/Task/check/delivery evidence including identities, initial source SHA, timings, commits, checks, push outcome, retries, and next action; empty work remains a truthful failed result.
- [ ] Write local structured evidence before attempting one final append-only MR report; leave the MR description unchanged and do not post stage/Task progress comments.
- [ ] In finalization independently attempt report delivery, MR needs-review, available cancellation/evidence collection, and lock release.
- [ ] API errors preserve local evidence and return the agreed publication/infrastructure exit distinctions; immediate kill/API outage is not guaranteed delivery.
- [ ] A failing-worker workflow fixture demonstrates the entire started-Run lifecycle without requiring a Product Change or push.
- [ ] Retained artifacts are diagnostics only; no automatic recovery, reset, or cleanup occurs.

**Source specifications:** P0-SPEC-002, P0-SPEC-007.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

