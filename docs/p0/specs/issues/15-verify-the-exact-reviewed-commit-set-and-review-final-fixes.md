> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../../MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# 15: Verify the exact reviewed commit set and review final fixes

**What to build:** Run final repository validation against precisely the reviewed commits intended for publication, applying bounded in-scope fixes through review and new Task commits.

**Blocked by:** 10: Execute dependent Tasks and isolate failed edits; 13: Apply independent bounded Task review and verification fixes; 14: Cancel over-budget work and preserve finalization evidence.

**Status:** draft (pending breakdown review)

- [ ] Create a verification snapshot of the current Run's reviewed committed history; failed/uncommitted edits must not influence claimed final results.
- [ ] Run mandatory Build and relevant unit checks for the scope, recording per-module evidence and failures.
- [ ] Repository-level verification has its own initial-plus-three code-fix counter, distinct from used Task counters and bounded by remaining Run time.
- [ ] Return code regressions to the responsible in-scope Task; retain its consumed counters, re-review fixes, and add new identifiable commits without amending/squashing.
- [ ] If scope expansion or environment blockage prevents completion, preserve evidence and report the actual failure rather than fabricating a passing check.
- [ ] A fixture containing a dirty failed Task proves final checks see only the reviewed commit set.
- [ ] Candidate outcomes distinguish all-complete, some-complete with passing mandatory checks, mandatory-check failure, and verified no-change work; publication still determines final delivery.

**Source specifications:** P0-SPEC-005, P0-SPEC-006, P0-SPEC-007.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

