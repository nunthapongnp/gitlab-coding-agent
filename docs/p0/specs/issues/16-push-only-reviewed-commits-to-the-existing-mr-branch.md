> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../../MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# 16: Push only reviewed commits to the existing MR branch

**What to build:** Publish the reviewed Run commit set through a normal push to the user's existing source branch and report delivery and verification independently.

**Blocked by:** 15: Verify the exact reviewed commit set and review final fixes.

**Status:** draft (pending breakdown review)

- [ ] Inspect every introduced commit and the full Run change range from initial source SHA, not just net final diff; reject any unreviewed, unrelated, documentation, or Agent Artifact commit even if later deleted.
- [ ] Push only to the existing same-Project MR source branch without modifying target/title/description or creating branches/MRs.
- [ ] Retry transient transport failure at most three additional times using identical existing commits; do not create duplicate commits.
- [ ] Stop on authentication, policy, or remote-history rejection without pull, rebase, merge, reset, force, squash, or overwriting remote work.
- [ ] After exhausted verification fixes, permitted reviewed commits may still be pushed but mandatory-check failure remains FAILED; dirty failed edits never enter publication.
- [ ] SUCCESS requires required Tasks/checks and necessary push; PARTIAL_SUCCESS requires some completed work plus passing mandatory checks and successful permitted push.
- [ ] Verified no-change work uses NOT_NEEDED rather than empty commits; no completed Task or failed necessary push cannot produce SUCCESS.
- [ ] Temporary real remotes and injected transport failures prove preserved user history, exact commit IDs, retry limits, and final report/exit outcomes.

**Source specifications:** P0-SPEC-005, P0-SPEC-006, P0-SPEC-007.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

