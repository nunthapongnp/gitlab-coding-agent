# 06: Validate a user-created MR and fresh Execute checkout

**What to build:** Prepare execution only for the latest executable Approved Plan and exactly one valid existing MR, with local diagnostics for rejected requests.

**Blocked by:** 05: Regenerate complete plans and block unresolved requirements.

**Status:** draft (pending breakdown review)

- [ ] Require the current approval label and latest executable matching Project/Issue plan; reject an approved clarification-required plan.
- [ ] Inspect all related open MRs and require exactly one, source and target Project identities matching the Issue Project, implement state, and an existing source branch.
- [ ] Acquire Issue and branch locks in stable order and re-read labels/MR/latest plan before beginning work.
- [ ] Clone only the Issue repository's existing MR source branch into a fresh workspace; inspect origin, branch, staged/unstaged state, and initial source SHA.
- [ ] Reject dirty, missing, wrong-Project/fork/closed/ambiguous preparation without stash, reset, temporary push, or product editing.
- [ ] Read-only publication-access probing does not create a branch or mutate remote state.
- [ ] Preflight rejection is CLI/local evidence, not an Execute Run or MR execution report; successful preparation returns context for the Run lifecycle.
- [ ] Tests prove no role lookup, historical source-drift analysis, old Task adoption, branch/MR creation, or changes to MR metadata.

**Source specifications:** P0-SPEC-002, P0-SPEC-003, P0-SPEC-005.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

