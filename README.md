# GitLab → LangGraph → OpenHands → Ollama

## Environment

```.env

# Required
GITLAB_URL=https://gitlab.com
GITLAB_TOKEN=glpat-xxxx
GITLAB_GIT_USERNAME=agent-bot # Git HTTPS username

OLLAMA_BASE_URL=https://localhost:11434
OLLAMA_MODEL=ollama/llama3.2:3b
OLLAMA_API_KEY=sk-xxxx

# Optional
OPENHANDS_MAX_ITERATIONS=60 # OpenHands iterations per agent invocation
OPENHANDS_TIMEOUT_SECONDS=1800 # Per-invocation timeout

MAX_FIX_ATTEMPTS=3 # Additional fix attempts after an initial failure
CHECK_TIMEOUT_SECONDS=1200 # Verification command timeout
EXECUTION_MAX_RUNTIME_SECONDS=7200 # Whole Execute Run budget

SETUP_COMMAND= # Override install command
BUILD_COMMAND= # Override build command
TEST_COMMAND= # Override unit test command

GIT_AUTHOR_NAME=agent-bot
GIT_AUTHOR_EMAIL=ai@agent.bot

# Ticket 03: per-operation storage and Planner worker endpoints
WORKSPACE_ROOT=/workspaces        # per-operation workspace volume root (inside compose)
ARTIFACT_ROOT=/artifacts          # per-operation artifact root (inside compose)
CONTROL_ROOT=/control             # shared control volume (lock metadata; inside compose)
PLANNER_AGENT_SERVER_URL=         # separately started Planner Agent Server worker (required for Plan)
AGENT_SERVER_API_KEY=             # worker session key (X-Session-API-Key); never a GitLab credential
PLANNER_WORKSPACE_MOUNT=/workspace # workspace root as mounted inside the planner (read-only)
```

## Tested package set

The dependency lock is in `requirements.lock`. Tested, verified combination:

- Container images build on **python:3.12-slim** (OpenHands requires 3.12+);
  the planner Agent Server image was built and its server verified running by
  the runtime probes on Python 3.12.
- Host import and remote-workspace construction verification was performed on
  **CPython 3.14.7** (macOS arm64) against the installed packages below.
- `openhands-sdk==1.60.2`, `openhands-tools==1.60.2`,
  `openhands-agent-server==1.60.2`, `openhands-workspace==1.60.2` — verified
  by import and remote-workspace construction against the installed packages
  (see `requirements.lock` header). Do not bump the OpenHands set casually.
- Application/test deps are exact-pinned to the tested environment:
  `langgraph==1.2.20`, `requests==2.34.2`, `python-dotenv==1.2.4`, `pytest==9.2.0`

The configured `OLLAMA_MODEL` is normalized **once** at config load: stacked
provider prefixes are collapsed (`openai/ollama/x` → `ollama/x`), a bare
model name gets the default `ollama/` prefix, and invalid values fail with an
actionable error. Configured values are honored; no second prefix is ever
stacked downstream.

## Operator launch (Docker runtime)

The Docker runtime isolates roles per ADR 0002: the coordinator holds the
GitLab token; the planner worker is a **separately started** OpenHands Agent
Server that receives **no GitLab credentials and no host Docker socket**, and
mounts the operation workspace **read-only**.

POSIX:

```bash
scripts/launch.sh --project "my-group/my-project" --issue 12 [--branch release/1.4]
```

Windows PowerShell:

```powershell
scripts/launch.ps1 --project "my-group/my-project" --issue 12 [--branch release/1.4]
```

Both launchers do exactly the same thing:

1. Generate a unique `OPERATION_ID` (or reuse one you exported).
2. Start a **distinct Compose project** `gitlab-agent-${OPERATION_ID}` —
   per-operation workspace volume, fresh SDK context, distinct artifact
   directory. Two launches never share operation storage or contexts.
3. Forward **all CLI arguments unchanged** to the same `python main.py …`
   invocation inside the coordinator container.
4. Keep artifacts on the host at
   `${AGENT_ARTIFACT_DIR:-~/.gitlab-coding-agent/artifacts}/<operation-id>`
   — outside the product checkout.

### Credential isolation summary

| Credential | Coordinator | Planner worker |
|---|---|---|
| `GITLAB_TOKEN` | yes | **never** |
| Host Docker socket | no | **never** |
| Agent Server session key (`AGENT_SERVER_API_KEY`) | holds; sends as `X-Session-API-Key` | verifies (its own worker credential) |
| Model endpoint (`OLLAMA_*`) | builds the LLM config | calls the model (read toolset only) |

The planner workspace mount is read-only; repository writes fail at the
filesystem level (probe-verified, see `tests/test_runtime_probes.py`).

### Retention and cleanup policy (manual — never automatic)

Nothing is deleted, reset, or restored automatically — not on success, not
on failure. A failed launch retains its compose project, workspace volume,
and `AGENT_ARTIFACT_DIR/<operation-id>` logs/evidence for inspection. The
launchers never run `docker compose down --volumes` and never remove volumes.

Explicit operator cleanup for a finished launch you no longer need:

```bash
docker compose -p gitlab-agent-<operation-id> down      # containers only; volumes are kept
docker volume rm gitlab-agent-<operation-id>_workspace  # only if you want that workspace gone
```

The shared control volume `gitlab-agent-control` (Issue/branch lock metadata)
persists across all launches and is never deleted; lock records are retained
with release markers.

## Runtime probes (opt-in)

```bash
pytest -m runtime_probe -q   # requires a running Docker daemon
pytest -q                    # offline suite; probes are deselected by default
```

## Run outcomes and exit codes

A Plan run publishes **one** complete plan comment on the Issue and moves its
labels `agent::plan` → `agent::in-progress` → `agent::needs-review` (unrelated
labels are preserved). The console report ends with a `=== Plan publication ===`
section:

```
=== Plan publication ===
Plan    : PLAN-v1 (version 1)
Note    : #12
Publication UID: op-20261109T…
Outcome : EXECUTABLE — ready for human review
Unresolved questions: 0
Labels  : transitioned_to_needs_review
```

- **EXECUTABLE** — the plan has no unresolved questions; it is ready for human
  review and later `agent::approved` execution.
- **CLARIFICATION REQUIRED — NOT executable** — the planner recorded unresolved
  questions (e.g. conflicting requirements). The plan comment is still
  published for review, but it must be answered and regenerated (new
  `PLAN-vN` version) before it can be approved.

`poc-result.json` carries the same distinction structurally in
`plan_publication.executable`, plus an `exit_code_semantics` map. Exit codes:

| Code | Meaning |
|---|---|
| `0` | Workflow completed; a plan was published (executable **or** clarification-required — distinguished by `plan_publication.executable` and the console marking) |
| `1` | Infrastructure failure: inspection/synthesis/publication failed or blocked (`inspection_failed`, `inspection_blocked`, `plan_synthesis_failed`, `publication_failed`); nothing was published |
| `2` | Invalid invocation, concurrent operation lock, or routing/state rejection (`rejected`, `operation_locked`); nothing was edited or published |

Publication is deduplicated by publication UID: a re-run that lost the POST
response re-scans the Issue comments and never posts a duplicate plan version.

## Run on local
```bash
python -m venv .venv

# activate visual env
source .venv/bin/activate # linux
.venv/Scripts/activate.bat # windows

python -m pip install -r requirements.txt

# run script
python main.py --project "my-group/my-project" --issue 12

python main.py --project "echo6604438/my-project" --issue 1
```
