#!/usr/bin/env bash
# Host launcher for the coding agent (POSIX; POSIX and Windows operators are
# both supported via scripts/launch.ps1). Both wrap THE SAME application
# invocation: `python main.py <args>` inside the coordinator container.
#
# Per launch (P0-SPEC-003 §4.2):
#   - a unique OPERATION_ID is generated;
#   - a distinct Compose project (and therefore distinct per-operation
#     workspace volume) is started;
#   - CLI arguments are forwarded unchanged to python main.py;
#   - artifacts are kept OUTSIDE the product checkout.
#
# Retention contract: nothing is ever cleaned up automatically. Neither on
# success nor on failure does this script run `docker compose down --volumes`
# or delete/reset volumes, workspaces, or logs. Failed-run evidence is retained
# for operator inspection. Successful-run cleanup is an explicit operator
# action (commands are printed below; see README).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Unique operation identifier for this launch.
if [ -z "${OPERATION_ID:-}" ]; then
    OPERATION_ID="op-$(date -u +%Y%m%dT%H%M%SZ)-$(uuidgen | tr 'A-Z' 'a-z' | cut -c1-8)"
fi
export OPERATION_ID

# Distinct Compose project per launch -> distinct per-operation volumes.
COMPOSE_PROJECT_NAME="gitlab-agent-${OPERATION_ID}"
export COMPOSE_PROJECT_NAME

# Artifacts live outside the product checkout by default.
AGENT_ARTIFACT_DIR="${AGENT_ARTIFACT_DIR:-${HOME}/.gitlab-coding-agent/artifacts}"
export AGENT_ARTIFACT_DIR
mkdir -p "${AGENT_ARTIFACT_DIR}"

# The shared control volume (Issue/branch lock metadata) is shared across all
# launches on this host; create it once if missing. It is never deleted here.
docker volume create gitlab-agent-control >/dev/null

echo "Operation        : ${OPERATION_ID}"
echo "Compose project  : ${COMPOSE_PROJECT_NAME}"
echo "Artifacts        : ${AGENT_ARTIFACT_DIR} (retained on failure; cleanup is manual)"

cd "${REPO_ROOT}"

set +e
# Forward ALL CLI arguments to the same `python main.py` invocation.
docker compose run --rm coordinator "$@"
status=$?
set -e

if [ ${status} -ne 0 ]; then
    echo
    echo "Launch FAILED (exit ${status}). NO cleanup was performed; evidence is retained for inspection:" >&2
    echo "  compose project : ${COMPOSE_PROJECT_NAME} (docker compose -p ${COMPOSE_PROJECT_NAME} ps -a)" >&2
    echo "  workspace volume: ${COMPOSE_PROJECT_NAME}_workspace" >&2
    echo "  artifacts/logs  : ${AGENT_ARTIFACT_DIR}/${OPERATION_ID}" >&2
    echo "  control volume  : gitlab-agent-control" >&2
    echo "Manual cleanup (explicit operator action; never automatic):" >&2
    echo "  docker compose -p ${COMPOSE_PROJECT_NAME} down    # containers only; volumes are kept" >&2
    echo "  docker volume rm ${COMPOSE_PROJECT_NAME}_workspace  # ONLY if you want the workspace gone" >&2
else
    echo
    echo "Launch finished (exit 0). Storage is retained; cleanup is an explicit operator action:" >&2
    echo "  docker compose -p ${COMPOSE_PROJECT_NAME} down && docker volume rm ${COMPOSE_PROJECT_NAME}_workspace" >&2
fi

exit ${status}
