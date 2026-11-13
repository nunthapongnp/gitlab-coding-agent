# Phase 0 walkthrough evidence

The opt-in live walkthrough against `echo6604438/my-project` Issue #2 has **NOT BEEN PERFORMED**. No demo Product Change, live GitLab/model write, pipeline, deployment, baseline legacy build/test or human UI acceptance is claimed here. Read-only snapshots from 2026-11-11 informed the [operator guide](OPERATOR_GUIDE.md); the operator must inspect the current baseline and confirm old automatic CI is disabled before a live run.

## Deterministic framework walkthrough

`tests/test_manual_walkthrough.py` uses the same `Application.invoke` operations as `main.py`. GitLab HTTP responses, planner/coder/reviewer output and the clock are controlled. Fresh clones, a local bare remote, original human history, aggregate commit, normal push and short Python verification child processes are real. The fixture changes a greeting, not the TaskFlow demo.

The journey requests explore, publishes one plan and hands the Issue back for human approval. The fixture removes the entire explore directory under a resolved temporary-directory guard, then simulates human approval and creation of a new source branch/empty draft MR. A new Application/runtime reads the agreement from GitLab, runs baseline/code/check/review, delivers one commit and produces one final report and MR review label. Human MR metadata and unrelated labels are preserved; review/merge remain human work.

The no-case harness reports an evidenced SKIPPED result. Two additional journeys run an existing real test that passes at baseline and fails for the first candidate: one repairs and freshly checks/reviews before delivery; the other exhausts its budget, retains FAIL logs and local edits, reports the failure and leaves the remote unchanged.

Run from the framework root after installing declared requirements:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_manual_walkthrough.py --junitxml "$env:TEMP\gitlab-agent-issue17-walkthrough.xml"
```

Validation on 2026-11-12 used the installed framework virtual environment at `D:\Workspaces\projects\gitlab-coding-agent\.venv\Scripts\python.exe`, with cwd `D:\Workspaces\projects\gitlab-agent-issue17`. Use your prepared checkout's virtual environment for the portable command above; an unrelated global Python may lack the declared SDK dependencies.

| Actual check | Result | Retained evidence |
| --- | --- | --- |
| Happy journey alone, `-m pytest -q tests/test_manual_walkthrough.py::test_operator_journey_delivers_one_commit_after_gitlab_only_handoff -x --tb=short` | 1 passed in 78.55 seconds | `%TEMP%\gitlab-agent-issue6\issue17-happy.xml` |
| `-m pytest -q tests/test_manual_walkthrough.py::test_real_relevant_failure_is_never_reported_as_absent_tests tests/test_invocation.py tests/test_generic_configuration.py -x --tb=short` | 25 passed in 124.55 seconds | `%TEMP%\gitlab-agent-issue6\issue17-repair-config.xml` |
| `main.py explore --help` and `main.py implement --help` | Both exit 0; exact guide flags confirmed | CLI output inspected |
| Active document local links, fixed demo command parsing, removed unsupported environment hints | PASS | Local source/config inspection |

The JUnit destinations are outside pytest's cache and product repositories. No entire framework suite was rerun for these documentation/walkthrough additions. Controlled model output proves orchestration, not live model quality; short fixture checks do not prove Angular/Spring compatibility or the demo's functional acceptance.

Both pytest commands emitted 13 upstream `langsmith` deprecation warnings about `asyncio.iscoroutinefunction`; no test failed. The three new walkthrough cases all passed. Expected fixture FAIL/EXHAUSTED outcomes are asserted behavior, not hidden suite failures.
