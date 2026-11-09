# P0-SPEC-002: M2 — GitLab records and state transitions

Status: ready-for-agent (local specification).

Dependencies: P0-SPEC-001.

## Problem Statement

Issue discussions, plans, MR relationships, and final reports are not available through the current client, and a branch-filtered first-result lookup cannot identify the one user-created MR required by the prototype.

## Solution

Expose complete Issue context, reliable same-Project MR selection, append-only plan/report records, and explicit label transitions without taking over branch or MR creation.

## User Stories

1. As a developer, I want to have all Issue comments read across pages, so that requirements are not lost at an API page boundary.
2. As a developer, I want to have all related MRs inspected, so that an arbitrary first result cannot receive my changes.
3. As a developer, I want to link my MR using the Issue reference, so that the workflow discovers the intended user-created review.
4. As a developer, I want to receive a rejection for no open MR, so that I know what to create before Execute.
5. As a developer, I want to receive a rejection for several open MRs, so that ambiguous publication cannot occur.
6. As a developer, I want to reject fork or cross-Project MRs, so that the agent remains within the Issue repository.
7. As a developer, I want to publish each complete plan as a new comment, so that earlier plans remain visible.
8. As a developer, I want to load machine-readable plan data, so that Execute does not reinterpret prose to guess the agreement.
9. As a developer, I want to block on a broken latest plan, so that an older approval cannot authorize a fallback plan.
10. As a developer, I want to see explicit agent-label transitions, so that workflow status is consistent across GitLab tiers.
11. As a developer, I want to keep my unrelated labels, so that agent operations preserve my classifications.
12. As a developer, I want to keep my MR description unchanged, so that reports do not overwrite my own explanation.
13. As an operator, I want to deduplicate uncertain note delivery, so that a lost response does not create repeated comments.
14. As a developer, I want to use approval without actor lookup, so that the prototype responds only to label state.
15. As an operator, I want to receive useful API failure diagnostics without credentials, so that integration failures remain actionable.

## Implementation Decisions

- Extend the existing GitLab transport to paginate context/history reads, read Issue/MR relationships and individual MR state, append notes, and add/remove recognized labels.
- Use documented relationships, not branch/title guesses. Require exactly one open related MR, existing source branch, implement state, and both Project identities equal to the Issue Project.
- Store a marked, schema-validated plan payload with Markdown in the same comment. Select the highest published version; invalid/non-executable latest records block rather than falling back.
- Plan publications carry a unique publication UID; final reports carry the Run UID. After an uncertain POST, query for that identifier before any repost; unresolved ambiguity remains a delivery error.
- Use explicit label removals/additions, preserving unrelated labels and avoiding dependence on paid-tier exclusivity. Re-read after lock acquisition.
- Do not inspect approval actor, effective permissions, approval timestamps, or signed content.
- Preserve existing token-safe error wrapping and authentication patterns. Read-only access probes must not create temporary branches or push.
- Keep request/backoff bounds within the operation budget. Treat report delivery and label updates as separately observable outcomes.

## Testing Decisions

- Exercise the application workflow through an injected HTTP transport serving paginated and failed responses; assert resulting comments and label sets.
- Cover duplicate/missing/wrong-Project MRs, current-label conflicts, latest invalid payload, response-loss deduplication, unrelated-label preservation, and sanitized errors.
- Assert no branch/MR creation, merge, approval, or actor/role endpoints are called.
- Prior art is the existing GitLab request wrapper; no client tests exist. Prefer transport substitution beneath the workflow to isolated tests of every request helper.

## Out of Scope

A persistent local database, webhooks, distributed locks, historical approval reconstruction, branch/MR creation, and MR description ownership.

## Further Notes

GitLab comments are append-only by application behavior, not immutable server objects. The prototype must not advertise tamper-proof approval.

Source and ADR traceability: see the [spec index](README.md). This specification records accepted requirements, not completed implementation.

