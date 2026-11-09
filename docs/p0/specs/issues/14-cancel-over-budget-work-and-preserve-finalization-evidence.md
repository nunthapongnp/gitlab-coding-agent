# 14: Cancel over-budget work and preserve finalization evidence

**What to build:** Stop stalled model or verification work at the configured limits while preserving partial logs, finalization opportunity, and existing commits.

**Blocked by:** 09: Verify one Task with native Build and existing unit tests.

**Status:** draft (pending breakdown review)

- [ ] Enforce 60 iterations and a 1800-second wall-clock invocation limit by default rather than relying only on an LLM request timeout.
- [ ] Verification commands are limited to 1200 seconds and remaining Run time; the whole Execute Run is limited to 7200 seconds after preflight including finalization.
- [ ] Reserve remaining time for finalization and do not begin another long invocation when that would consume the remaining finalization opportunity.
- [ ] Supervised cancellation stops child process groups/active work and retains available partial output with BLOCKED/failed evidence.
- [ ] Budgets are environment configuration; add no maximum-runtime CLI flag.
- [ ] Timeout finalization attempts the local report, one MR report, needs-review, and lock release; no destructive recovery or guarantee after immediate kill is claimed.
- [ ] Use a controlled clock at the workflow seam plus real runtime child-process probes to prove termination and retained evidence.

**Source specifications:** P0-SPEC-003, P0-SPEC-006, P0-SPEC-007.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

