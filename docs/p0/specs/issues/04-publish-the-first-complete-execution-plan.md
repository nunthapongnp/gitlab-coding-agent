# 04: Publish the first complete Execution Plan

**What to build:** Request Plan for a clear Issue and receive one complete Markdown/JSON plan comment and an Issue ready for human review.

**Blocked by:** 03: Inspect a fresh repository in a read-only Docker operation.

**Status:** in-progress — implemented on feature/p0-ticket-04

- [ ] Read Project/Issue data, all comment pages, and related MR context; preserve and use requirements beyond the first API page.
- [ ] Select the agreed planning baseline: explicit override, default without MR, single-MR target, or unique open-MR target; ambiguous cases request an explicit branch.
- [ ] Generate and validate a complete plan with schema/Project/Issue/version/publication identity, inspected baseline evidence, requirement understanding, impact, and Tasks with scope, dependencies, acceptance, and verification hints.
- [ ] Validate unique Task IDs, known dependencies, acyclicity, working directories, and explicit configuration/database-file scope; do not require allowed_paths.
- [ ] Render Markdown and structured data from the same validated object and append one complete plan version under the Issue lock.
- [ ] Transition Issue plan to in-progress to needs-review using explicit add/remove operations without erasing unrelated labels.
- [ ] No setup, Build, Test, product edit, new branch, commit, push, or MR creation occurs; discovered/proposed commands remain unverified hints.
- [ ] A workflow acceptance case demonstrates a usable first plan for a legacy repository without agent guidance documentation.

**Source specifications:** P0-SPEC-002, P0-SPEC-004.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

