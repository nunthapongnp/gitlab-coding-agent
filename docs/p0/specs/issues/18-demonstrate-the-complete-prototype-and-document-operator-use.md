# 18: Demonstrate the complete prototype and document operator use

**What to build:** Run the full disposable-Project happy path from Issue to reviewed MR delivery, with operator instructions and evidence that the accepted prototype limits hold.

**Blocked by:** 11: Derive legacy and multi-module validation commands; 12: Validate changed source through a dedicated Dockerfile builder; 16: Push only reviewed commits to the existing MR branch; 17: Deduplicate uncertain Plan/report delivery and independent label outcomes.

**Status:** draft (pending breakdown review)

- [ ] Walk through Plan, manual needs-review removal/approved label, user-created linked source branch/MR with implement label, fresh Execute, reviewed commits, validation, one final report, and human review/merge.
- [ ] Update operator guidance for configured model/toolchains, Docker launch, retained artifacts, explicit cleanup, statuses, and failure diagnostics.
- [ ] Adapt established plan/report templates for metadata, complete regeneration, sequential strategy, timestamps, UID, commit messages, canonical statuses, initial-plus-three counts, partial/no-change outcomes, and dynamic command evidence.
- [ ] Correct obsolete approval namespace and deferred parallel/cross-Run/incremental promises without adding unrelated behavior.
- [ ] Capture runtime evidence for role mounts/credentials, fresh contexts/volumes, locks, child cancellation, native/legacy validation, and supported builder behavior.
- [ ] Validate missing approval/MR, conflicting requirements/labels, review rejection, failed mandatory Build, absent tests, and delivery failure using fixtures or the disposable Project as appropriate.
- [ ] Ordinary tests remain offline with fake transports/worker results; the opt-in real walkthrough does not add target-repository integration/E2E features.
- [ ] No automatic merge/approval, RAG, parallel coders, source-drift analysis, human-work adoption, resume, real DB access, or deployment is introduced.

**Source specifications:** P0-SPEC-000, P0-SPEC-001, P0-SPEC-002, P0-SPEC-003, P0-SPEC-004, P0-SPEC-005, P0-SPEC-006, P0-SPEC-007.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

