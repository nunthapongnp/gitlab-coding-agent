# GitLab coding agent

Phase 0 provides a manual workflow with explicit operator handoffs. Development targets `feature/phase-0`; the `main` branch retains the original POC.

The invocation/configuration foundation is implemented. Both operations currently stop with `unavailable` before accessing GitLab, cloning, running a model, or publishing. Later slices enable the [current contract](docs/p0/MANUAL_WORKFLOW_CONTRACT.md).

Install dependencies in a local virtual environment, copy `.env.example` to `.env`, and set the required values:

```sh
python -m pip install -r requirements.txt
python main.py explore --project "group/project" --issue 12
python main.py implement --project "group/project" --issue 12 --mr 3
```

Both commands require a Project and positive Issue IID. Implement requires the existing user-created MR IID. Labels are preconditions/status and cannot select or trigger an operation.

`LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY` configure the team's LiteLLM Proxy. Supply the complete API root (for example `https://proxy.example/v1`) and its model alias. The role adapter applies `openai/` routing once; an alias already beginning with `openai/` is preserved. No API suffix is appended and there is no implicit model or endpoint default. `GITLAB_TOKEN` is required. Keep credentials in the local environment and out of source control.

`MAX_FIX_ATTEMPTS=3` allows three shared workflow repairs after initial coding. `EXECUTION_MAX_RUNTIME_SECONDS=3600` is the whole implement deadline. `OPENHANDS_MAX_ITERATIONS` limits each SDK invocation and `OPENHANDS_TIMEOUT_SECONDS` limits individual model requests; these are separate budgets.

The CLI and `Application.invoke` share structured outcomes. Exit codes: 0 successful requested work, 1 failed execution, 2 invalid invocation, 3 configuration error, 4 unavailable capability. `--help` displays usage without starting a workflow.

Tests use controlled services and require no live credentials:

```sh
python -m pytest -q
```
