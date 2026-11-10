> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../../MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# 12: Validate changed source through a dedicated Dockerfile builder

**What to build:** Use the repository's relevant Dockerfile target as Build evidence when it actually compiles or validates the changed source, without exposing host daemon control.

**Blocked by:** 09: Verify one Task with native Build and existing unit tests.

**Status:** draft (pending breakdown review)

- [ ] Verification connects to a dedicated builder with new per-Run state; coding tools receive no host Docker socket or builder lifecycle authority.
- [ ] Setup checks builder host/security requirements; unsupported configuration reports BLOCKED without automatic privileged fallback.
- [ ] Supply the relevant context and selected target and prove the changed source is validated; copying stale artifacts or defining runtime commands alone cannot substantiate compilation.
- [ ] Run relevant unit tests separately when the selected build graph does not execute them.
- [ ] Do not deploy, push an image registry artifact, reuse previous-Run builder cache, or commit image exports/logs.
- [ ] Report target, command, evidence, and failures through the same verification/Run contracts as native Build.
- [ ] Real builder probes and a stale-artifact fixture demonstrate truthful PASS versus insufficient evidence.

**Source specifications:** P0-SPEC-003, P0-SPEC-006.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

