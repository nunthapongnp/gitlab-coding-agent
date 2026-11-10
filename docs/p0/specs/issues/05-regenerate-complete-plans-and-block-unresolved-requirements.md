# 05: Regenerate complete plans and block unresolved requirements

**What to build:** Clarify or change an Issue and obtain a new independently usable plan version whose approval is reset; unresolved questions remain non-executable.

**Blocked by:** 04: Publish the first complete Execution Plan.

**Status:** done — implemented on feature/p0-ticket-05 (a9c4177..HEAD; PR #5 to develop)

- [ ] Read the current Issue and all comments again and publish a full incremented version; do not edit old plan comments.
- [ ] Invalidate prior approval for every regeneration, including mostly unchanged content, and finish with Issue needs-review.
- [ ] Publish explicit unresolved questions with executable=false when requirements conflict or are insufficient.
- [ ] Schema-invalid generated content is not published as a usable plan; malformed/non-executable latest records block Execute rather than falling back to earlier versions.
- [ ] Task IDs are version-local; do not implement incremental changes, a feedback ledger, cross-version Task matching, or prior-Run adoption.
- [ ] Workflow cases demonstrate regeneration, approval invalidation, conflicting requirements, and latest-plan rejection without identity checks.

**Source specifications:** P0-SPEC-002, P0-SPEC-004.

**Verification:** Demonstrate observable behavior through the existing application/workflow seam. Use offline controlled transports and temporary repositories by default; use real runtime probes where permissions, locks, cancellation, or builder behavior are the property under test.

## Implementation notes

- Under the Issue lock, `activate_issue` re-reads the Issue labels before the
  `agent::plan` → `agent::in-progress` transition (§3.2 race guard). When that
  re-read finds the valid regeneration combination — `agent::plan` together
  with exactly one of `agent::approved` / `agent::needs-review` — the run
  treats it as a regeneration request and proceeds. This covers a human
  applying `agent::approved` between routing (read without the lock) and the
  transition: routing never saw that approval, and the regeneration
  invalidates (removes) it rather than blocking the run. Any other label
  mutation under the lock still blocks the run truthfully
  (`inspection_blocked`; nothing is transitioned, inspected, or published).
- A schema-invalid latest plan record whose version is still determinable does
  not block a regeneration: regeneration is the remedy, so the run proceeds
  and publishes the next full version as a new comment. Blocking execution on
  a malformed/invalid latest record (version undeterminable, or invalid at or
  above the highest valid version) is Execute's job (ticket 06); version
  allocation itself already refuses to reuse an undeterminable version.

