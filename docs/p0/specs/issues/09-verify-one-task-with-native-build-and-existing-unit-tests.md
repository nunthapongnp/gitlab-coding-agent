# 09: Verify one Task with native Build and existing unit tests

**What to build:** Run a narrow repository-native verification path in the Run environment, install declared dependencies when needed, and attach actual Build/Test evidence to the Task and final report.

**Blocked by:** 08: Implement and independently review one Task before local commit.

**Status:** draft (pending breakdown review)

- [ ] A relevant single-module fixture builds/compiles in a verification environment without coordinator credentials.
- [ ] Use detected native commands or explicit hints with recorded argv, working directory, toolchain, origin, timing, exit code, logs, and outcome.
- [ ] Install dependencies from declared sources/lockfiles without unapproved manifest changes or host mutation.
- [ ] Inspect referenced scripts/targets and reject real DB access, environment migrations, deployment, integration/E2E, or cross-Project effects; shell use is deliberate and inspected.
- [ ] Run existing relevant unit suites; absent suites are SKIPPED with reason, missing credible Build is UNVERIFIED, environment failure is BLOCKED, and code failure is FAIL.
- [ ] Mandatory verification is not bypassed by push or missing configuration; scope expansion requires a new Plan.
- [ ] Task and Run reports retain actual evidence and classify incomplete work honestly; new commits remain unpublished until the push slice lands.
- [ ] Workflow tests exercise command execution outcomes using temporary fixtures without live GitLab/model calls.

**Source specifications:** P0-SPEC-005, P0-SPEC-006.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

