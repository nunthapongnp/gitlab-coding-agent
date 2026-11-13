# Operator guide: Phase 0 manual pilot

This guide describes the manual local workflow on `feature/phase-0`. **Opt-in live walkthrough: NOT PERFORMED.** The demo observations below are read-only source evidence from 2026-11-11, not passing build/test results.

## Prepare the host

Use a framework checkout of `feature/phase-0`, Python 3.12 or newer, Git, and writable run storage outside all Git checkouts. LocalRuntime requires no Docker. From the framework root, using PowerShell, prepare a new environment if needed:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create an untracked `.env` there or set process environment variables. Replace angle-bracket placeholders with actual team configuration:

```dotenv
GITLAB_URL=https://gitlab.com
GITLAB_TOKEN=<operator-provided token>
GITLAB_GIT_USERNAME=agent-bot
LLM_BASE_URL=<complete team proxy API root>
LLM_MODEL=<actual configured proxy alias>
LLM_API_KEY=<operator-provided proxy key>
GIT_AUTHOR_NAME=<intended commit author>
GIT_AUTHOR_EMAIL=<intended commit email>
OPENHANDS_MAX_ITERATIONS=35
OPENHANDS_TIMEOUT_SECONDS=1800
MAX_FIX_ATTEMPTS=3
EXECUTION_MAX_RUNTIME_SECONDS=3600
CHECK_TIMEOUT_SECONDS=1200
```

These placeholders are not working defaults. Set the appropriate HTTPS Git username if `agent-bot` is unsuitable. The token/account must permit project/Issue/MR reads, comment and label writes, clone, and push to the selected source branch. Keep credentials out of remote URLs, Issue/plan text and product files.

`LLM_BASE_URL` is the complete LiteLLM Proxy API root, with a version suffix only if the actual proxy uses it. The adapter does not append `/v1`. `LLM_MODEL` is the proxy's configured alias: routing adds `openai/` once and preserves an alias already beginning with it. There is no implicit model, endpoint or key. All roles initially use that same model with independent contexts. Do not invent an alias or deploy/configure the proxy for this pilot.

Three shared repairs follow initial coding, for at most four coding attempts across build/test/review failures. The accepted-run 3600-second deadline covers preparation through normal finalization, reserving handoff time. SDK iteration/request limits are separate.

Repository commands come from the agreement and discovery; legacy `SETUP_COMMAND`, `BUILD_COMMAND` and `TEST_COMMAND` environment fields are not command overrides. Discovery's exact evidence order, per module/check, is: Approved Plan; repository readme/agent/contribution guidance; Node/Angular manifests; Maven descriptors; scripts/Make/configuration; GitLab CI as corroboration; explained repository analysis. Record cwd/module/provenance; a suggested command is not a passing check.

## Prepare the selected demo

