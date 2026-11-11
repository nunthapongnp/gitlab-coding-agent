# GitLab coding agent

Phase 0 provides a manual workflow with explicit operator handoffs. Development targets `feature/phase-0`; the `main` branch retains the original POC.

Explore reads the Issue and its complete discussion, clones the Project default branch into a fresh checkout, and runs a read-only OpenHands planner through the configured LiteLLM Proxy. It validates and publishes one Execution Plan comment, then hands the Issue back for human review. Implement remains unavailable. See the [current contract](docs/p0/MANUAL_WORKFLOW_CONTRACT.md).

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

`LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY` configure the team's LiteLLM Proxy. Supply the complete API root (for example `https://proxy.example/v1`) and its model alias. The role adapter applies `openai/` routing once; an alias already beginning with `openai/` is preserved. No API suffix is appended and there is no implicit model or endpoint default. `GITLAB_TOKEN` is required. Keep credentials in the local environment and out of source control.

`MAX_FIX_ATTEMPTS=3` allows three shared workflow repairs after initial coding. `EXECUTION_MAX_RUNTIME_SECONDS=3600` is the whole implement deadline. `OPENHANDS_MAX_ITERATIONS` limits each SDK invocation and `OPENHANDS_TIMEOUT_SECONDS` limits individual model requests; these are separate budgets.

The CLI and `Application.invoke` share structured outcomes. Exit codes: 0 successful requested work, 1 failed execution, 2 invalid invocation, 3 configuration error, 4 unavailable capability. `--help` displays usage without starting a workflow.

Tests use controlled services and require no live credentials:

```sh
python -m pytest -q
```
