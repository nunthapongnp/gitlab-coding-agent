---
status: accepted
---

# Separate coordination from agent work in Docker

Run the prototype on one Docker host using Compose, with a trusted coordinator and role-specific OpenHands workers sharing the current run's repository. The coordinator owns GitLab credentials and Git publication; Planner and Reviewer receive read-only repository access, while the Coding worker receives the working access needed for approved Tasks. This requires more runtime configuration than a single container but makes read-only roles and credential separation enforceable without exposing the host Docker daemon to coding tools.

## Consequences

- Host-side launch scripts create the run's containers and volumes; containers do not need the host Docker socket to manage their own lifecycle.
- Workers connect to separately started Agent Servers through the supported remote workspace interface, rather than spawning nested Docker workspaces.
- Verification runs in a worker environment with the required project toolchain. Dockerfile validation uses a dedicated builder when needed, with its requirements checked during runtime setup.
- Coding workers cannot mutate Git metadata or acquire GitLab publication credentials.
- Only one Coding Agent runs at a time in the prototype. Concurrent coders and their ownership coordination are deferred.
- Native Windows process execution is outside the initial runtime contract; Windows users launch Linux containers through Docker.

Implementation details: [Phase 0 implementation plan](../CODING_AGENT_PHASE0_IMPLEMENTATION_PLAN.md).