Use [echo6604438/my-project Issue #2](https://gitlab.com/echo6604438/my-project/-/work_items/2): show each task row's existing API ID as `TASK-<id>`. Exclude pagination Issue #1. Preserve backend/API/schema/dependencies/CI, search/filter/reset, row navigation and empty state.

Read-only snapshots on 2026-11-11 showed Angular 11.2.x, Spring Boot 2.2.6, Java 8 and Node 12.22.12 in the frontend Dockerfile. These observations are not current compatibility guarantees or build/test results. Inspect the live manifests/lockfiles again and prepare compatible local Node/npm, JDK/Maven and any browser needed by actual tests. Stop on incompatibility rather than upgrading host tools or demo dependencies silently.

Before the first live command, the operator confirms the demo's old automatic agent CI is disabled. The inspected `.gitlab-ci.yml` was entirely commented, including its old agent include; this does not prove current automation settings. The operator owns any disabling needed. Framework code and ordinary tests must not edit CI or trigger a pipeline.

Establish the actual baseline in a separate disposable demo checkout. Do not use a personal checkout as the agent workspace. Dependency caches, build output and local SQLite test data require writable locations. Inspect current backend test/configuration paths and service prerequisites before running them; use disposable local data where needed. Each agent invocation creates a fresh checkout, so separately installed `node_modules` is not reused automatically. Repository dependency installation is allowed through discovered setup commands; host upgrades are not.

Confirm these candidate commands against the current checkout and affected modules, then record actual commands/cwd, tool versions, exit codes and logs:

| Purpose | Demo directory | Candidate command and evidence |
| --- | --- | --- |
| Frontend setup | `frontend` | `npm install --no-audit --no-fund` (inspected Dockerfile; README also has `npm install`) |
| Issue #2 production build | `frontend` | `npm run build -- --configuration production` (Dockerfile/CI; package script is `ng build`) |
| Frontend tests if cases exist | `frontend` | `npm test` (script runs `ng test --watch=false --browsers=ChromeHeadless`; needs a suitable local browser) |
| Backend build if relevant | `backend` | `mvn -B -DskipTests package` (current verifier's Maven-derived command; use repository wrapper if present) |
| Backend tests if relevant | `backend` | `mvn -B test` (Maven-derived; inspected CI corroborates `mvn -q test`) |

Recheck test sources: inspection found a frontend harness without `*.spec.ts` cases and two backend test files. No meaningful frontend cases supports SKIPPED with a reason, never PASS. Backend files do not prove tests pass. A real relevant test failure stays FAIL; browser/service/toolchain failure is BLOCKED or FAIL, not an absent-suite skip. A frontend-only change may exclude unrelated backend checks with an explicit reason; reconsider actual dependencies. New focused tests are optional; any added tests participate in verification. Existing baseline failures or unavailable mandatory validation stop before coding without unrelated repair.

## Explore, review and approve

In GitLab place `agent::explore` on Issue #2 with no other recognized agent label. Preserve unrelated labels. Recognized labels are `agent::explore`, `agent::implement`, `agent::in-progress`, `agent::needs-review`, `agent::approved`; contradictions reject invocation. Labels never automatically start work.

From the configured framework root:

```powershell
.\.venv\Scripts\python.exe main.py explore --project echo6604438/my-project --issue 2
```

Both commands also accept `--gitlab-url URL` and `--output-dir PATH`. Windows run storage defaults to `%LOCALAPPDATA%\gitlab-coding-agent\runs`; overrides must be outside Git checkouts. Keep the diagnostic path printed by the CLI.

Explore replaces Issue `agent::explore` with `agent::in-progress`, then `agent::needs-review` after confirmed publication of one complete plan comment. Inspect its readable/structured agreement, frontend scope, acceptance mapping and production-build expectation. Missing command suggestions alone need not block a plan; unresolved requirements do. Human approval replaces Issue `agent::needs-review` with `agent::approved`; the application never approves.

GitLab is the durable agreement. Explore files are diagnostics; implement must work without them. The plan is unversioned. Changing the requirements/agreement requires the reset below.

## Create the branch/MR and implement

The human creates a new source branch from the plan's baseline branch and an open draft MR in the same demo project. Target must match that baseline (observed default: `main`), and source must differ. Empty draft MRs are supported. Link to Issue #2 through GitLab's relationship or an exact closing-reference line such as `Closes #2` in the description. Retain your chosen title/description. Put only `agent::implement` among recognized labels on the MR; keep Issue `agent::approved`.

Replace the placeholder with the new MR's positive project-local IID, not its global identifier, before running:

```powershell
.\.venv\Scripts\python.exe main.py implement --project echo6604438/my-project --issue 2 --mr <HUMAN_CREATED_MR_IID>
```

Implement validates the Issue, single executable plan, approval and explicit MR. After acceptance it changes MR status to `agent::in-progress`, clones its source into a fresh directory and records the starting commit. Relevant baseline validation precedes one whole-Issue coder. Checks and a fresh independent read-only review follow. Repairs retain only that run's workspace/coder context and invalidate old checks/review. Scope changes cause controlled stop and human reset.

Publication requires current mandatory verification, justified tests, independent approval and remaining budget. The coordinator stages exactly the reviewed Product Change, creates one aggregate commit and normally pushes to the existing MR source. It preserves starting history and refuses an intervening remote change, unexpected index/history, unreviewed late content or empty change. No automatic branch/MR creation, force push, merge or partial publication occurs.

## Interpret results and finish manually

Normal completion/controlled stop attempts one final MR Execution Report and replaces MR `agent::in-progress` with `agent::needs-review`, preserving Issue approval and unrelated labels. Process/API failure can leave stale state; inspect GitLab and diagnostics.

CLI exit codes: 0 success, 1 execution failure, 2 invalid invocation, 3 configuration error, 4 unavailable capability. `needs-review` is a handoff, not success proof. Inspect actual build/test evidence, acceptance coverage, skips/blockers, review/repairs, commit and push independently. PASS, FAIL, SKIPPED, BLOCKED and UNVERIFIED are distinct. Local commit success is not remote delivery.

`artifacts/execution-result.json` records final commit, push, report-delivery and label outcomes separately. The remote report is an as-of-submission snapshot and may show PENDING delivery fields. Confirmed push with failed report/label handoff remains delivered code, even if CLI fails. `execution-report.md`, `finalization.log`, `run-context.json`, attempt/review/check artifacts retain evidence outside product source. Inspect uncertain writes by run marker; do not blindly rerun/repost. Diagnostics are not resumable state.

The human starts the demo in their prepared local environment and checks before merge:

- Every loaded Tasks row shows its actual `TASK-<id>` next to/below the title.
- Opening a row shows the same ID on its detail page; navigation still works.
- Search/filter/reset retain correct IDs on visible rows.
- No-results search preserves the empty state without a fictitious identifier.
- The production build actually passed, tests have truthful outcomes, and remaining manual acceptance is explicit.

The inspected README's `npm start` and `mvn spring-boot:run` are development-server commands, not mandatory source validation. Its local backend example uses writable `APP_DATA_DIR=./data`; frontend `/api` needs prepared local routing/proxy. Confirm current runtime configuration and use disposable local data. A separately chosen human Docker Compose demo environment is not required for LocalRuntime or proof of agent verification. Human review/merge remain manual.

## Human reset paths

Changed requirements/plan: create a new Issue, or remove all comments from the old Issue, clear recognized workflow/approval labels and request `agent::explore` again. Review/approve the new single agreement. Preserve unrelated labels; do not leave a second old plan.

Another implement attempt: inspect delivery/diagnostics, delete the old MR and source branch, then create a new branch/MR. Reuse an unchanged Approved Plan only with that new MR. Reset the Issue first if requirements changed. Clearing a prior report or stale in-progress label is not permission to reuse the old MR. The application performs no destructive reset, resume, replan or automatic cleanup.

## Evidence and limitations

LocalRuntime separates fresh directories and role capabilities. Planner/reviewer inspect read-only; coder edits approved work and runs local commands; coordinator owns GitLab writes/commit/push. These are application capability restrictions, not an OS sandbox against hostile code. DockerRuntime/custom toolchain images remain future extension points; no container isolation is implemented.

The separate deterministic walkthrough in `tests/test_manual_walkthrough.py` invokes the CLI's `Application.invoke` with controlled GitLab HTTP/model transports, a controlled clock, real temporary Git repositories/bare remotes and real short Python child checks. Its toy greeting requirement is unrelated to the demo Product Change. It removes all explore files before creating a new Application/runtime for implement, checks label replacements and preserved human metadata/history, and verifies one aggregate commit/normal push/report. Additional cases exercise an actual failing relevant test, repair and terminal non-publication with retained diagnostics. GitLab responses and role output are simulated; Git history, delivery and child exit evidence are real. No live GitLab/model writes, demo changes, legacy builds/tests, pipelines, or human UI acceptance are implied. See [walkthrough evidence](WALKTHROUGH_EVIDENCE.md) for commands actually executed.
