> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../../MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# 05: Regenerate complete plans and block unresolved requirements

**What to build:** Clarify or change an Issue and obtain a new independently usable plan version whose approval is reset; unresolved questions remain non-executable.

**Blocked by:** 04: Publish the first complete Execution Plan.

**Status:** draft (pending breakdown review)

- [ ] Read the current Issue and all comments again and publish a full incremented version; do not edit old plan comments.
- [ ] Invalidate prior approval for every regeneration, including mostly unchanged content, and finish with Issue needs-review.
- [ ] Publish explicit unresolved questions with executable=false when requirements conflict or are insufficient.
- [ ] Schema-invalid generated content is not published as a usable plan; malformed/non-executable latest records block Execute rather than falling back to earlier versions.
- [ ] Task IDs are version-local; do not implement incremental changes, a feedback ledger, cross-version Task matching, or prior-Run adoption.
- [ ] Workflow cases demonstrate regeneration, approval invalidation, conflicting requirements, and latest-plan rejection without identity checks.

**Source specifications:** P0-SPEC-002, P0-SPEC-004.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

