# 08: Implement and independently review one Task before local commit

**What to build:** Implement one approved Task in a fresh Coding context, obtain an independent read-only review, and preserve only that accepted Product Change as a local Task commit.

**Blocked by:** 07: Start and finalize an Execute Run with one report.

**Status:** draft (pending breakdown review)

- [ ] Use a Coding worker without GitLab credentials or writable Git metadata and a separate read-only Reviewer context; real probes establish access restrictions.
- [ ] Coder explores current source; new related code/assets/tests are permitted without an approved file allowlist.
- [ ] Reviewer checks acceptance and semantic relevance, including new, binary, renamed, deleted, staged, unstaged, and untracked content.
- [ ] Reject documentation changes, Agent Artifacts, unrelated edits, prohibited capabilities, and configuration/dependency/schema changes without explicit Task coverage.
- [ ] Bind accepted review to the exact diff fingerprint, stage only execution-produced reviewed changes, and verify the index still matches before committing.
- [ ] Create an identifiable Issue/Plan/Run/Task commit with Run UID evidence; preserve prior commits and never amend, reset, squash, force, rebase, or create branches/MRs.
- [ ] Rejected review does not commit; genuinely complete no-change work may finish with recorded evidence and no empty commit.
- [ ] The final local/MR report records the Task outcome and local commit but does not claim completed remote publication or successful missing Build.

**Source specifications:** P0-SPEC-003, P0-SPEC-005, P0-SPEC-007.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

