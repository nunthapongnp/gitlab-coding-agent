# 13: Apply independent bounded Task review and verification fixes

**What to build:** Revise a Task after review or verification failure with separate visible budgets, retaining review-before-commit throughout.

**Blocked by:** 09: Verify one Task with native Build and existing unit tests.

**Status:** draft (pending breakdown review)

- [ ] An initial review plus up to MAX_FIX_ATTEMPTS additional code fixes is allowed per Task; the default is three additional fixes.
- [ ] Task-verification fixes use a separate initial-plus-three counter that neither consumes nor resets review fixes.
- [ ] Each invocation uses fresh context while repairing the same Run's current code; failed evidence remains external to the product checkout.
- [ ] Counters and terminal Task outcomes are explicit; exhausted review/check failures stop that Task without committing rejected changes.
- [ ] Malformed agent responses cannot create an unbounded graph loop; graph recursion bounds account for Task count and bounded cycles.
- [ ] Fixes stay within approved semantic scope; genuine scope expansion stops for re-planning, while safe command changes do not require approval.
- [ ] Workflow tests prove separate limits, exact attempt counts, successful repair, exhausted failure, and truthful final retry summaries.

**Source specifications:** P0-SPEC-005, P0-SPEC-006.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

