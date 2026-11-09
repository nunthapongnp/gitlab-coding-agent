# 01: Make workflow invocation testable and stop unsafe publication

**What to build:** Invoke the existing application with controlled external dependencies and receive truthful failure diagnostics instead of automatic branch/MR creation or success without Build evidence.

**Blocked by:** None (can start immediately).

**Status:** draft (pending breakdown review)

- [ ] The application workflow accepts injectable GitLab, agent, verification, and clock dependencies without a new user-facing mode or framework.
- [ ] Existing argument/settings errors return actionable diagnostics through the application boundary.
- [ ] The temporary incomplete-verification publication route and unconditional success messaging are disabled; the old automatic branch/MR creation path cannot run during the transition.
- [ ] A workflow-level fixture exercises an Issue request without calling real GitLab or a model and proves that incomplete verification cannot publish or return success.
- [ ] Existing useful transport/Git behavior remains available behind the workflow seam; do not perform an unrelated wide rewrite.

**Source specifications:** P0-SPEC-001, P0-SPEC-000.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

