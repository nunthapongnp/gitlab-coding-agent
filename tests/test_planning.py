"""Planning module tests (ticket 04 WP-B).

Fully offline: ``src/planning.py`` is transport-independent (no GitLab
client, no network, no command execution). Tests cover the §5.1 validation
contract, PLAN-vN version allocation (§5.3), plan-record recognition in
notes (marker + fenced JSON metadata convention), template-02 rendering with
the JSON round-trip guarantee, and the deterministic ``build_plan``
assembly seam WP-C's planner transport will call.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.planning import (
    PLAN_MARKER,
    PLAN_SCHEMA_VERSION,
    ExecutionPlan,
    PlanRecordError,
    PlanValidationError,
    build_plan,
    build_publication_note,
    next_plan_version,
    parse_plan_note,
    read_latest_plan,
    render_plan_comment,
    render_plan_markdown,
    validate_plan,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

PROJECT_ID = 42
PROJECT_PATH = "group/project"
ISSUE_IID = 123


def make_task(**overrides) -> dict:
    task = {
        "id": "TASK-001",
        "title": "Support SKU search",
        "scope": "Search by SKU without changing existing name matching",
        "modules": ["api/search"],
        "dependencies": [],
        "acceptance": ["SKU search returns the matching product"],
        "verification": ["Existing search unit tests"],
        "configuration_changes": [],
        "database_file_changes": [],
        "status": "PENDING",
    }
    task.update(overrides)
    return task


def make_hint(**overrides) -> dict:
    hint = {
        "module": "api",
        "kind": "BUILD",
        "command": "./mvnw -B -DskipTests package",
        "cwd": "api",
        "origin": "DETECTED",
        "confirmation": "UNVERIFIED",
    }
    hint.update(overrides)
    return hint


def make_plan(**overrides) -> dict:
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "kind": "execution_plan",
        "publication_uid": "unique-operation-id-1",
        "project_id": PROJECT_ID,
        "project_path": PROJECT_PATH,
        "issue_iid": ISSUE_IID,
        "version": 1,
        "plan_id": "PLAN-v1",
        "generated_at": "2026-11-09T09:00:00Z",
        "baseline": {"branch": "main", "sha": "inspected-sha"},
        "executable": True,
        "unresolved_questions": [],
        "requirement_understanding": "Expected product behavior",
        "impact": [
            {"module": "api/search", "required": True,
             "reason": "Search contract"}
        ],
        "tasks": [make_task()],
        "validation_hints": [make_hint()],
    }
    plan.update(overrides)
    return plan


def make_issue_note(note_id: int, body: str) -> dict:
    return {"id": note_id, "body": body}


def plan_note_body(plan_dict: dict) -> str:
    """Render a note body through the module itself (round-trip source)."""
    markdown, _ = render_plan_comment(plan_dict)
    return markdown


def make_issue(**overrides) -> dict:
    issue = {"iid": ISSUE_IID, "title": "Add SKU search",
             "description": "Support searching products by SKU."}
    issue.update(overrides)
    return issue


def make_project(**overrides) -> dict:
    project = {"id": PROJECT_ID, "path": "project",
               "path_with_namespace": PROJECT_PATH,
               "default_branch": "main"}
    project.update(overrides)
    return project


def make_inspection(**overrides) -> dict:
    inspection = {
        "baseline_branch": "main",
        "inspected_commit": "abc123def456",
        "modules": [{"path": "api", "file_count": 3}],
        "config_files": ["pom.xml"],
        "verification_hints": {
            "build": {"command": "./mvnw package", "origin": "DETECTED",
                      "status": "UNVERIFIED"},
        },
    }
    inspection.update(overrides)
    return inspection


# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------


class TestValidatePlanHappyPath:
    def test_valid_plan_passes(self):
        assert validate_plan(make_plan(), project_id=PROJECT_ID,
                             issue_iid=ISSUE_IID) is None

    def test_accepts_executionplan_instance(self):
        plan = build_plan(
            issue=make_issue(), project=make_project(),
            inspection=make_inspection(),
            requirement_understanding="Expected product behavior",
            tasks=[{"title": "T", "scope": "S", "modules": ["api"],
                    "acceptance": ["A"]}],
        )
        assert validate_plan(plan, project_id=PROJECT_ID,
                             issue_iid=ISSUE_IID) is None

    def test_no_file_allowlist_is_required_or_modeled(self):
        # ADR 0003: modules are semantic scope; a plan without any
        # allowed_paths key must validate, and modules are not paths.
        plan = make_plan()
        plan["tasks"][0]["modules"] = ["api/search", "web/search layer"]
        assert "allowed_paths" not in json.dumps(plan)
        validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)

    def test_empty_config_and_db_change_lists_are_valid(self):
        plan = make_plan()
        assert plan["tasks"][0]["configuration_changes"] == []
        assert plan["tasks"][0]["database_file_changes"] == []
        validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)


class TestValidatePlanIdentityAndSchema:
    def test_all_violations_are_collected_not_just_first(self):
        plan = make_plan()
        plan["tasks"][0]["scope"] = ""
        plan["validation_hints"][0]["kind"] = "RUN"
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        violations = excinfo.value.violations
        assert len(violations) >= 2
        assert any("tasks[0].scope" in v for v in violations)
        assert any("validation_hints[0].kind" in v for v in violations)

    def test_mismatched_project_id_rejected(self):
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(make_plan(), project_id=999, issue_iid=ISSUE_IID)
        assert any("project_id" in v for v in excinfo.value.violations)

    def test_mismatched_issue_iid_rejected(self):
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(make_plan(), project_id=PROJECT_ID, issue_iid=999)
        assert any("issue_iid" in v for v in excinfo.value.violations)

    def test_identity_match_is_type_tolerant(self):
        plan = make_plan()
        plan["project_id"] = "42"
        plan["issue_iid"] = "123"
        validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)

    @pytest.mark.parametrize("bad_version", [0, -1, "1", 1.5, None, True])
    def test_bad_version_rejected(self, bad_version):
        plan = make_plan(version=bad_version, plan_id="PLAN-v1")
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("version" in v for v in excinfo.value.violations)

    def test_unsupported_schema_version_rejected(self):
        plan = make_plan(schema_version=2)
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("schema_version" in v for v in excinfo.value.violations)

    def test_wrong_kind_rejected(self):
        plan = make_plan(kind="plan")
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("kind" in v for v in excinfo.value.violations)

    @pytest.mark.parametrize("plan_id", ["PLAN-v2", "plan-v1", "PLAN-1"])
    def test_plan_id_must_match_version(self, plan_id):
        plan = make_plan(plan_id=plan_id)
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("plan_id" in v for v in excinfo.value.violations)

    @pytest.mark.parametrize("bad_generated_at", [
        "not-a-timestamp", "2026-11-09 09:00:00", "2026-11-09T09:00:00",
    ])
    def test_generated_at_must_be_iso8601_utc(self, bad_generated_at):
        plan = make_plan(generated_at=bad_generated_at)
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("generated_at" in v for v in excinfo.value.violations)

    def test_generated_at_accepts_offset_utc_form(self):
        plan = make_plan(generated_at="2026-11-09T09:00:00+00:00")
        validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)

    def test_missing_publication_uid_rejected(self):
        plan = make_plan(publication_uid="")
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("publication_uid" in v for v in excinfo.value.violations)

    def test_non_dict_plan_rejected(self):
        with pytest.raises(PlanValidationError):
            validate_plan("nope", project_id=PROJECT_ID, issue_iid=ISSUE_IID)

    def test_baseline_branch_and_sha_required(self):
        plan = make_plan(baseline={"branch": "", "sha": ""})
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        violations = excinfo.value.violations
        assert any("baseline.branch" in v for v in violations)
        assert any("baseline.sha" in v for v in violations)


class TestValidatePlanContent:
    @pytest.mark.parametrize("field", [
        "requirement_understanding",
    ])
    def test_empty_top_level_content_rejected(self, field):
        plan = make_plan(**{field: ""})
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any(field in v for v in excinfo.value.violations)

    @pytest.mark.parametrize("field", ["title", "scope"])
    def test_empty_task_text_fields_rejected(self, field):
        plan = make_plan(tasks=[make_task(**{field: "  "})])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any(f"tasks[0].{field}" in v for v in excinfo.value.violations)

    def test_empty_task_acceptance_rejected(self):
        plan = make_plan(tasks=[make_task(acceptance=[])])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("tasks[0].acceptance" in v for v in excinfo.value.violations)

    def test_empty_task_modules_rejected(self):
        plan = make_plan(tasks=[make_task(modules=[])])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("modules" in v and "semantic scope" in v
                   for v in excinfo.value.violations)

    def test_empty_tasks_list_rejected(self):
        plan = make_plan(tasks=[])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("tasks" in v for v in excinfo.value.violations)

    def test_duplicate_task_ids_rejected(self):
        plan = make_plan(tasks=[
            make_task(id="TASK-001"),
            make_task(id="TASK-001", title="Second", scope="Other"),
        ])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("duplicate task id" in v and "TASK-001" in v
                   for v in excinfo.value.violations)

    def test_malformed_task_id_rejected(self):
        plan = make_plan(tasks=[make_task(id="TASK1")])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("TASK-001 convention" in v for v in excinfo.value.violations)

    def test_unknown_dependency_rejected(self):
        plan = make_plan(tasks=[make_task(dependencies=["TASK-009"])])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("unknown task id 'TASK-009'" in v
                   for v in excinfo.value.violations)

    def test_dependency_cycle_reported_with_path(self):
        plan = make_plan(tasks=[
            make_task(id="TASK-001", dependencies=["TASK-002"]),
            make_task(id="TASK-002", dependencies=["TASK-001"]),
        ])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        cycle = [v for v in excinfo.value.violations if "cycle" in v]
        assert cycle, excinfo.value.violations
        assert "TASK-001 -> TASK-002 -> TASK-001" in cycle[0]

    def test_self_loop_dependency_reported_as_cycle(self):
        plan = make_plan(tasks=[make_task(id="TASK-001",
                                          dependencies=["TASK-001"])])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("TASK-001 -> TASK-001" in v
                   for v in excinfo.value.violations)

    def test_known_acyclic_dependencies_accepted(self):
        plan = make_plan(tasks=[
            make_task(id="TASK-001"),
            make_task(id="TASK-002", dependencies=["TASK-001"]),
            make_task(id="TASK-003", dependencies=["TASK-001", "TASK-002"]),
        ])
        validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)

    def test_missing_configuration_changes_key_rejected(self):
        task = make_task()
        del task["configuration_changes"]
        plan = make_plan(tasks=[task])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("configuration_changes" in v and "explicitly present" in v
                   for v in excinfo.value.violations)

    def test_missing_database_file_changes_key_rejected(self):
        task = make_task()
        del task["database_file_changes"]
        plan = make_plan(tasks=[task])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("database_file_changes" in v and "explicitly present" in v
                   for v in excinfo.value.violations)

    def test_bad_task_status_rejected(self):
        plan = make_plan(tasks=[make_task(status="DONE")])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("tasks[0].status" in v for v in excinfo.value.violations)


class TestValidatePlanHints:
    @pytest.mark.parametrize("bad_kind", ["RUN", "build", "", None])
    def test_hint_kind_must_be_build_or_test(self, bad_kind):
        plan = make_plan(validation_hints=[make_hint(kind=bad_kind)])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("validation_hints[0].kind" in v
                   for v in excinfo.value.violations)

    @pytest.mark.parametrize("bad_origin", ["CONFIGURED", "INVENTED", "", None])
    def test_hint_origin_must_be_detected_or_proposed(self, bad_origin):
        plan = make_plan(validation_hints=[make_hint(origin=bad_origin)])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("validation_hints[0].origin" in v
                   for v in excinfo.value.violations)

    @pytest.mark.parametrize("bad_confirmation", ["VERIFIED", "PENDING", None])
    def test_hint_confirmation_must_be_unverified(self, bad_confirmation):
        plan = make_plan(
            validation_hints=[make_hint(confirmation=bad_confirmation)]
        )
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("confirmation" in v and "UNVERIFIED" in v
                   for v in excinfo.value.violations)

    def test_hint_command_must_be_nonempty(self):
        plan = make_plan(validation_hints=[make_hint(command="  ")])
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("validation_hints[0].command" in v
                   for v in excinfo.value.violations)

    def test_empty_validation_hints_list_is_valid(self):
        plan = make_plan(validation_hints=[])
        validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)


class TestValidatePlanExecutability:
    def test_executable_true_with_unresolved_questions_rejected(self):
        plan = make_plan(
            executable=True,
            unresolved_questions=["Should search be case-insensitive?"],
        )
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("executable must be false" in v
                   for v in excinfo.value.violations)

    def test_executable_false_with_unresolved_questions_is_valid(self):
        plan = make_plan(
            executable=False,
            unresolved_questions=["Should search be case-insensitive?"],
        )
        validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)

    def test_executable_true_with_no_questions_is_valid(self):
        plan = make_plan(executable=True, unresolved_questions=[])
        validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)

    def test_executable_must_be_boolean(self):
        plan = make_plan(executable="true")
        with pytest.raises(PlanValidationError) as excinfo:
            validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert any("executable" in v for v in excinfo.value.violations)


# ---------------------------------------------------------------------------
# Version allocation and plan-record recognition
# ---------------------------------------------------------------------------


class TestNextPlanVersion:
    def test_no_prior_notes_yields_version_1(self):
        assert next_plan_version([]) == 1
        assert next_plan_version(None) == 1

    def test_ignores_non_plan_comments(self):
        notes = [
            make_issue_note(1, "Looks good to me!"),
            make_issue_note(2, "```\nnot a plan\n```"),
        ]
        assert next_plan_version(notes) == 1

    def test_highest_published_version_plus_one(self):
        v1 = make_plan(version=1, plan_id="PLAN-v1")
        v3 = make_plan(version=3, plan_id="PLAN-v3")
        notes = [
            make_issue_note(1, "a question about scope"),
            make_issue_note(2, plan_note_body(v1)),
            make_issue_note(3, "feedback: please add tests"),
            make_issue_note(4, plan_note_body(v3)),
        ]
        assert next_plan_version(notes) == 4

    def test_accepts_plan_payloads_directly(self):
        assert next_plan_version([
            make_plan(version=2, plan_id="PLAN-v2"),
            make_plan(version=5, plan_id="PLAN-v5"),
        ]) == 6

    def test_accepts_executionplan_instances(self):
        plan = build_plan(
            issue=make_issue(), project=make_project(),
            inspection=make_inspection(),
            requirement_understanding="Expected behavior",
            tasks=[{"title": "T", "scope": "S", "modules": ["api"],
                    "acceptance": ["A"]}],
            version=4,
        )
        assert next_plan_version([plan]) == 5

    def test_undeterminable_version_raises_instead_of_colliding(self):
        body = plan_note_body(make_plan(version=2, plan_id="PLAN-v2"))
        # Hand-mangled: marker survives but the version attribute and the
        # payload version are both unreadable.
        mangled = body.replace("version=2", "version=not-a-number")
        mangled = mangled.replace('"version": 2', '"version": "two"')
        with pytest.raises(PlanRecordError) as excinfo:
            next_plan_version([make_issue_note(1, mangled)])
        assert "version" in str(excinfo.value)


class TestReadLatestPlan:
    def test_no_plan_records_returns_none(self):
        notes = [make_issue_note(1, "just a comment")]
        assert read_latest_plan(notes, project_id=PROJECT_ID,
                                issue_iid=ISSUE_IID) is None

    def test_returns_highest_version_valid_plan(self):
        v1 = make_plan(version=1, plan_id="PLAN-v1",
                       publication_uid="uid-1")
        v2 = make_plan(version=2, plan_id="PLAN-v2",
                       publication_uid="uid-2",
                       requirement_understanding="Revised understanding")
        notes = [
            make_issue_note(1, "question from the developer"),
            make_issue_note(2, plan_note_body(v1)),
            make_issue_note(3, "feedback: see revised plan"),
            make_issue_note(4, plan_note_body(v2)),
        ]
        latest = read_latest_plan(notes, project_id=PROJECT_ID,
                                  issue_iid=ISSUE_IID)
        assert isinstance(latest, ExecutionPlan)
        assert latest.version == 2
        assert latest.plan_id == "PLAN-v2"
        assert latest.requirement_understanding == "Revised understanding"

    def test_malformed_latest_raises_rather_than_falling_back(self):
        v2 = make_plan(version=2, plan_id="PLAN-v2",
                       requirement_understanding="Revised")
        good_body = plan_note_body(v2)
        # Latest record: marker + attrs intact (version=3), JSON mangled.
        broken_latest = good_body.replace('"version": 2', '"version": 3') \
                                 .replace("PLAN-v2", "PLAN-v3") \
                                 .replace("```json\n{", "```json\n{broken: ,")
        notes = [
            make_issue_note(1, plan_note_body(make_plan(version=1))),
            make_issue_note(2, broken_latest),
        ]
        with pytest.raises(PlanRecordError) as excinfo:
            read_latest_plan(notes, project_id=PROJECT_ID,
                             issue_iid=ISSUE_IID)
        assert "not fall back" in str(excinfo.value)
        # The typed error is distinguishable from a validation failure.
        assert not isinstance(excinfo.value, PlanValidationError)

    def test_invalid_latest_payload_raises_rather_than_falling_back(self):
        # Render a VALID PLAN-v3 note body, then mangle the fenced JSON
        # payload (marker attrs keep version=3, so the record is recognized
        # as the latest but its payload fails validation).
        broken_v3 = plan_note_body(
            make_plan(version=3, plan_id="PLAN-v3",
                      publication_uid="uid-invalid-3")
        ).replace(
            '"scope": "Search by SKU without changing existing name matching"',
            '"scope": ""',
        )
        assert '"scope": ""' in broken_v3  # payload really was mangled
        notes = [
            make_issue_note(1, plan_note_body(make_plan(version=2,
                                                        plan_id="PLAN-v2"))),
            make_issue_note(2, broken_v3),
        ]
        with pytest.raises(PlanRecordError) as excinfo:
            read_latest_plan(notes, project_id=PROJECT_ID,
                             issue_iid=ISSUE_IID)
        assert any("scope" in v for v in excinfo.value.violations)

    def test_older_malformed_record_is_tolerated_when_newer_valid_exists(self):
        broken_v1 = plan_note_body(make_plan(version=1)) \
            .replace("```json\n{", "```json\n{broken")
        notes = [
            make_issue_note(1, broken_v1),
            make_issue_note(2, plan_note_body(make_plan(version=2,
                                                        plan_id="PLAN-v2"))),
        ]
        latest = read_latest_plan(notes, project_id=PROJECT_ID,
                                  issue_iid=ISSUE_IID)
        assert latest is not None and latest.version == 2

    def test_ignores_non_plan_comments(self):
        notes = [
            make_issue_note(1, "A comment containing ```json\n{}\n``` but "
                               f"no {PLAN_MARKER} marker"),
            make_issue_note(2, "<!-- some-other-marker --><details>"
                               "<summary>x</summary>\n```json\n{}\n```"
                               "</details>"),
        ]
        assert read_latest_plan(notes, project_id=PROJECT_ID,
                                issue_iid=ISSUE_IID) is None

    def test_ignores_other_projects_plans(self):
        other = make_plan(project_id=999, version=5, plan_id="PLAN-v5")
        ours = make_plan(version=1)
        notes = [
            make_issue_note(1, plan_note_body(other)),
            make_issue_note(2, plan_note_body(ours)),
        ]
        latest = read_latest_plan(notes, project_id=PROJECT_ID,
                                  issue_iid=ISSUE_IID)
        assert latest is not None and latest.version == 1

    def test_ignores_other_issues_plans(self):
        other = make_plan(issue_iid=777, version=9, plan_id="PLAN-v9")
        notes = [make_issue_note(1, plan_note_body(other))]
        assert read_latest_plan(notes, project_id=PROJECT_ID,
                                issue_iid=ISSUE_IID) is None


class TestParsePlanNote:
    def test_non_plan_note_returns_none(self):
        assert parse_plan_note(make_issue_note(1, "hello")) is None
        assert parse_plan_note("plain string body") is None

    def test_marker_attrs_are_extracted(self):
        body = plan_note_body(make_plan())
        record = parse_plan_note(make_issue_note(1, body))
        assert record is not None
        assert record.attrs["plan_id"] == "PLAN-v1"
        assert record.attrs["version"] == "1"
        assert record.attrs["project_id"] == str(PROJECT_ID)
        assert record.attrs["issue_iid"] == str(ISSUE_IID)
        assert record.attrs["publication_uid"] == "unique-operation-id-1"
        assert record.parsed is True  # payload present and parseable

    def test_marker_without_json_payload_is_a_typed_malformed_record(self):
        body = (f"<!-- {PLAN_MARKER} version=1 plan_id=PLAN-v1 -->\n"
                "<details><summary>no payload</summary></details>")
        record = parse_plan_note(make_issue_note(1, body))
        assert record is not None
        assert record.payload is None
        assert record.error and "no fenced" in record.error

    def test_broken_json_payload_is_a_typed_malformed_record(self):
        body = plan_note_body(make_plan()).replace("```json\n{",
                                                   "```json\n{oops")
        record = parse_plan_note(make_issue_note(1, body))
        assert record is not None
        assert record.payload is None
        assert record.error and "not parseable" in record.error
        assert record.version == 1  # survives via marker attrs


# ---------------------------------------------------------------------------
# Rendering (template 02)
# ---------------------------------------------------------------------------


class TestRenderPlanMarkdown:
    def test_header_contains_required_fields(self):
        markdown = render_plan_markdown(make_plan())
        assert markdown.startswith("# AI Execution Plan — PLAN-v1")
        assert "**Status:** REVIEW REQUIRED" in markdown
        assert f"**Issue:** #{ISSUE_IID}" in markdown
        assert f"**Repository:** {PROJECT_PATH}" in markdown
        assert "**Plan Version:** PLAN-v1" in markdown

    def test_all_six_sections_present(self):
        markdown = render_plan_markdown(make_plan())
        for heading in (
            "## 1. Requirement Understanding",
            "## 2. Impact Analysis (this repository only)",
            "## 3. Execution Tasks",
            "## 4. Repository Validation",
            "## 5. Execution Strategy",
            "## 6. Constraints",
        ):
            assert heading in markdown

    def test_requirement_understanding_and_impact_table_rendered(self):
        markdown = render_plan_markdown(make_plan(
            impact=[
                {"module": "api/search", "required": True,
                 "reason": "Search contract"},
                {"module": "web/search", "required": False,
                 "reason": "No UI change"},
            ]
        ))
        assert "Expected product behavior" in markdown
        assert "| api/search | Required | Search contract |" in markdown
        assert "| web/search | Not required | No UI change |" in markdown

    def test_task_details_rendered_with_required_fields(self):
        markdown = render_plan_markdown(make_plan(tasks=[
            make_task(id="TASK-001"),
            make_task(
                id="TASK-002",
                title="Wire search endpoint",
                scope="Expose GET /search",
                modules=["api/search", "web/search"],
                dependencies=["TASK-001"],
                acceptance=["GET /search returns 200 with matches"],
                verification=["Integration test for /search"],
            ),
        ]))
        assert "### TASK-001 — Support SKU search" in markdown
        assert "### TASK-002 — Wire search endpoint" in markdown
        assert markdown.count("- **Status:** PENDING") == 2
        assert "- **Scope:** Search by SKU" in markdown
        assert "- **Files / Modules:** api/search" in markdown
        # TASK-001 has no deps; TASK-002 lists its dependency explicitly.
        first_block = markdown.split("### TASK-002")[0]
        second_block = "### TASK-002" + markdown.split("### TASK-002")[1]
        assert "- **Dependencies:** None" in first_block
        assert "- **Dependencies:** TASK-001" in second_block
        assert "- **Acceptance:** SKU search returns the matching product" \
               in markdown
        assert "- **Verification:** Existing search unit tests" in markdown

    def test_repository_validation_table_with_sources(self):
        markdown = render_plan_markdown(make_plan(validation_hints=[
            make_hint(module="api", kind="BUILD", origin="DETECTED"),
            make_hint(module="api", kind="TEST", command="mvn test",
                      origin="PROPOSED"),
        ]))
        assert "### api" in markdown
        assert "| Build | `./mvnw -B -DskipTests package` | Detected |" \
               in markdown
        assert "| Unit Test | `mvn test` | Proposed |" in markdown
        assert "Unverified" in markdown
        # Template legend for sources.
        assert "- **Detected:** Found in `AGENTS.md`" in markdown
        assert "- **Proposed:** Agent-derived command" in markdown
        assert "- **Unverified:** Not confirmed to work" in markdown

    def test_no_hints_renders_explicit_no_commands_note(self):
        markdown = render_plan_markdown(make_plan(validation_hints=[]))
        assert "No validation commands were detected or proposed" in markdown

    def test_metadata_section_present_with_marker_and_publication_uid(self):
        markdown = render_plan_markdown(make_plan())
        assert PLAN_MARKER in markdown
        assert "publication_uid=unique-operation-id-1" in markdown
        assert "<details>" in markdown and "</details>" in markdown
        assert "```json" in markdown

    def test_metadata_json_matches_the_rendered_object(self):
        plan = make_plan()
        markdown = render_plan_markdown(plan)
        start = markdown.index("```json\n") + len("```json\n")
        end = markdown.index("\n```", start)
        embedded = json.loads(markdown[start:end])
        assert embedded == plan

    def test_rendering_invalid_plan_raises(self):
        with pytest.raises(PlanValidationError):
            render_plan_markdown(make_plan(tasks=[]))


class TestRenderPlanCommentRoundTrip:
    def test_json_payload_round_trips_through_validate_plan(self):
        plan = make_plan()
        markdown, payload = render_plan_comment(plan)
        assert markdown and payload
        parsed = json.loads(payload)
        validate_plan(parsed, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        assert parsed == plan

    def test_markdown_and_json_derive_from_same_object(self):
        plan = make_plan()
        markdown, payload = render_plan_comment(plan)
        # Mutate one field, re-render: BOTH outputs change together.
        changed = json.loads(json.dumps(plan))
        changed["requirement_understanding"] = "Revised expected behavior"
        assert changed != plan
        markdown2, payload2 = render_plan_comment(changed)
        assert markdown2 != markdown
        assert payload2 != payload
        assert "Revised expected behavior" in markdown2
        assert "Revised expected behavior" in payload2
        # The embedded metadata JSON equals the standalone payload.
        start = markdown2.index("```json\n") + len("```json\n")
        end = markdown2.index("\n```", start)
        assert json.loads(markdown2[start:end]) == json.loads(payload2)

    def test_publication_uid_and_marker_present_in_both_outputs(self):
        markdown, payload = render_plan_comment(make_plan())
        assert "unique-operation-id-1" in markdown
        assert "unique-operation-id-1" in payload
        assert PLAN_MARKER in markdown

    def test_publication_note_carries_dedup_and_identity_markers(self):
        note = build_publication_note(make_plan())
        assert note["publication_uid"] == "unique-operation-id-1"
        assert note["plan_id"] == "PLAN-v1"
        assert note["version"] == 1
        assert note["project_id"] == PROJECT_ID
        assert note["issue_iid"] == ISSUE_IID
        assert PLAN_MARKER in note["body"]
        # The note body is exactly the render_plan_comment markdown.
        markdown, _ = render_plan_comment(make_plan())
        assert note["body"] == markdown

    def test_note_body_round_trips_through_read_latest_plan(self):
        plan = make_plan()
        notes = [make_issue_note(1, plan_note_body(plan))]
        latest = read_latest_plan(notes, project_id=PROJECT_ID,
                                  issue_iid=ISSUE_IID)
        assert isinstance(latest, ExecutionPlan)
        assert latest.to_dict() == plan


class TestClarificationRequiredRendering:
    def test_non_executable_plan_has_distinguishable_status(self):
        plan = make_plan(
            executable=False,
            unresolved_questions=["Should search be case-insensitive?"],
        )
        executable_md, _ = render_plan_comment(make_plan())
        markdown, payload = render_plan_comment(plan)
        assert "**Status:** REVIEW REQUIRED" in executable_md
        assert "**Status:** REVIEW REQUIRED" not in markdown
        assert "CLARIFICATION REQUIRED" in markdown
        assert "NOT EXECUTABLE" in markdown
        # Unresolved questions are carried in both outputs.
        assert "case-insensitive" in markdown
        assert "case-insensitive" in payload
        validate_plan(json.loads(payload), project_id=PROJECT_ID,
                      issue_iid=ISSUE_IID)


# ---------------------------------------------------------------------------
# build_plan assembly seam (§6 call shape; WP-C consumes this)
# ---------------------------------------------------------------------------


class TestBuildPlan:
    def test_assembles_a_validated_plan_from_issue_inspection_and_content(self):
        plan = build_plan(
            issue=make_issue(),
            project=make_project(),
            inspection=make_inspection(),
            requirement_understanding="Search by SKU must return products",
            impact=[{"module": "api/search", "required": True,
                     "reason": "Search contract"}],
            tasks=[{
                "title": "Support SKU search",
                "scope": "Add SKU lookup",
                "modules": ["api/search"],
                "acceptance": ["SKU search returns the matching product"],
                "verification": ["Search unit tests"],
            }],
            validation_hints=[{
                "module": "api", "kind": "BUILD",
                "command": "./mvnw -B package", "cwd": "api",
                "origin": "DETECTED",
            }],
        )

        assert isinstance(plan, ExecutionPlan)
        assert plan.schema_version == PLAN_SCHEMA_VERSION
        assert plan.kind == "execution_plan"
        assert plan.project_id == PROJECT_ID
        assert plan.project_path == PROJECT_PATH
        assert plan.issue_iid == ISSUE_IID
        assert plan.version == 1
        assert plan.plan_id == "PLAN-v1"
        assert plan.baseline.branch == "main"
        assert plan.baseline.sha == "abc123def456"
        assert plan.executable is True
        assert plan.unresolved_questions == []
        # Task identity assigned per §5.3 and normalized with explicit
        # configuration/database-file coverage.
        assert plan.tasks[0].id == "TASK-001"
        assert plan.tasks[0].status == "PENDING"
        assert plan.tasks[0].configuration_changes == []
        assert plan.tasks[0].database_file_changes == []
        # Hint provenance normalized: UNVERIFIED by construction.
        assert plan.validation_hints[0].confirmation == "UNVERIFIED"
        assert plan.validation_hints[0].origin == "DETECTED"
        # Publication identity allocated.
        assert plan.publication_uid
        # generated_at is ISO-8601 UTC.
        datetime.strptime(plan.generated_at, "%Y-%m-%dT%H:%M:%SZ")
        # The assembled plan satisfies the full §5.1 contract.
        validate_plan(plan, project_id=PROJECT_ID, issue_iid=ISSUE_IID)
        # And renders into a round-trippable note.
        markdown, payload = render_plan_comment(plan)
        assert PLAN_MARKER in markdown
        validate_plan(json.loads(payload), project_id=PROJECT_ID,
                      issue_iid=ISSUE_IID)

    def test_unresolved_questions_force_executable_false(self):
        plan = build_plan(
            issue=make_issue(), project=make_project(),
            inspection=make_inspection(),
            requirement_understanding="Something was said about search",
            tasks=[{"title": "T", "scope": "S", "modules": ["api"],
                    "acceptance": ["A"]}],
            unresolved_questions=["Which search backend is authoritative?"],
        )
        # Normalized, not trusted from the caller: no executable parameter
        # exists to contradict the questions.
        assert plan.executable is False
        assert plan.unresolved_questions == [
            "Which search backend is authoritative?"
        ]
        validate_plan(plan.to_dict(), project_id=PROJECT_ID,
                      issue_iid=ISSUE_IID)

    def test_publication_uids_are_unique_per_plan(self):
        kwargs = dict(
            issue=make_issue(), project=make_project(),
            inspection=make_inspection(),
            requirement_understanding="Expected behavior",
            tasks=[{"title": "T", "scope": "S", "modules": ["api"],
                    "acceptance": ["A"]}],
        )
        first = build_plan(**kwargs)
        second = build_plan(**kwargs)
        assert first.publication_uid != second.publication_uid

    def test_version_and_plan_id_are_allocatable(self):
        plan = build_plan(
            issue=make_issue(), project=make_project(),
            inspection=make_inspection(),
            requirement_understanding="Expected behavior",
            tasks=[{"title": "T", "scope": "S", "modules": ["api"],
                    "acceptance": ["A"]}],
            version=7,
        )
        assert plan.version == 7
        assert plan.plan_id == "PLAN-v7"

    def test_missing_baseline_evidence_is_actionable(self):
        with pytest.raises(PlanValidationError) as excinfo:
            build_plan(
                issue=make_issue(), project=make_project(),
                inspection=None,
                requirement_understanding="Expected behavior",
                tasks=[{"title": "T", "scope": "S", "modules": ["api"],
                        "acceptance": ["A"]}],
            )
        assert "baseline" in str(excinfo.value)

    def test_explicit_baseline_overrides_inspection(self):
        plan = build_plan(
            issue=make_issue(), project=make_project(),
            inspection=make_inspection(),
            requirement_understanding="Expected behavior",
            tasks=[{"title": "T", "scope": "S", "modules": ["api"],
                    "acceptance": ["A"]}],
            baseline_branch="release/1.4",
            baseline_sha="deadbeef",
        )
        assert plan.baseline.branch == "release/1.4"
        assert plan.baseline.sha == "deadbeef"

    def test_invalid_content_raises_planvalidationerror(self):
        with pytest.raises(PlanValidationError) as excinfo:
            build_plan(
                issue=make_issue(), project=make_project(),
                inspection=make_inspection(),
                requirement_understanding="Expected behavior",
                tasks=[{"title": "T", "scope": "", "modules": [],
                        "acceptance": []}],
            )
        assert excinfo.value.violations

    def test_bad_version_argument_rejected(self):
        with pytest.raises(PlanValidationError):
            build_plan(
                issue=make_issue(), project=make_project(),
                inspection=make_inspection(),
                requirement_understanding="Expected behavior",
                tasks=[{"title": "T", "scope": "S", "modules": ["api"],
                        "acceptance": ["A"]}],
                version=0,
            )


class TestBuildPlanHintWorkingDirectories:
    """§5.1: validation-hint cwd must be real, discovered by inspection."""

    def _build(self, **overrides) -> ExecutionPlan:
        kwargs = dict(
            issue=make_issue(),
            project=make_project(),
            inspection=make_inspection(),
            requirement_understanding="Expected behavior",
            tasks=[{"title": "T", "scope": "S", "modules": ["api"],
                    "acceptance": ["A"]}],
        )
        kwargs.update(overrides)
        return build_plan(**kwargs)

    @pytest.mark.parametrize("cwd", [".", "api", "./api", "api/"])
    def test_repo_root_and_inspected_module_dirs_are_accepted(self, cwd):
        plan = self._build(validation_hints=[make_hint(cwd=cwd)])
        # Everything normalizes to a directory the evidence discovered.
        assert plan.validation_hints[0].cwd

    def test_config_file_directory_is_discoverable_from_evidence(self):
        # The config file lives at api/pom.xml: its directory is evidence.
        inspection = make_inspection(modules=[], config_files=["api/pom.xml"])
        plan = self._build(
            inspection=inspection, validation_hints=[make_hint(cwd="api")]
        )
        assert plan.validation_hints[0].cwd == "api"

    def test_fabricated_cwd_is_a_validation_violation(self):
        with pytest.raises(PlanValidationError) as excinfo:
            self._build(
                validation_hints=[make_hint(cwd="nonexistent/module")]
            )
        violations = excinfo.value.violations
        assert any(
            "validation_hints[0].cwd" in v and "nonexistent/module" in v
            for v in violations
        )
        # The actionable message names the directories the evidence has.
        assert any("api" in v for v in violations)

    def test_cwd_violations_are_collected_alongside_other_violations(self):
        with pytest.raises(PlanValidationError) as excinfo:
            self._build(
                tasks=[{"title": "", "scope": "", "modules": [],
                        "acceptance": []}],
                validation_hints=[
                    make_hint(cwd="made-up/dir"),
                    make_hint(cwd="api"),  # valid; must not be flagged
                ],
            )
        violations = excinfo.value.violations
        assert any("validation_hints[0].cwd" in v for v in violations)
        assert not any("validation_hints[1].cwd" in v for v in violations)
        # The schema violations are reported in the SAME collected error.
        assert any("tasks[0].title" in v for v in violations)

    def test_task_modules_are_never_crosschecked_against_the_tree(self, tmp_path):
        # ADR 0003: modules are semantic scope. A module name with no
        # corresponding inspected directory must NOT be a violation.
        plan = self._build(
            inspection=make_inspection(modules=[]),
            tasks=[{"title": "T", "scope": "S", "modules": ["brand/new/module"],
                    "acceptance": ["A"]}],
            validation_hints=[make_hint(cwd=".")],
        )
        assert plan.tasks[0].modules == ["brand/new/module"]
