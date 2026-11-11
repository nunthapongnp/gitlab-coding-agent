# GitLab coding agent

Phase 0 provides a manual workflow with explicit operator handoffs. Development targets `feature/phase-0`; the `main` branch retains the original POC.

Explore reads the Issue and its complete discussion, clones the Project default branch into a fresh checkout, and runs a read-only OpenHands planner through the configured LiteLLM Proxy. It validates and publishes one Execution Plan comment, then hands the Issue back for human review. Implement validates that agreement and the explicit human-created MR, prepares its source checkout, runs relevant baseline validation before one whole-Issue coder, and verifies its local Product Change. Independent review and delivery remain unavailable. See the [current contract](docs/p0/MANUAL_WORKFLOW_CONTRACT.md).

Use Python 3.12 or newer. Install dependencies in a local virtual environment, copy `.env.example` to `.env`, and set the required values:

```sh
python -m pip install -r requirements.txt
python main.py explore --project "group/project" --issue 12
python main.py implement --project "group/project" --issue 12 --mr 3
```

Both commands require a Project and positive Issue IID. Implement requires the existing user-created MR IID. Labels are preconditions/status and cannot select or trigger an operation.

Explore requires `agent::explore` without another recognized workflow/approval label and refuses an existing marked Execution Plan. Run directories default to the user's local state directory (`%LOCALAPPDATA%/gitlab-coding-agent/runs` on Windows). `--output-dir` must be outside Git checkouts. Each run has a fresh `checkout` and sibling `artifacts/inspection.json`; the CLI prints the diagnostic directory. Planner tools only list, read, search, and submit structured observations. They cannot execute proposed commands, edit source, or write GitLab records. Native local execution needs no Docker and does not provide an OS sandbox against hostile host code.

Explore replaces `agent::explore` with `agent::in-progress` after preflight and replaces it with `agent::needs-review` after confirmed publication. It preserves unrelated labels. Human approval consists of replacing `agent::needs-review` with `agent::approved`. Missing suggested commands are allowed; unresolved requirement questions require human clarification and never produce an executable plan.

GitLab holds the agreement; local inspection artifacts are diagnostic copies. The comment marker is `<!-- coding-agent:execution-plan -->` and its schema is `coding-agent.execution-plan/1` (a parser schema identity, not a plan version). `src.agreement.parse_plan_comment(body, project_id=..., issue_iid=...)` validates identity, inspected branch/commit, scope, checklist/acceptance mappings, verification expectations, optional commands with cwd/evidence, and matching readable/structured content. Re-explore never edits or supersedes an existing marked comment. To reset manually, delete old plan comments and clear obsolete recognized status/approval labels before requesting `agent::explore` again. After an uncertain write, the coordinator reads back once and never blindly posts a second plan; inspect GitLab before retrying if delivery remains uncertain.

Implement requires exactly one marked executable plan and only `agent::approved` on the Issue among recognized workflow labels. The explicit open MR must belong to the same Project, link to that Issue, carry only `agent::implement` among workflow labels, and have an existing source distinct from the planning baseline target. Draft and empty MRs are valid. A prior final agent Execution Report or stale in-progress state requires a new source branch/MR. Rejected preflight makes no writes or local checkout. Accepted runs get a unique ID and shared deadline, replace the MR request label with `agent::in-progress`, and clone its source into a fresh `implement-<run-id>` directory, preserving human history and MR metadata. `artifacts/run-context.json` records provenance; `OperationOutcome.run_context` exposes the validated agreement and prepared workspace to later capabilities. The current implementation returns exit code 4 because independent review and delivery are unavailable.

The coder receives the complete Issue and Approved Plan, scoped file tools and repository commands. Relevant new source/assets are allowed; configuration, dependency and schema edits need explicit approved coverage. Requirements changes, setup failures and host toolchain upgrades cause controlled stops. Git/GitLab publication tools and coordinator credentials are unavailable to the role. One run-local in-memory conversation can support later repairs and is closed before the application returns. `artifacts/coding-result.json` retains the candidate inventory, including untracked files, exact before/after content and content identity. Unexpected commits, index content or branch metadata cause rejection. Coding never commits or pushes these local changes. Verification evidence is collected separately; it does not grant review or publication approval.

Verification discovers commands per affected module from the Approved Plan, repository guidance, Node manifests, Maven descriptors, scripts/Make/configuration, CI corroboration, then explained repository analysis. It runs module commands with explicit argument vectors and native host wrappers, never triggers a CI job, and stops before coding when baseline checks fail or cannot be established. The operator must prepare compatible toolchains; dependency installation may be selected inside the workspace, while host upgrades are not permitted.

After coding, relevant builds/validation and existing or newly added tests execute again against the candidate identity. An absent suite or configured runner without case declarations is SKIPPED with source evidence. Real failures remain FAIL, execution blockers are BLOCKED, and missing mandatory validation is UNVERIFIED. Container changes require relevant configuration validation. `artifacts/checks/<phase>/report.json` and sibling command logs retain commands, cwd, evidence, exit codes, durations, skips, exclusions, and generated-file inventories. Baseline-generated output is excluded from the Product Change; generated verification artifacts are recorded for the later publication gate. These results remain local evidence for future review/repair/publication, and the application does not publish code.

Every normally completed or controlled-stopped accepted run uses one finalizer, including failed preparation. It attempts one MR report comment marked `<!-- coding-agent:execution-report run=<run-id> -->` and replaces `agent::in-progress` with `agent::needs-review`, preserving unrelated labels and Issue approval. `OperationOutcome.execution` and `artifacts/execution-result.json` expose verification, review, commit, push, report delivery, and label outcomes independently. `execution-report.md` retains the submitted report; `finalization.log` retains sanitized delivery errors. These diagnostics are outside the product checkout and cannot resume a run. A timeout triggers bounded marker readback without another POST. Each finalization request is clipped to the remaining run deadline; exhausted time can prevent the report or label handoff. Forced termination has no crash-proof finalization guarantee. Inspect local results and GitLab before a manual reset when delivery is uncertain.

`LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY` configure the team's LiteLLM Proxy. Supply the complete API root (for example `https://proxy.example/v1`) and its model alias. The role adapter applies `openai/` routing once; an alias already beginning with `openai/` is preserved. No API suffix is appended and there is no implicit model or endpoint default. `GITLAB_TOKEN` is required. Keep credentials in the local environment and out of source control.

`MAX_FIX_ATTEMPTS=3` allows three shared workflow repairs after initial coding. `EXECUTION_MAX_RUNTIME_SECONDS=3600` is the whole implement deadline. `OPENHANDS_MAX_ITERATIONS` limits each SDK invocation and `OPENHANDS_TIMEOUT_SECONDS` limits individual model requests; these are separate budgets.

The CLI and `Application.invoke` share structured outcomes. Exit codes: 0 successful requested work, 1 failed execution, 2 invalid invocation, 3 configuration error, 4 unavailable capability. `--help` displays usage without starting a workflow.

Tests use controlled services and require no live credentials:

```sh
python -m pytest -q
```
