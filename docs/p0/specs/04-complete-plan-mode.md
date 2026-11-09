# P0-SPEC-004: M4 — read-only complete Plan Mode

Status: ready-for-agent (local specification).

Dependencies: P0-SPEC-002, P0-SPEC-003.

## Problem Statement

Developers cannot currently obtain a reviewable Execution Plan before implementation, particularly when a legacy repository lacks agent guidance or preconfigured validation commands.

## Solution

Explore a fresh read-only snapshot, synthesize complete Tasks and validation hints from the Issue and all discussion, and publish one independently usable version for human approval.

## User Stories

1. As a developer, I want to plan without an existing MR, so that I can understand the work before preparing implementation.
2. As a developer, I want to use the default or appropriate MR target baseline, so that planning follows the agreed repository context.
3. As a developer, I want to override an ambiguous planning baseline, so that multiple related MRs do not cause an arbitrary choice.
4. As a developer, I want to have source, configuration, and CI explored, so that the plan reflects actual repository structure.
5. As a developer, I want to plan without agent guidance documentation, so that legacy repositories remain usable.
6. As a developer, I want to see requirement understanding and impact by module, so that I can assess what the agent intends.
7. As a developer, I want to receive small Tasks with dependencies and acceptance, so that I can review implementation order and behavior.
8. As a developer, I want to see discovered or proposed validation hints, so that missing scripts are acknowledged instead of fabricated.
9. As a developer, I want to keep planning free of installation and validation execution, so that inspection cannot mutate the repository.
10. As a developer, I want to receive explicit unresolved questions, so that conflicting requirements are not silently assumed.
11. As a developer, I want to have blocked plans rejected even when approved, so that a label cannot bypass incomplete requirements.
12. As a developer, I want to receive complete Markdown and JSON together, so that I and Execute share the same plan.
13. As a developer, I want to regenerate a full new version from all current discussion, so that a revised plan is usable without older comments.
14. As a developer, I want to invalidate approval on every regeneration, so that old approval does not authorize a new version.
15. As a developer, I want to use the plan manually without Execute, so that planning has value independently of agent implementation.
16. As an operator, I want to avoid duplicate versions after uncertain publication, so that plan history remains understandable.

## Implementation Decisions

- Use a separate Plan graph with Issue lock, in-progress transition, fresh snapshot inspection, full synthesis, validation, append-only publication, and needs-review transition.
- Branch selection: explicit override first; no related MR uses Project default; one related MR uses its target; several with one open use that target; multiple open or other ambiguous historical selection require an explicit override.
- After snapshot preparation, no repository writes, new product branches, commits, pushes, MR creation, dependency setup, Build, or Test execution are allowed.
- Plan data contains schema/Project/Issue identity, publication UID, version, timestamp, inspected baseline evidence, requirement understanding, module impact, Tasks, validation hints, executable state, and unresolved questions.
- Tasks contain purpose, module scope, dependencies, measurable acceptance, verification expectations, and explicit configuration/database-file coverage; never require a file allowlist.
- Validate schema, identity, versions, Task uniqueness/dependencies/acyclicity, working directories, prohibited scope, and clarity. Unresolved requirements produce executable=false.
- Render Markdown and JSON from the same validated object. Hints record origin and unverified confirmation; they are not evidence of successful commands.
- Read all comments again on regeneration, publish the full next version, invalidate approval, and use version-local Task identities. Do not add feedback classification or incremental reuse.
- Latest malformed/non-executable plan blocks Execute; do not silently select an earlier valid version. Serialize version assignment and deduplicate publication UID under the Issue lock.

## Testing Decisions

- Invoke Plan through the workflow using paginated discussion fixtures and a read-only repository; assert resulting content, labels, and forbidden external calls.
- Cover branch selection combinations, legacy discovery, conflicting requirements, schema rejection, complete regeneration, approval invalidation, and publication response loss.
- Use actual mount probes from runtime tests to prove write prohibition; model responses are structured fixtures for ordinary acceptance tests.
- Prior art is existing repository inspection/command detection and the graph entrypoint. No planner tests exist; keep assertions on the published agreement rather than prompt text or individual nodes.

## Out of Scope

Running setup/Build/Test during planning; incremental plan diffs; feedback ledgers; cross-version Task matching; prior-Run adoption; RAG; approver identity checks.

## Further Notes

Preserve the established execution-plan layout with explicit metadata/clarification additions. The old incremental plan-review layout is deferred for this prototype.

Source and ADR traceability: see the [spec index](README.md). This specification records accepted requirements, not completed implementation.

