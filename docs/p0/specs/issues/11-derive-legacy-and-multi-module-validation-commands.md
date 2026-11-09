# 11: Derive legacy and multi-module validation commands

**What to build:** Verify a legacy or web/API repository even without configured command strings, checking affected modules and recording how each command was chosen.

**Blocked by:** 09: Verify one Task with native Build and existing unit tests.

**Status:** draft (pending breakdown review)

- [ ] Inspect source layout, optional guidance, manifests/lockfiles, scripts, CI, and relevant affected dependents rather than only the root manifest.
- [ ] Agent derives safe commands and may replace Plan hints during Execute without new approval when semantic scope is unchanged.
- [ ] An existing unit suite without a configured command receives a derived command or UNVERIFIED/BLOCKED evidence, never a false absent-suite skip.
- [ ] Relevant configured tests remain mandatory and genuinely absent suites skip without creating target-project test infrastructure.
- [ ] Check all in-scope and demonstrably affected modules while avoiding unrelated modules.
- [ ] Record discovered/proposed origin, exact working directory, command/toolchain, result, and failure category per module in final evidence.
- [ ] Known unrelated baseline failures do not authorize scope expansion and failing mandatory checks still prevent SUCCESS.
- [ ] Fixture-driven workflow cases cover a legacy project and a web/API layout without actual model/GitLab services.

**Source specifications:** P0-SPEC-004, P0-SPEC-006.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

