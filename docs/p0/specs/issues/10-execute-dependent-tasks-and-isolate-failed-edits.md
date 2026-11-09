# 10: Execute dependent Tasks and isolate failed edits

**What to build:** Process multiple Tasks sequentially, block failed dependencies, and continue independent Tasks without including failed edits in their work.

**Blocked by:** 09: Verify one Task with native Build and existing unit tests.

**Status:** draft (pending breakdown review)

- [ ] Select dependency-ready Tasks in topological order with only one active Coding Agent and one active product editing workspace.
- [ ] Use PENDING, IN_PROGRESS, REVIEW, REVISION, COMPLETED, FAILED, BLOCKED runtime states and canonical terminal report statuses.
- [ ] Commit completed reviewed Tasks promptly instead of waiting for other Tasks.
- [ ] When a Task fails, preserve its working-copy/patch/new-file/log evidence and block its descendants.
- [ ] Continue independent Tasks from the same Run's reviewed committed history using an uncontaminated working copy; do not reset/clean the failed copy or reuse an earlier Run.
- [ ] Reports distinguish completed, failed, blocked, and pending work; partial unfinished edits are evidence, not a conflicting terminal PARTIAL state.
- [ ] A multi-Task workflow proves independent continuation and preservation of original and Task commits; no parallel scheduler or restart recovery is introduced.

**Source specifications:** P0-SPEC-005, P0-SPEC-006.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

