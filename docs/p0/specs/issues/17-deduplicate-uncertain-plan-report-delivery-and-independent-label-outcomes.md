> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../../MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# 17: Deduplicate uncertain Plan/report delivery and independent label outcomes

**What to build:** Handle lost API responses without duplicate plan versions or final reports and retain clear local delivery evidence when GitLab cannot confirm the result.

**Blocked by:** 07: Start and finalize an Execute Run with one report.

**Status:** draft (pending breakdown review)

- [ ] Plan publication uses a publication UID and final report publication uses the unique Run UID; search all relevant note pages before reposting an uncertain request.
- [ ] Under the Issue lock, concurrent local invocations cannot allocate/publish the same plan version.
- [ ] When a response was lost but the note exists, reuse its identity rather than posting again.
- [ ] If delivery cannot be checked, retain UNKNOWN/error locally without blind repeated POSTs or falsely declaring success.
- [ ] Report failure does not prevent an independent final needs-review attempt or lock release; label failure is separately visible.
- [ ] Preserve unrelated labels and append-only comments while respecting the remaining operation budget and sanitized error output.
- [ ] Workflow failure fixtures cover POST success/response loss, query outage, label failure, and retained local evidence; do not claim crash-proof exactly-once delivery.

**Source specifications:** P0-SPEC-002, P0-SPEC-004, P0-SPEC-007.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

