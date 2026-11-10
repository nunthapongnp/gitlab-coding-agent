> **Superseded historical design:** Use the [current Phase 0 manual workflow contract](../MANUAL_WORKFLOW_CONTRACT.md) and GitHub Issue #6 with its linked slices. Conflicting requirements below are inactive, including label-selected modes, plan versions, mandatory Docker, per-Task commits, partial publication, and the old runtime limit.

# P0-SPEC-003: M3 — fresh Docker runtime and role isolation

Status: ready-for-agent (local specification).

Dependencies: P0-SPEC-001.

## Problem Statement

Prompt restrictions and a workspace string do not enforce read-only planning/review or credential separation. Reused execution storage would also violate the agreed fresh-run behavior.

## Solution

Launch isolated Linux role workers and a trusted coordinator on one Docker host, using new operation storage and fresh contexts with separate shared execution locks.

## User Stories

1. As an operator, I want to launch through Docker from Windows or a POSIX host, so that the execution environment uses a consistent Linux contract.
2. As an operator, I want to give each operation distinct storage, so that a new attempt cannot reuse a previous workspace.
3. As a developer, I want to clone and explore the repository each Run, so that the agent reads current source instead of cached context.
4. As an operator, I want to keep GitLab credentials in the coordinator, so that coding tools cannot publish independently.
5. As a developer, I want to make Planner repository writes fail, so that read-only planning is enforced.
6. As a developer, I want to make Reviewer repository writes fail, so that review remains independent of implementation.
7. As an operator, I want to deny coding access to writable Git metadata, so that only the coordinator can commit and change refs.
8. As an operator, I want to start workers without the host Docker socket, so that agent tools do not manage the host daemon.
9. As an operator, I want to use supported remote worker connections, so that the existing SDK can run in role-specific containers.
10. As an operator, I want to provision the repository toolchain, so that missing host installations do not require unsafe mutation.
11. As an operator, I want to configure the reachable Ollama endpoint, so that container networking does not assume host localhost.
12. As an operator, I want to keep artifacts outside the checkout, so that logs and memory cannot leak into Product Changes.
13. As an operator, I want to reject simultaneous Issue or branch ownership, so that two attempts cannot conflict on the same work.
14. As an operator, I want to retain failed storage for inspection, so that unpublished commits and logs survive failure.
15. As an operator, I want to start new invocations with separate contexts, so that no persistent conversation is restored.
16. As an operator, I want to pin a tested SDK package set, so that runtime setup is reproducible.

## Implementation Decisions

- Use host-launched Compose services: coordinator, role-specific OpenHands workers, verification environment, and a dedicated builder when needed. Connect to separately started Agent Servers rather than nesting daemon-controlled workspaces.
- Coordinator owns API credentials and Git publication; Planner/Reviewer get read-only repository access and tools; Coder edits product files without GitLab credentials or writable Git metadata.
- Create new workspace/artifact volumes and new builder state per operation/Run. Retain available failed evidence and never auto-delete, reset, or resume it.
- Shared control storage holds active lock/ownership metadata only. Lock Issue and source branch in stable order; protect Plan publication using the Issue lock as well.
- Allow within-run repairs to retain working code, with fresh role contexts per invocation. Separate within-run copies for failed-change isolation are not previous-Run recovery.
- Validate worker mounts and role endpoint permissions at setup, not only prompts. Keep worker API credentials distinct from GitLab credentials.
- Select a compatible Python/OpenHands package set through a compatibility probe and lock it; verify imports, remote execution, and cancellation before claiming support.
- Honor configured model/provider values, normalize their prefix once, and document one tested combination rather than silently selecting a new model.
- Toolchains are repository-specific. Missing tools or unsupported builder setup produce explicit blocking results rather than host changes or automatic privileged fallback.

## Testing Decisions

- Use the workflow seam for allocation/lock outcomes and real Docker probes for mount behavior, token absence, metadata protection, worker connectivity, and fresh context/storage.
- Run competing invocations against the same shared control storage and prove rejection by externally observed ownership, not lock-helper internals.
- Verify failure retention and absence of cleanup/recovery side effects; do not claim mocks prove permission isolation.
- Prior art is timestamped clone preparation and SDK conversation construction. No Docker runtime/test suite exists; new probes must use the public launch/workflow boundary.

## Out of Scope

Native Windows process execution, Kubernetes/multiple hosts, persistent contexts, resume checkpoints, automatic cleanup, shared previous-Run caches, and host daemon access in coding tools.

## Further Notes

Implements ADR 0001 and ADR 0002. Host launchers wrap the existing application invocation rather than introducing another mode interface.

Source and ADR traceability: see the [spec index](README.md). This specification records accepted requirements, not completed implementation.

