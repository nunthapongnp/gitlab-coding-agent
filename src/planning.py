"""Execution Plan model, validation, versioning, and template-02 rendering.

Ticket 04 WP-B (P0-SPEC-004; implementation plan §5.1, §5.3, §6, §12).

Design invariants (P0-SPEC-004 / ADR 0003):

- LLM text is never trusted as workflow state: every published plan passed
  ``validate_plan``, and the Markdown + JSON comment are rendered from the
  SAME validated object (JSON round-trips through ``validate_plan``).
- ``modules`` describe SEMANTIC scope. They are not ``allowed_paths``; no
  file allowlist is required or modeled anywhere in this module (ADR 0003).
- Validation hints carry ``origin`` (DETECTED/PROPOSED) and are UNVERIFIED by
  construction. Plan Mode never executes Setup/Build/Test; nothing in this
  module runs commands.
- A malformed or invalid LATEST plan record is a typed error, never a silent
  fallback to an older version (P0-SPEC-004 "Implementation Decisions").
- Plan/Task identity follows §5.3: ``PLAN-v1``... per Issue, ``TASK-001``...
  within one plan version (version-local identities, no cross-version
  matching).

This module is transport-independent: no GitLab client, no network, no I/O
side effects. The workflow layer (WP-C) owns note transport and locking; the
planner transport (WP-C) owns the LLM call and passes its content through
``build_plan`` — the deterministic assembly + validation seam defined here.

Plan-comment recognition convention (versioned marker):

    <!-- gca-execution-plan:v1 version=1 plan_id=PLAN-v1 project_id=42
         issue_iid=123 publication_uid=... executable=true -->
    <details>
    <summary>Execution Plan metadata — PLAN-v1 (machine-readable JSON;
    do not edit)</summary>

    ```json
    { ...the full validated plan payload... }
    ```

    </details>

The marker line carries the record identity in machine-readable attributes
so version/identity checks survive even when the fenced JSON payload was
hand-mangled; the fenced JSON is the authoritative payload when it parses.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Constants (§5.1 payload shape, §5.3 identity conventions, ADR 0003 scope)
# ---------------------------------------------------------------------------

PLAN_SCHEMA_VERSION = 1
"""Only schema version this prototype understands."""

PLAN_KIND = "execution_plan"

PLAN_MARKER = "gca-execution-plan:v1"
"""Versioned marker recognizing published plan comments."""

TASK_STATUS_PENDING = "PENDING"
TASK_STATUSES = frozenset({"PENDING", "COMPLETED", "PARTIAL"})

HINT_KINDS = frozenset({"BUILD", "TEST"})
HINT_ORIGINS = frozenset({"DETECTED", "PROPOSED"})
HINT_CONFIRMATIONS = frozenset({"UNVERIFIED"})

TASK_ID_RE = re.compile(r"^TASK-\d{3,}$")

MARKER_ATTRS_RE = re.compile(
    r"<!--\s*" + re.escape(PLAN_MARKER) + r"\s+(?P<attrs>[^>]*?)-->"
)
_MARKER_ATTR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s>]+)")
_JSON_FENCE_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PlanValidationError(ValueError):
    """A plan violates the §5.1 contract.

    Carries ALL collected violations (not just the first) so a rejected
    plan can be repaired in one pass. ``violations`` are human-readable
    strings with field paths (e.g. ``tasks[1].modules``).
    """

    def __init__(self, violations: list[str]):
        self.violations = list(violations)
        super().__init__(
            "execution plan validation failed: " + "; ".join(self.violations)
        )


class PlanRecordError(RuntimeError):
    """A published plan record in a note is malformed or invalid.

    Raised instead of silently falling back to an older plan version
    (P0-SPEC-004). ``note_ref`` identifies the offending note, ``violations``
    the collected validation problems when the payload parsed but was
    invalid.
    """

    def __init__(self, message: str, *, note_ref: str | None = None,
                 violations: list[str] | None = None):
        self.note_ref = note_ref
        self.violations = list(violations or [])
        super().__init__(message)


# ---------------------------------------------------------------------------
# Typed structures (§5.1 JSON shape)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanBaseline:
    branch: str
    sha: str


@dataclass(frozen=True)
class PlanImpact:
    module: str
    required: bool
    reason: str


@dataclass(frozen=True)
class PlanTask:
    id: str
    title: str
    scope: str
    modules: list[str]
    dependencies: list[str]
    acceptance: list[str]
    verification: list[str] = field(default_factory=list)
    configuration_changes: list[str] = field(default_factory=list)
    database_file_changes: list[str] = field(default_factory=list)
    status: str = TASK_STATUS_PENDING


@dataclass(frozen=True)
class PlanValidationHint:
    module: str
    kind: str
    command: str
    cwd: str
    origin: str
    confirmation: str = "UNVERIFIED"


@dataclass(frozen=True)
class ExecutionPlan:
    """One complete plan version (§5.1). Immutable once validated."""

    schema_version: int
    kind: str
    publication_uid: str
    project_id: int | str
    project_path: str
    issue_iid: int | str
    version: int
    plan_id: str
    generated_at: str
    baseline: PlanBaseline
    executable: bool
    unresolved_questions: list[str]
    requirement_understanding: str
    impact: list[PlanImpact]
    tasks: list[PlanTask]
    validation_hints: list[PlanValidationHint]

    def to_dict(self) -> dict:
        """The §5.1 JSON-serializable payload shape."""
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "publication_uid": self.publication_uid,
            "project_id": self.project_id,
            "project_path": self.project_path,
            "issue_iid": self.issue_iid,
            "version": self.version,
            "plan_id": self.plan_id,
            "generated_at": self.generated_at,
            "baseline": {"branch": self.baseline.branch, "sha": self.baseline.sha},
            "executable": self.executable,
            "unresolved_questions": list(self.unresolved_questions),
            "requirement_understanding": self.requirement_understanding,
            "impact": [
                {"module": i.module, "required": i.required, "reason": i.reason}
                for i in self.impact
            ],
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "scope": t.scope,
                    "modules": list(t.modules),
                    "dependencies": list(t.dependencies),
                    "acceptance": list(t.acceptance),
                    "verification": list(t.verification),
                    "configuration_changes": list(t.configuration_changes),
                    "database_file_changes": list(t.database_file_changes),
                    "status": t.status,
                }
                for t in self.tasks
            ],
            "validation_hints": [
                {
                    "module": h.module,
                    "kind": h.kind,
                    "command": h.command,
                    "cwd": h.cwd,
                    "origin": h.origin,
                    "confirmation": h.confirmation,
                }
                for h in self.validation_hints
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExecutionPlan:
        """Rebuild from a §5.1 payload. Call only after ``validate_plan``;
        unknown extra keys in nested objects are ignored."""
        return cls(
            schema_version=data["schema_version"],
            kind=data["kind"],
            publication_uid=data["publication_uid"],
            project_id=data["project_id"],
            project_path=data["project_path"],
            issue_iid=data["issue_iid"],
            version=data["version"],
            plan_id=data["plan_id"],
            generated_at=data["generated_at"],
            baseline=PlanBaseline(
                branch=data["baseline"]["branch"], sha=data["baseline"]["sha"]
            ),
            executable=data["executable"],
            unresolved_questions=list(data["unresolved_questions"]),
            requirement_understanding=data["requirement_understanding"],
            impact=[
                PlanImpact(
                    module=i["module"], required=i["required"], reason=i["reason"]
                )
                for i in data["impact"]
            ],
            tasks=[
                PlanTask(
                    id=t["id"],
                    title=t["title"],
                    scope=t["scope"],
                    modules=list(t["modules"]),
                    dependencies=list(t["dependencies"]),
                    acceptance=list(t["acceptance"]),
                    verification=list(t.get("verification", [])),
                    configuration_changes=list(t.get("configuration_changes", [])),
                    database_file_changes=list(t.get("database_file_changes", [])),
                    status=t.get("status", TASK_STATUS_PENDING),
                )
                for t in data["tasks"]
            ],
            validation_hints=[
                PlanValidationHint(
                    module=h["module"],
                    kind=h["kind"],
                    command=h["command"],
                    cwd=h.get("cwd", "."),
                    origin=h["origin"],
                    confirmation=h.get("confirmation", "UNVERIFIED"),
                )
                for h in data.get("validation_hints", [])
            ],
        )


def plan_to_dict(plan: dict | ExecutionPlan) -> dict:
    """Normalize a plan argument (payload dict or ExecutionPlan) to a dict."""
    if isinstance(plan, ExecutionPlan):
        return plan.to_dict()
    if isinstance(plan, dict):
        return plan
    raise PlanValidationError(
        [f"plan must be a dict or ExecutionPlan, got {type(plan).__name__}"]
    )


# ---------------------------------------------------------------------------
# Validation (§5.1 validation rules; all violations collected)
# ---------------------------------------------------------------------------


def _identity_matches(expected, actual) -> bool:
    """Identity comparison tolerant to int/str representations."""
    if expected is None or actual is None:
        return False
    return str(expected) == str(actual)


def _is_nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_iso8601_utc(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    offset = parsed.utcoffset()
    return parsed.tzinfo is not None and offset == timedelta(0)


def _check_list_of_nonempty_str(violations: list[str], where: str, value,
                                *, allow_empty: bool = True) -> None:
    if not isinstance(value, list):
        violations.append(f"{where} must be a list, got {type(value).__name__}")
        return
    if not allow_empty and not value:
        violations.append(f"{where} must not be empty")
    for index, item in enumerate(value):
        if not _is_nonempty_str(item):
            violations.append(f"{where}[{index}] must be a nonempty string")


def _validate_plan_payload(
    data: dict, *, project_id, issue_iid
) -> list[str]:
    """Collect all §5.1 violations for one plan payload."""
    violations: list[str] = []
    if not isinstance(data, dict):
        return [f"plan must be a JSON object, got {type(data).__name__}"]

    # Schema / kind.
    if data.get("schema_version") != PLAN_SCHEMA_VERSION or not _is_int(
        data.get("schema_version")
    ):
        violations.append(
            "schema_version: unsupported value "
            f"{data.get('schema_version')!r} (supported: {PLAN_SCHEMA_VERSION})"
        )
    if data.get("kind") != PLAN_KIND:
        violations.append(
            f"kind: expected {PLAN_KIND!r}, got {data.get('kind')!r}"
        )

    # Identity: matching Project/Issue.
    if not _identity_matches(data.get("project_id"), project_id):
        violations.append(
            f"project_id: plan {data.get('project_id')!r} does not match "
            f"expected {project_id!r}"
        )
    if not _identity_matches(data.get("issue_iid"), issue_iid):
        violations.append(
            f"issue_iid: plan {data.get('issue_iid')!r} does not match "
            f"expected {issue_iid!r}"
        )
    if not _is_nonempty_str(data.get("project_path")):
        violations.append("project_path must be a nonempty string")
    if not _is_nonempty_str(data.get("publication_uid")):
        violations.append("publication_uid must be a nonempty string")

    # Version identity (§5.3).
    version = data.get("version")
    if not _is_int(version) or version < 1:
        violations.append(f"version must be a positive integer, got {version!r}")
    else:
        expected_plan_id = f"PLAN-v{version}"
        if data.get("plan_id") != expected_plan_id:
            violations.append(
                f"plan_id: expected {expected_plan_id!r} for version {version}, "
                f"got {data.get('plan_id')!r}"
            )
    if not _is_iso8601_utc(data.get("generated_at")):
        violations.append(
            "generated_at must be an ISO-8601 UTC timestamp, got "
            f"{data.get('generated_at')!r}"
        )

    # Baseline evidence.
    baseline = data.get("baseline")
    if not isinstance(baseline, dict):
        violations.append(
            f"baseline must be an object with branch/sha, got {baseline!r}"
        )
    else:
        if not _is_nonempty_str(baseline.get("branch")):
            violations.append("baseline.branch must be a nonempty string")
        if not _is_nonempty_str(baseline.get("sha")):
            violations.append("baseline.sha must be a nonempty string")

    # Executability state.
    if not isinstance(data.get("executable"), bool):
        violations.append(
            f"executable must be a boolean, got {data.get('executable')!r}"
        )
    unresolved = data.get("unresolved_questions")
    if not isinstance(unresolved, list):
        violations.append(
            "unresolved_questions must be a list, got "
            f"{type(unresolved).__name__}"
        )
        unresolved = []
    else:
        _check_list_of_nonempty_str(violations, "unresolved_questions", unresolved)

    # Requirement understanding must be present in every version — a
    # clarification-required plan still restates the requirement and lists
    # its explicit questions.
    if not _is_nonempty_str(data.get("requirement_understanding")):
        violations.append("requirement_understanding must be a nonempty string")

    # Executable=true requires no unresolved questions (P0-SPEC-004).
    if data.get("executable") is True and unresolved:
        violations.append(
            f"executable=true but unresolved_questions has "
            f"{len(unresolved)} item(s); executable must be false while "
            "questions remain"
        )

    # Impact (semantic module scope — never an allowed_paths list, ADR 0003).
    impact = data.get("impact")
    if not isinstance(impact, list):
        violations.append(f"impact must be a list, got {type(impact).__name__}")
        impact = []
    for index, item in enumerate(impact):
        where = f"impact[{index}]"
        if not isinstance(item, dict):
            violations.append(f"{where} must be an object")
            continue
        if not _is_nonempty_str(item.get("module")):
            violations.append(f"{where}.module must be a nonempty string")
        if not isinstance(item.get("required"), bool):
            violations.append(f"{where}.required must be a boolean")
        if not _is_nonempty_str(item.get("reason")):
            violations.append(f"{where}.reason must be a nonempty string")

    # Tasks.
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        violations.append(
            "tasks must be a nonempty list (a plan proposes at least one task)"
        )
        tasks = []
    seen_ids: set[str] = set()
    task_ids: set[str] = set()
    for index, task in enumerate(tasks):
        where = f"tasks[{index}]"
        if not isinstance(task, dict):
            violations.append(f"{where} must be an object")
            continue
        task_id = task.get("id")
        if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
            violations.append(
                f"{where}.id must match TASK-001 convention, got {task_id!r}"
            )
        elif task_id in seen_ids:
            violations.append(f"duplicate task id {task_id!r} (task ids must be "
                              "unique within a plan version)")
        else:
            seen_ids.add(task_id)
            task_ids.add(task_id)
        for key in ("title", "scope"):
            if not _is_nonempty_str(task.get(key)):
                violations.append(f"{where}.{key} must be a nonempty string")
        # modules: semantic scope, required nonempty. Explicitly NOT a file
        # allowlist — allowed_paths is neither required nor modeled (ADR 0003).
        modules = task.get("modules")
        if not isinstance(modules, list) or not modules:
            violations.append(
                f"{where}.modules must be a nonempty list of module names "
                "(semantic scope per ADR 0003; no file allowlist)"
            )
        else:
            _check_list_of_nonempty_str(violations, f"{where}.modules", modules)
        deps = task.get("dependencies")
        if not isinstance(deps, list):
            violations.append(
                f"{where}.dependencies must be a list, got {type(deps).__name__}"
            )
        else:
            _check_list_of_nonempty_str(violations, f"{where}.dependencies", deps)
        acceptance = task.get("acceptance")
        _check_list_of_nonempty_str(
            violations, f"{where}.acceptance", acceptance, allow_empty=False
        )
        if "verification" in task:
            _check_list_of_nonempty_str(violations, f"{where}.verification",
                                        task["verification"])
        # Explicit configuration/database-file coverage (ADR 0003): the keys
        # must be PRESENT, but the lists may be empty.
        for key in ("configuration_changes", "database_file_changes"):
            if key not in task:
                violations.append(
                    f"{where}.{key} must be explicitly present (may be empty)"
                )
            elif not isinstance(task[key], list):
                violations.append(
                    f"{where}.{key} must be a list, got {type(task[key]).__name__}"
                )
            else:
                _check_list_of_nonempty_str(violations, f"{where}.{key}", task[key])
        status = task.get("status", TASK_STATUS_PENDING)
        if status not in TASK_STATUSES:
            violations.append(
                f"{where}.status must be one of {sorted(TASK_STATUSES)}, "
                f"got {status!r}"
            )

    # Dependencies must reference known task ids.
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        for dep in task.get("dependencies") or []:
            if _is_nonempty_str(dep) and dep not in task_ids:
                violations.append(
                    f"tasks[{index}].dependencies: unknown task id {dep!r} "
                    "(dependencies must reference ids within this plan version)"
                )

    # Acyclic dependency graph; cycles are reported with their path.
    for cycle in _find_dependency_cycles(tasks):
        violations.append("dependency cycle detected: " + " -> ".join(cycle))

    # Validation hints: provenance only, UNVERIFIED by construction.
    hints = data.get("validation_hints")
    if not isinstance(hints, list):
        violations.append(
            f"validation_hints must be a list, got {type(hints).__name__}"
        )
        hints = []
    for index, hint in enumerate(hints):
        where = f"validation_hints[{index}]"
        if not isinstance(hint, dict):
            violations.append(f"{where} must be an object")
            continue
        if not _is_nonempty_str(hint.get("module")):
            violations.append(f"{where}.module must be a nonempty string")
        if hint.get("kind") not in HINT_KINDS:
            violations.append(
                f"{where}.kind must be one of {sorted(HINT_KINDS)}, "
                f"got {hint.get('kind')!r}"
            )
        if not _is_nonempty_str(hint.get("command")):
            violations.append(f"{where}.command must be a nonempty string")
        if not _is_nonempty_str(hint.get("cwd", ".")):
            violations.append(f"{where}.cwd must be a nonempty string")
        if hint.get("origin") not in HINT_ORIGINS:
            violations.append(
                f"{where}.origin must be one of {sorted(HINT_ORIGINS)}, "
                f"got {hint.get('origin')!r}"
            )
        if hint.get("confirmation") not in HINT_CONFIRMATIONS:
            violations.append(
                f"{where}.confirmation must be {sorted(HINT_CONFIRMATIONS)[0]!r} "
                "(hints are UNVERIFIED by construction; Plan Mode never "
                "executes them), got " f"{hint.get('confirmation')!r}"
            )

    return violations


def _find_dependency_cycles(tasks: list) -> list[list[str]]:
    """Return dependency cycles as id paths, e.g. [T1, T2, T1]."""
    ids: set[str] = set()
    deps_map: dict[str, list[str]] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = task.get("id")
        if isinstance(task_id, str) and TASK_ID_RE.match(task_id):
            ids.add(task_id)
            deps_map[task_id] = [
                dep for dep in task.get("dependencies") or []
                if isinstance(dep, str)
            ]
    cycles: list[list[str]] = []
    seen_cycles: set[tuple[str, ...]] = set()
    state: dict[str, int] = {}  # 0/absent=unvisited, 1=in-progress, 2=done
    path: list[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        path.append(node)
        for dep in sorted(deps_map.get(node, [])):
            if dep not in ids:
                continue  # unknown dependency; reported separately
            status = state.get(dep, 0)
            if status == 1:
                cycle = path[path.index(dep):] + [dep]
                key = min(
                    tuple(cycle[i:] + cycle[:i]) for i in range(len(cycle) - 1)
                )
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    cycles.append(cycle)
            elif status == 0:
                visit(dep)
        path.pop()
        state[node] = 2

    for task_id in sorted(ids):
        if state.get(task_id, 0) == 0:
            visit(task_id)
    return cycles


def validate_plan(plan: dict | ExecutionPlan, *, project_id, issue_iid) -> None:
    """Validate a full §5.1 plan payload against identity and schema rules.

    Raises :class:`PlanValidationError` carrying ALL violations, or returns
    None when the plan is publishable. Rules enforced:

    - matching Project/Issue identity, supported ``schema_version``/``kind``;
    - positive integer ``version`` and ``plan_id == "PLAN-v{version}"``;
    - nonempty requirement understanding; per-task title/scope/acceptance and
      nonempty semantic ``modules`` (NOT an allowed_paths list, ADR 0003);
    - unique task ids; dependencies reference known ids; acyclic graph
      (self-loops included, cycles reported with their path);
    - explicit ``configuration_changes``/``database_file_changes`` lists
      (present, may be empty);
    - validation hints use allowed kind/origin values with nonempty command
      and confirmation UNVERIFIED;
    - ``executable=true`` requires empty ``unresolved_questions`` (and
      nonempty requirement understanding); a plan with unresolved questions
      must be ``executable=false``.
    """
    data = plan_to_dict(plan)
    violations = _validate_plan_payload(data, project_id=project_id,
                                        issue_iid=issue_iid)
    if violations:
        raise PlanValidationError(violations)


# ---------------------------------------------------------------------------
# Note payload convention: versioned marker + fenced JSON metadata section
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanRecordParse:
    """Result of scanning one note for a plan record.

    ``payload`` is the parsed fenced JSON when it parsed; ``error`` explains
    why the record is malformed (no payload / invalid JSON) and
    ``violations`` the validation problems when it parsed but is invalid.
    ``version`` comes from the marker attributes (or the payload), so the
    latest-version comparison survives a hand-mangled payload.
    """

    attrs: dict[str, str]
    payload: dict | None
    error: str | None = None

    @property
    def version(self) -> int | None:
        raw = self.attrs.get("version")
        if raw is not None and raw.isdigit():
            return int(raw)
        if isinstance(self.payload, dict) and _is_int(self.payload.get("version")):
            return self.payload["version"]
        return None

    @property
    def parsed(self) -> bool:
        """True when the fenced JSON payload was present and parseable.

        This is NOT full contract validation; callers run
        ``_validate_plan_payload`` on ``payload`` separately.
        """
        return self.error is None


def parse_plan_note(note: dict | str) -> PlanRecordParse | None:
    """Recognize a plan record in a note body.

    Returns None when the note is not a plan comment (no versioned marker).
    A note WITH the marker but a missing/broken JSON payload yields a
    PlanRecordParse with ``error`` set — malformed records are
    distinguishable, never silently skipped (P0-SPEC-004).
    """
    body = note["body"] if isinstance(note, dict) else str(note)
    match = MARKER_ATTRS_RE.search(body)
    if match is None:
        return None
    attrs = dict(_MARKER_ATTR_RE.findall(match.group("attrs") or ""))
    fence = _JSON_FENCE_RE.search(body, match.end())
    if fence is None:
        return PlanRecordParse(
            attrs=attrs, payload=None,
            error="plan marker present but no fenced ```json payload found",
        )
    try:
        payload = json.loads(fence.group(1))
    except json.JSONDecodeError as exc:
        return PlanRecordParse(
            attrs=attrs, payload=None,
            error=f"fenced JSON payload is not parseable: {exc.msg} "
                  f"(line {exc.lineno}, column {exc.colno})",
        )
    if not isinstance(payload, dict):
        return PlanRecordParse(
            attrs=attrs, payload=None,
            error=f"fenced JSON payload must be an object, got "
                  f"{type(payload).__name__}",
        )
    return PlanRecordParse(attrs=attrs, payload=payload)


def _record_matches_identity(
    record: PlanRecordParse, *, project_id, issue_iid
) -> bool:
    """Match record identity via marker attrs first, else the payload."""
    attrs = record.attrs
    if attrs.get("project_id") or attrs.get("issue_iid"):
        return _identity_matches(attrs.get("project_id"), project_id) and (
            _identity_matches(attrs.get("issue_iid"), issue_iid)
        )
    if isinstance(record.payload, dict):
        return _identity_matches(record.payload.get("project_id"), project_id) and (
            _identity_matches(record.payload.get("issue_iid"), issue_iid)
        )
    return False


def _note_ref(note: dict | str, index: int) -> str:
    if isinstance(note, dict):
        return str(note.get("id", f"notes[{index}]"))
    return f"notes[{index}]"


def _iter_note_bodies(notes) -> list[tuple[str, dict | str]]:
    """Normalize note inputs (dicts with 'body', or raw strings)."""
    bodies: list[tuple[str, dict | str]] = []
    for index, note in enumerate(notes or []):
        ref = _note_ref(note, index)
        if isinstance(note, dict):
            if "body" not in note:
                continue  # not a comment record; recognition ignores it
            bodies.append((ref, note))
        elif isinstance(note, str):
            bodies.append((ref, note))
    return bodies


def read_latest_plan(
    notes, *, project_id, issue_iid
) -> ExecutionPlan | None:
    """Return the highest-version valid plan for this Project/Issue.

    Scans note dicts (``{"id": ..., "body": ...}`` or raw strings),
    recognizes plan comments by the versioned marker + matching identity,
    validates each payload, and returns the highest-version valid plan as an
    :class:`ExecutionPlan`.

    - No recognized plan record → ``None`` (nothing published yet).
    - Non-plan comments and OTHER projects' plans are ignored.
    - A malformed/invalid record at or above the highest valid version
      raises :class:`PlanRecordError` — the latest record is never silently
      replaced by an older version (P0-SPEC-004).
    """
    valid: list[tuple[int, dict]] = []
    malformed: list[tuple[int | None, str, list[str]]] = []  # version, msg, viols

    for ref, note in _iter_note_bodies(notes):
        record = parse_plan_note(note)
        if record is None or not _record_matches_identity(
            record, project_id=project_id, issue_iid=issue_iid
        ):
            continue
        version = record.version
        if record.payload is None:
            malformed.append(
                (version, f"{ref}: {record.error}", [])
            )
            continue
        violations = _validate_plan_payload(
            record.payload, project_id=project_id, issue_iid=issue_iid
        )
        if violations:
            plan_id = record.payload.get("plan_id") or record.attrs.get("plan_id")
            malformed.append(
                (version, f"{ref}: plan {plan_id} failed validation",
                 violations)
            )
        else:
            valid.append((record.payload["version"], record.payload))

    highest_valid = max((v for v, _ in valid), default=0)
    for version, message, violations in malformed:
        # Unknown version means we cannot prove it is not the latest:
        # conservatively block rather than fall back (P0-SPEC-004).
        if version is None or version >= highest_valid:
            raise PlanRecordError(
                f"latest plan record is malformed or invalid and blocks "
                f"selection; do not fall back to an older version "
                f"({message})",
                note_ref=message.split(":", 1)[0] if ":" in message else None,
                violations=violations,
            )

    if not valid:
        return None
    payload = max(valid, key=lambda item: item[0])[1]
    return ExecutionPlan.from_dict(payload)


def next_plan_version(existing_plans_or_notes) -> int:
    """Allocate the next plan version: highest published version + 1.

    Accepts note dicts/strings (scanned for plan records via the marker) or
    already-parsed plan payloads/ExecutionPlan instances. Returns 1 when no
    plan was published yet. A recognized plan record whose version cannot be
    determined raises :class:`PlanRecordError` — allocation must never
    silently reuse a version number (§5.1: version allocation happens under
    the Issue lock; an undeterminable prior record is an error, not a
    collision risk).
    """
    versions: list[int] = []
    for index, item in enumerate(existing_plans_or_notes or []):
        if isinstance(item, ExecutionPlan):
            versions.append(item.version)
            continue
        if isinstance(item, dict) and item.get("kind") == PLAN_KIND:
            version = item.get("version")
            if not _is_int(version) or version < 1:
                raise PlanRecordError(
                    f"plans[{index}]: plan payload has no usable version "
                    f"({version!r}); refusing to allocate a colliding version"
                )
            versions.append(version)
            continue
        record = parse_plan_note(item)
        if record is None:
            continue
        version = record.version
        if version is None:
            raise PlanRecordError(
                f"notes[{index}]: recognized plan record without a "
                f"determinable version "
                f"({record.error or 'marker attributes missing version'}); "
                "refusing to allocate a possibly-colliding version"
            )
        versions.append(version)
    return max(versions, default=0) + 1


# ---------------------------------------------------------------------------
# Rendering (template 02: docs/report_template/02-issue-execution-plan.md)
# ---------------------------------------------------------------------------

_STATUS_REVIEW = "REVIEW REQUIRED"
_STATUS_CLARIFICATION = (
    "CLARIFICATION REQUIRED (NOT EXECUTABLE — unresolved questions must be "
    "answered and the plan regenerated with a new version before Execute)"
)

_METADATA_SUMMARY = (
    "Execution Plan metadata — {plan_id} (machine-readable JSON; do not edit)"
)


def _metadata_section(data: dict) -> str:
    """The versioned marker + collapsible fenced-JSON metadata block.

    The marker line carries record identity so version/identity checks
    survive even a hand-mangled payload; the fenced JSON is the SAME
    validated object rendered as Markdown.
    """
    attrs = (
        f"version={data['version']} plan_id={data['plan_id']} "
        f"project_id={data['project_id']} issue_iid={data['issue_iid']} "
        f"publication_uid={data['publication_uid']} "
        f"executable={str(data['executable']).lower()}"
    )
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    return (
        f"<!-- {PLAN_MARKER} {attrs} -->\n"
        f"<details>\n<summary>{_METADATA_SUMMARY.format(plan_id=data['plan_id'])}"
        f"</summary>\n\n"
        f"```json\n{payload}\n```\n\n"
        f"</details>"
    )


def _render_list_field(label: str, items: list[str]) -> list[str]:
    if not items:
        return [f"- **{label}:** None"]
    if len(items) == 1:
        return [f"- **{label}:** {items[0]}"]
    lines = [f"- **{label}:**"]
    lines.extend(f"  - {item}" for item in items)
    return lines


def _hint_kind_label(kind: str) -> str:
    return "Unit Test" if kind == "TEST" else kind.capitalize()


def _hint_source_label(origin: str) -> str:
    return origin.capitalize()  # DETECTED -> Detected, PROPOSED -> Proposed


def _render_markdown(data: dict) -> str:
    status = _STATUS_REVIEW if data["executable"] else _STATUS_CLARIFICATION
    lines: list[str] = [
        f"# AI Execution Plan — {data['plan_id']}",
        "",
        f"**Status:** {status}  ",
        f"**Issue:** #{data['issue_iid']}  ",
        f"**Repository:** {data['project_path']}  ",
        f"**Plan Version:** {data['plan_id']}",
        "",
        "## 1. Requirement Understanding",
        data["requirement_understanding"],
        "",
        "## 2. Impact Analysis (this repository only)",
        "",
        "| Module / Component | Impact | Reason |",
        "|---|---|---|",
    ]
    for item in data["impact"]:
        impact_label = "Required" if item["required"] else "Not required"
        lines.append(
            f"| {item['module']} | {impact_label} | {item['reason']} |"
        )
    lines.extend(["", "## 3. Execution Tasks", ""])
    for task in data["tasks"]:
        lines.append(f"### {task['id']} — {task['title']}")
        lines.append(f"- **Status:** {task.get('status', TASK_STATUS_PENDING)}")
        lines.append(f"- **Scope:** {task['scope']}")
        lines.append(
            f"- **Files / Modules:** {', '.join(task['modules'])}"
        )
        dependencies = task.get("dependencies") or []
        lines.append(
            "- **Dependencies:** "
            + (", ".join(dependencies) if dependencies else "None")
        )
        lines.extend(_render_list_field("Acceptance", task["acceptance"]))
        lines.extend(
            _render_list_field("Verification", task.get("verification") or [])
        )
        lines.extend(
            _render_list_field(
                "Configuration changes", task.get("configuration_changes") or []
            )
        )
        lines.extend(
            _render_list_field(
                "Database file changes", task.get("database_file_changes") or []
            )
        )
        lines.append("")
    lines.extend([
        "## 4. Repository Validation",
        "",
    ])
    if data["validation_hints"]:
        by_module: dict[str, list[dict]] = {}
        for hint in data["validation_hints"]:
            by_module.setdefault(hint["module"], []).append(hint)
        for module, hints in by_module.items():
            lines.append(f"### {module}")
            lines.extend([
                "",
                "| Check | Command | Source | Notes |",
                "|---|---|---|---|",
            ])
            for hint in hints:
                notes_text = f"cwd: {hint.get('cwd', '.')} — Unverified"
                lines.append(
                    f"| {_hint_kind_label(hint['kind'])} | "
                    f"`{hint['command']}` | "
                    f"{_hint_source_label(hint['origin'])} | {notes_text} |"
                )
            lines.append("")
    else:
        lines.extend([
            "_No validation commands were detected or proposed for this "
            "plan; validation commands must be confirmed before Execute._",
            "",
        ])
    lines.extend([
        "- **Detected:** Found in `AGENTS.md`, package scripts, "
        "Maven/Gradle, Makefile/scripts, or CI configuration.",
        "- **Proposed:** Agent-derived command, explicitly identified as such.",
        "- **Unverified:** Not confirmed to work in the intended environment.",
        "- **Build must pass**; test is mandatory **when a relevant test "
        "command is configured**.",
        "- Validate only modules in scope, plus modules demonstrably "
        "affected by the change.",
        "",
        "## 5. Execution Strategy",
        "- Planned task order: "
        + ", ".join(task["id"] for task in data["tasks"]) + ".",
        "- Task order follows dependencies; independent tasks may be "
        "parallelized with file ownership controls.",
        "- Shared workspace, no cross-repository changes.",
        "- One identifiable commit for each completed code-changing Task.",
        "- The source branch and MR are created **by the user** before Execute.",
        "- Regeneration publishes a complete new version (PLAN-vN+1) from all "
        "current discussion; Task IDs are version-local and every "
        "regeneration invalidates prior approval.",
        "",
        "## 6. Constraints",
        "- No edits outside the Issue repository; no deployment.",
        "- Do not create or publish branches or create MRs.",
        "- No setup, Build, or Test execution during planning; discovered or "
        "proposed commands remain UNVERIFIED hints.",
        "- Preserve existing user commits and use the MR source branch.",
        "- Modules describe semantic scope; they are not a file allowlist "
        "(new files are permitted when related to the Task).",
    ])
    return "\n".join(lines)


def render_plan_markdown(plan: dict | ExecutionPlan) -> str:
    """Render the complete template-02 Markdown, INCLUDING the marked,
    collapsible metadata section carrying the fenced JSON of the SAME
    validated object.

    The plan is validated first (against its own identity — the workflow
    layer additionally validates against the expected Project/Issue before
    publishing); rendering an invalid plan raises ``PlanValidationError``.
    """
    data = plan_to_dict(plan)
    validate_plan(
        data, project_id=data.get("project_id"), issue_iid=data.get("issue_iid")
    )
    return _render_markdown(data) + "\n\n" + _metadata_section(data)


def render_plan_comment(plan: dict | ExecutionPlan) -> tuple[str, str]:
    """Render the full plan comment as ``(markdown_body, json_payload)``.

    Both outputs derive from the SAME validated object; parsing the JSON
    payload passes ``validate_plan`` (round-trip guarantee, P0-SPEC-004
    user story 12).
    """
    data = plan_to_dict(plan)
    validate_plan(
        data, project_id=data.get("project_id"), issue_iid=data.get("issue_iid")
    )
    markdown = _render_markdown(data) + "\n\n" + _metadata_section(data)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    return markdown, payload


def build_publication_note(plan: dict | ExecutionPlan) -> dict:
    """Assemble the complete plan comment for publication.

    Returns a dict the client layer (WP-C) appends via the append-only note
    API and uses for publication-UID deduplication (§5.1: if a Plan POST
    response is lost, search notes for the same ``publication_uid`` before
    posting again).

    Keys: ``body`` (full Markdown incl. metadata section), plus
    ``publication_uid`` / ``plan_id`` / ``version`` / ``project_id`` /
    ``issue_iid`` mirrored from the plan for dedup and audit.
    """
    data = plan_to_dict(plan)
    markdown, _ = render_plan_comment(data)
    return {
        "body": markdown,
        "publication_uid": data["publication_uid"],
        "plan_id": data["plan_id"],
        "version": data["version"],
        "project_id": data["project_id"],
        "issue_iid": data["issue_iid"],
    }


# ---------------------------------------------------------------------------
# Deterministic assembly seam (implementation plan §6 call shape)
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_task(index: int, task: dict) -> dict:
    """Fill §5.1 defaults for one planner-supplied task description."""
    if not isinstance(task, dict):
        raise PlanValidationError(
            [f"task #{index + 1} must be an object, got {type(task).__name__}"]
        )
    normalized = {
        "id": task.get("id") or f"TASK-{index + 1:03d}",
        "title": task.get("title", ""),
        "scope": task.get("scope", ""),
        "modules": list(task.get("modules") or []),
        "dependencies": list(task.get("dependencies") or []),
        "acceptance": list(task.get("acceptance") or []),
        "verification": list(task.get("verification") or []),
        # Explicit presence, even when empty (ADR 0003).
        "configuration_changes": list(task.get("configuration_changes") or []),
        "database_file_changes": list(task.get("database_file_changes") or []),
        "status": task.get("status") or TASK_STATUS_PENDING,
    }
    return normalized


def _normalize_hint(hint: dict) -> dict:
    return {
        "module": hint.get("module", ""),
        "kind": str(hint.get("kind", "")).upper(),
        "command": hint.get("command", ""),
        "cwd": hint.get("cwd") or ".",
        "origin": str(hint.get("origin") or "PROPOSED").upper(),
        "confirmation": str(hint.get("confirmation") or "UNVERIFIED").upper(),
    }


def build_plan(
    *,
    issue: dict,
    project: dict,
    inspection: dict | None = None,
    requirement_understanding: str = "",
    impact: list[dict] | None = None,
    tasks: list[dict] | None = None,
    validation_hints: list[dict] | None = None,
    unresolved_questions: list[str] | None = None,
    version: int | None = None,
    publication_uid: str | None = None,
    generated_at: str | None = None,
    baseline_branch: str | None = None,
    baseline_sha: str | None = None,
) -> ExecutionPlan:
    """Deterministic plan assembly + validation seam for the planner (§6).

    WP-C's planner transport produces the plan CONTENT (requirement
    understanding, impact, tasks, hints, unresolved questions) from the
    issue context and inspection evidence; this function assembles it into a
    versioned :class:`ExecutionPlan`, normalizes the §5.1 defaults, allocates
    identity fields, and validates — raising ``PlanValidationError`` on any
    contract violation. No LLM text is trusted as workflow state beyond what
    validation accepts.

    Parameters:
    - ``issue`` / ``project``: GitLab dicts (``iid`` / ``id``,
      ``path_with_namespace``).
    - ``inspection``: optional ``InspectionEvidence.to_dict()``-shaped dict;
      ``baseline_branch``/``baseline_sha`` override it when given.
    - ``version``: allocated by the caller via ``next_plan_version`` under
      the Issue lock (defaults to 1).
    - ``publication_uid``: unique publication identifier; generated when
      omitted.
    - ``unresolved_questions``: when nonempty, ``executable`` is forced to
      False and the questions are carried verbatim (normalized, never
      rejected silently — P0-SPEC-004).

    Returns a validated :class:`ExecutionPlan`; use ``build_publication_note``
    to render the append-only comment.
    """
    if version is None:
        version = 1
    if not _is_int(version) or version < 1:
        raise PlanValidationError(
            [f"version must be a positive integer, got {version!r}"]
        )

    inspection = inspection or {}
    branch = baseline_branch or inspection.get("baseline_branch")
    sha = baseline_sha or inspection.get("inspected_commit")
    if not branch or not sha:
        raise PlanValidationError(
            [
                "baseline evidence is required: provide baseline_branch/"
                "baseline_sha or an inspection record carrying "
                "baseline_branch/inspected_commit (P0-SPEC-004: plans cite "
                "the inspected baseline)"
            ]
        )

    unresolved = [str(q) for q in (unresolved_questions or [])]
    requirement = str(requirement_understanding or "")
    # Executability is NORMALIZED here, not trusted from the caller: any
    # unresolved question forces executable=false and the questions travel
    # with the plan, so reviewers see exactly why it is blocked.
    executable = bool(requirement.strip()) and not unresolved

    plan = ExecutionPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        kind=PLAN_KIND,
        publication_uid=publication_uid or uuid.uuid4().hex,
        project_id=project.get("id"),
        project_path=str(
            project.get("path_with_namespace")
            or project.get("path")
            or ""
        ),
        issue_iid=issue.get("iid"),
        version=version,
        plan_id=f"PLAN-v{version}",
        generated_at=generated_at or _utc_now_iso(),
        baseline=PlanBaseline(branch=str(branch), sha=str(sha)),
        executable=executable,
        unresolved_questions=unresolved,
        requirement_understanding=requirement,
        impact=[
            PlanImpact(
                module=str(item.get("module", "")),
                required=bool(item.get("required", True)),
                reason=str(item.get("reason", "")),
            )
            for item in (impact or [])
            if isinstance(item, dict)
        ],
        tasks=[
            PlanTask(**_normalize_task(index, task))
            for index, task in enumerate(tasks or [])
        ],
        validation_hints=[
            PlanValidationHint(**_normalize_hint(hint))
            for hint in (validation_hints or [])
        ],
    )

    validate_plan(
        plan.to_dict(),
        project_id=plan.project_id,
        issue_iid=plan.issue_iid,
    )
    return plan
