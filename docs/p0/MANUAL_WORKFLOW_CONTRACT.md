# Current Phase 0 manual workflow contract

Status: accepted design. This contract supersedes conflicting earlier Phase 0 documents, ADR requirements, report templates, specifications, and draft tickets. Authoritative implementation work: [GitHub Issue #6](https://github.com/nunthapongnp/gitlab-coding-agent/issues/6) and its eleven linked slices. The historical eighteen-ticket breakdown is inactive.

The manual explore/implement workflow is implemented, including aggregate commit and normal push after verification and independent review. The [operator guide](OPERATOR_GUIDE.md) describes prerequisites, human handoffs and reset paths. The [walkthrough evidence](WALKTHROUGH_EVIDENCE.md) separates deterministic checks from the opt-in live pilot; the live walkthrough has not been performed.

## Invocation and ownership

Use `explore --project PROJECT --issue IID` and `implement --project PROJECT --issue IID --mr IID`. IIDs must be positive. The subcommand chooses the operation; labels never select a mode or trigger a background run. The human owns Issue creation, approval, source branch/MR creation, toolchain preparation, reset, acceptance, and merge. The agent never creates a product branch/MR, changes MR metadata/target, approves, or merges.

Develop and merge framework tickets on `feature/phase-0`; preserve the main POC baseline. Keep the application/workflow boundary with replaceable GitLab, role runtime, verification command execution, and monotonic clock at `Application.invoke`. Importing the entrypoint starts no workflow; there is no POC fallback.

## GitLab agreement and labels

One Execution Plan is one complete unversioned agreement in one marked Issue comment. Render human Markdown from its validated structured payload. Include schema and Project/Issue identity, inspected branch/commit, requirements, affected modules, implementation checklist, acceptance mapping, intended verification, optional command suggestions/evidence/working directories, and unresolved questions. Task means a checklist item, with no scheduler, persistence identity, per-Task retry, commit, or publication.

Explore inspects a fresh local checkout of the Project default branch read-only. Read Issue/comments with complete pagination. A prior marked plan prevents another plan. Unresolved requirements prevent executability; absent suggested commands alone do not. Implement loads exactly one valid executable plan and Issue approval from GitLab even with all explore files absent. Missing, duplicate, malformed, or mismatched plans stop before coding.

Recognized labels: `agent::explore`, `agent::implement`, `agent::in-progress`, `agent::needs-review`, `agent::approved`. Issue flow: explore request becomes in-progress, then needs-review; the human replaces needs-review with approved. Explicit MR flow: implement request becomes in-progress, then needs-review after success or controlled stop. Preserve unrelated labels and Issue approval during implement. Reject contradictory states. Approval checks label values only.

Preflight validates an open same-Project MR, existing source branch distinct from target, Issue linkage, implement label, and planning-baseline target. Preserve title, description, draft status, existing history, source/target. Empty draft MRs are supported. A prior final Execution Report or stale in-progress state cannot start another run. Retry requires a new human-created branch/MR. Changed requirements require a new Issue or complete human reset of its agreement. Never automatically delete records or resume a crashed run.

## Local runtime and model connection

Use fresh isolated local directories and new role contexts per run. No Docker, Agent Server, shared control volume, or container builder is required. Operators prepare compatible Java/Node/toolchains. Permit repository dependency installation; do not upgrade host toolchains or unrelated dependencies to conceal failure. Keep a runtime boundary for future Docker. Local role restrictions are application policy, not OS-enforced sandboxing.

All roles use the team's LiteLLM Proxy via required `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`. No obsolete provider-specific settings or implicit defaults remain. Supply the complete API root; preserve the proxy alias and apply `openai/` routing once. Independent review means fresh context, not another model. Keep model/publication credentials out of prompts, repository command environments, logs, remote URLs, and Git config. The coordinator owns GitLab writes and publication.

## Whole-Issue implementation and verification

One Coding Agent implements the whole Issue sequentially in one workspace, retaining edits/context for repairs. Semantic scope follows plan purpose, modules, and acceptance. Related new files are allowed. Configuration, dependency, schema, and database-file edits require explicit coverage. Stop if requirements/scope must change; no automatic replan or interactive mid-run approval.

The fixed pilot has Angular frontend and Spring Boot backend modules. Discover commands in order: Approved Plan; repository readme/agent/contribution guidance; Node/Angular manifests; Maven descriptors; scripts/Make/configuration; GitLab CI corroboration; reasoned analysis. Record module, directory, command, provenance, actual exit status/time, and logs. Respect host command conventions. CI is evidence only; never trigger deployment/pipeline execution.

Run relevant baseline checks before coding. Existing failures/toolchain blockers stop rather than expand scope. After coding/repair run mandatory validation relevant to changed deliverables and relevant existing tests. New focused tests are optional when practical; added tests must pass. A runner without cases is not a suite. Missing/inapplicable tests may be SKIPPED with reasons; failures remain FAIL. Missing mandatory validation is BLOCKED or UNVERIFIED and forbids publication. Compilation does not prove behavioral acceptance.

After checks pass, a fresh read-only Reviewer assesses the complete change, acceptance coverage, scope relevance, correctness, and verification evidence. It returns structured findings/approval; it cannot edit, commit, push, or approve the MR. Send findings to the Coding Agent. Repairs invalidate prior verification/review; verify and review final content again.

## Budgets, publication, and reporting

`MAX_FIX_ATTEMPTS=3` allows three shared repairs after initial coding, at most four attempts across build/test/review failures. `EXECUTION_MAX_RUNTIME_SECONDS=3600` is a monotonic deadline covering accepted preparation, baseline, coding, review, checks, commit, push, and normal finalization. Clip child timeouts, stop children on expiry, reserve finalization time, and prohibit new mutations/publication after expiry. SDK iteration/request limits remain separate. Reports/labels are best effort after timeout or API/process failure.

Publish only the complete verified and independently reviewed Issue. Create one aggregate commit and normally push to the existing MR source branch, preserving history and rejecting intervening remote changes. No partial publication, per-Task commits, forced push, rebase, reset, squash, or merge. Stage the exact reviewed Product Change inventory including related new files; exclude Agent Artifacts, operational documentation, logs, secrets, and unrelated files. Reject empty changes, unexpected index/history changes, and late edits inconsistent with reviewed content.

Append one final MR Execution Report per accepted Execute Run. Include run/plan/source identities, timing/outcome, behavior/files, acceptance coverage, checks/skips/blockers, review/repairs, commit/push, diagnostic locations, and next action. Separate local commit, remote delivery, report, and label outcomes. SUCCESS requires confirmed delivery of complete verified/reviewed work. Retain failure evidence and inspect uncertain delivery by marker before resending. Persistent ledgers, crash-proof delivery, automatic recovery, versioned/regenerated plans, parallel coding, and general multi-repository/toolchain support are deferred.
