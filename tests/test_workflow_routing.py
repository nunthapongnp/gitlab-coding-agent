"""Label-routing tests (ticket 02).

Seams under test (per docs/p0/specs/README.md "Shared testing seam"):
- Workflow seam: ``route_issue_request`` and
  ``run_routed_workflow(settings, ..., gitlab=..., plan_graph=...,
  execute_graph=...)`` with in-memory fake transports and fake mode graphs.
- Application boundary: ``main()`` return code and stderr/stdout messages,
  with the routing entry injected.

Ordinary tests never call real GitLab or a model. The fake transport records
every call; creation, merge, approval, label-write, and user/role lookup
methods raise ``AssertionError`` if ever touched.
"""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import src.workflow as workflow_module
from src.config import Settings
from src.gitlab_client import GitLabAPIError
from src.workflow import (
    MODE_EXECUTE,
    MODE_PLAN,
    ROUTING_READ_CALLS,
    STATUS_EXECUTE_UNAVAILABLE,
    STATUS_INSPECTION_BLOCKED,
    STATUS_REJECTED,
    RoutingRejection,
    WorkflowUnavailableError,
    route_issue_request,
    run_routed_workflow,
)


def make_settings(output_dir: Path) -> Settings:
    return Settings(
        gitlab_url="http://gitlab.invalid",
        gitlab_token=None,
        gitlab_git_username="agent-bot",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="test-model",
        ollama_api_key="ollama",
        max_iterations=5,
        timeout_seconds=60,
        max_fix_attempts=3,
        check_timeout_seconds=60,
        execution_max_runtime_seconds=7200,
        setup_command=None,
        build_command=None,
        test_command=None,
        git_author_name="agent-bot",
        git_author_email="ai@agent.bot",
        output_dir=output_dir,
    )


def make_project() -> dict:
    return {
        "id": 101,
        "path": "demo",
        "name": "demo",
        "path_with_namespace": "group/demo",
        "default_branch": "main",
        "http_url_to_repo": "http://gitlab.invalid/group/demo.git",
        "web_url": "http://gitlab.invalid/group/demo",
    }


def make_issue(labels: list[str] | None = None) -> dict:
    return {
        "iid": 7,
        "title": "Add a greeting",
        "description": "Print a greeting on startup.",
        "labels": list(labels) if labels is not None else [],
        "web_url": "http://gitlab.invalid/group/demo/-/issues/7",
    }


def make_mr(
    iid: int = 9,
    *,
    state: str = "opened",
    labels: list[str] | None = None,
    source_branch: str = "feature/greeting",
    project_id: int = 101,
    target_project_id: int = 101,
    source_project_id: int | None = 101,
    target_branch: str = "main",
) -> dict:
    return {
        "iid": iid,
        "state": state,
        "labels": list(labels) if labels is not None else [],
        "source_branch": source_branch,
        "target_branch": target_branch,
        "project_id": project_id,
        "target_project_id": target_project_id,
        "source_project_id": source_project_id,
        "web_url": f"http://gitlab.invalid/group/demo/-/merge_requests/{iid}",
    }


class FakeGitLabTransport:
    """In-memory GitLab transport that records every call and never hits HTTP.

    Every write-shaped or identity-shaped method raises AssertionError: label
    routing must only read, and label-only approval must never look up users,
    roles, permissions, or approvals.
    """

    def __init__(
        self,
        project: dict,
        issue: dict,
        issue_mrs: list[dict] | None = None,
        notes: list[dict | str] | None = None,
        *,
        note_post_error: Exception | None = None,
        lose_note_response: bool = False,
        label_update_error: Exception | None = None,
        notes_page_size: int | None = None,
    ):
        self.project = copy.deepcopy(project)
        self.issue = copy.deepcopy(issue)
        self.issue_mrs = [copy.deepcopy(mr) for mr in (issue_mrs or [])]
        self._notes: list[dict] = []
        for note in notes or []:
            if isinstance(note, str):
                note = {"body": note}
            self._notes.append(copy.deepcopy(note))
        self._renumber_notes()
        self.calls: list[tuple[str, tuple]] = []
        # Failure injection for publication/label-transition tests:
        # - note_post_error: the POST fails BEFORE any append (total failure)
        # - lose_note_response: the append succeeds, then the POST raises
        #   (simulates a lost response; the dedup re-scan must find the note).
        #   GitLabAPIError is the real client's failure type after the server
        #   applied the POST, so the workflow's except clause sees it.
        # - label_update_error: every label transition raises
        self.note_post_error = note_post_error
        self.lose_note_response = lose_note_response
        self.label_update_error = label_update_error
        # When set, get_issue_notes paginates at this page size so multi-page
        # note reads are exercised offline.
        self.notes_page_size = notes_page_size

    def _renumber_notes(self) -> None:
        for index, note in enumerate(self._notes, start=1):
            note.setdefault("id", index)

    # --- read surface -----------------------------------------------------
    def get_project(self, project_id):
        self.calls.append(("get_project", (project_id,)))
        return copy.deepcopy(self.project)

    def get_issue(self, project_id, issue_iid):
        self.calls.append(("get_issue", (project_id, issue_iid)))
        return copy.deepcopy(self.issue)

    def get_issue_merge_requests(self, project_id, issue_iid):
        self.calls.append(("get_issue_merge_requests", (project_id, issue_iid)))
        return copy.deepcopy(self.issue_mrs)

    def get_issue_notes(self, project_id, issue_iid, *, per_page=50, max_pages=100):
        self.calls.append(("get_issue_notes", (project_id, issue_iid)))
        page_size = self.notes_page_size or per_page
        pages = [
            self._notes[i : i + page_size]
            for i in range(0, len(self._notes), page_size)
        ]
        return copy.deepcopy([note for page in (pages or [[]]) for note in page])

    # --- write surface (publication + label transitions; ticket 04) --------
    def add_issue_note(self, project_id, issue_iid, body):
        self.calls.append(("add_issue_note", (project_id, issue_iid, body)))
        if self.note_post_error is not None:
            raise self.note_post_error
        note = {"id": (max((n["id"] for n in self._notes), default=0) + 1), "body": body}
        self._notes.append(note)
        if self.lose_note_response:
            raise GitLabAPIError(
                "simulated lost response: note appended, then the POST raised"
            )
        return copy.deepcopy(note)

    def update_issue_labels(self, project_id, issue_iid, *, add_labels=(), remove_labels=()):
        self.calls.append(
            (
                "update_issue_labels",
                (project_id, issue_iid, tuple(sorted(add_labels)), tuple(sorted(remove_labels))),
            )
        )
        if self.label_update_error is not None:
            raise self.label_update_error
        labels = [label for label in self.issue["labels"] if label not in remove_labels]
        for label in add_labels:
            if label not in labels:
                labels.append(label)
        self.issue["labels"] = labels
        return copy.deepcopy(self.issue)

    # --- forbidden surface -------------------------------------------------
    def _forbidden(self, name: str):
        def raise_forbidden(*args, **kwargs):
            raise AssertionError(f"{name} must never be called by label routing")

        return raise_forbidden

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        forbidden_names = (
            "create", "merge", "approve", "put", "post", "delete", "update",
            "add_label", "remove_label", "set_labels", "user", "users",
            "permission", "permissions", "role", "approval",
        )
        if any(fragment in name.lower() for fragment in forbidden_names):
            return self._forbidden(name)
        raise AttributeError(name)

    # --- assertions helpers -------------------------------------------------
    def read_calls(self) -> list[str]:
        return [name for name, _ in self.calls]

    def assert_only_reads(self):
        unexpected = [name for name in self.read_calls() if name not in ROUTING_READ_CALLS]
        assert not unexpected, f"non-read transport calls occurred: {unexpected}"


class FakeModeGraph:
    """Injectable stand-in for a compiled mode graph; records its state."""

    def __init__(self, status: str = "fake_mode_ran"):
        self.status = status
        self.states: list[dict] = []

    def invoke(self, state: dict) -> dict:
        self.states.append(copy.deepcopy(state))
        return {**state, "status": self.status}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path / "runs")


def route(
    transport: FakeGitLabTransport,
    *,
    project_id: str = "group/demo",
    issue_iid: int = 7,
    branch_override: str | None = None,
) -> object:
    return route_issue_request(
        gitlab=transport,
        project_id=project_id,
        issue_iid=issue_iid,
        branch_override=branch_override,
    )


class TestLabelRoutingPlan:
    def test_plan_trigger_selects_plan_with_default_baseline(self, settings):
        transport = FakeGitLabTransport(make_project(), make_issue(["agent::plan"]))

        result = route(transport)

        assert result.mode == MODE_PLAN
        assert result.baseline_branch == "main"
        assert result.issue_agent_labels == ["agent::plan"]
        # Plan also reads the related-MR context for the baseline matrix
        # (implementation plan §3.3); nothing else is read or written.
        assert transport.read_calls() == [
            "get_project",
            "get_issue",
            "get_issue_merge_requests",
        ]
        transport.assert_only_reads()

    def test_branch_override_wins_for_plan_baseline(self, settings):
        transport = FakeGitLabTransport(make_project(), make_issue(["agent::plan"]))

        result = route(transport, branch_override="release/1.4")

        assert result.mode == MODE_PLAN
        assert result.baseline_branch == "release/1.4"
        # The explicit override wins outright: related MRs are not even read.
        assert transport.read_calls() == ["get_project", "get_issue"]
        transport.assert_only_reads()

    def test_unrelated_labels_are_preserved_and_ignored(self, settings):
        labels = ["agent::plan", "priority::high", "backend", "agent::prototype"]
        transport = FakeGitLabTransport(make_project(), make_issue(labels))

        result = route(transport)

        assert result.mode == MODE_PLAN
        # The stored issue labels are untouched (nothing was ever written).
        assert transport.issue["labels"] == labels

    def test_agent_runner_style_work_is_never_started(self, settings):
        # The routing boundary must not import or start any coding work; the
        # only observable effect is the routing result itself.
        transport = FakeGitLabTransport(make_project(), make_issue(["agent::plan"]))
        result = route(transport)
        assert result.mode == MODE_PLAN
        assert result.mr is None


class TestPlanBaselineMatrix:
    """Baseline selection matrix for Plan (implementation plan §3.3)."""

    def test_no_related_mr_uses_project_default_branch(self, settings):
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::plan"]), issue_mrs=[]
        )

        result = route(transport)

        assert result.mode == MODE_PLAN
        assert result.baseline_branch == "main"

    def test_single_related_mr_uses_its_target_branch(self, settings):
        mr = make_mr(iid=9, state="merged", target_branch="develop")
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::plan"]), issue_mrs=[mr]
        )

        result = route(transport)

        assert result.baseline_branch == "develop"

    def test_single_closed_mr_with_target_wins_over_default(self, settings):
        mr = make_mr(iid=4, state="closed", target_branch="release/1.4")
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::plan"]), issue_mrs=[mr]
        )

        result = route(transport)

        assert result.baseline_branch == "release/1.4"

    def test_several_mrs_with_exactly_one_open_use_open_target(self, settings):
        merged = make_mr(iid=3, state="merged", target_branch="release/1.0")
        open_mr = make_mr(iid=9, state="opened", target_branch="develop")
        merged2 = make_mr(iid=5, state="merged", target_branch="release/1.2")
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::plan"]), issue_mrs=[merged, open_mr, merged2]
        )

        result = route(transport)

        assert result.baseline_branch == "develop"

    def test_multiple_open_mrs_reject_and_name_branch_flag(self, settings):
        mrs = [
            make_mr(iid=9, state="opened", target_branch="develop"),
            make_mr(iid=10, state="opened", target_branch="release/1.4"),
        ]
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::plan"]), issue_mrs=mrs
        )

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        message = str(excinfo.value)
        assert "--branch" in message
        assert "!9" in message and "!10" in message
        assert excinfo.value.mode == MODE_PLAN
        assert excinfo.value.status == STATUS_REJECTED

    def test_several_closed_mrs_with_disagreeing_targets_reject(self, settings):
        mrs = [
            make_mr(iid=3, state="merged", target_branch="release/1.0"),
            make_mr(iid=5, state="closed", target_branch="release/1.2"),
        ]
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::plan"]), issue_mrs=mrs
        )

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        message = str(excinfo.value)
        assert "--branch" in message
        assert "release/1.0" in message and "release/1.2" in message
        assert excinfo.value.mode == MODE_PLAN

    def test_several_closed_mrs_with_agreeing_target_use_it(self, settings):
        mrs = [
            make_mr(iid=3, state="merged", target_branch="release/1.2"),
            make_mr(iid=5, state="closed", target_branch="release/1.2"),
        ]
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::plan"]), issue_mrs=mrs
        )

        result = route(transport)

        assert result.baseline_branch == "release/1.2"

    def test_single_mr_without_target_branch_rejects(self, settings):
        mr = make_mr(iid=9, state="opened")
        del mr["target_branch"]
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::plan"]), issue_mrs=[mr]
        )

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        message = str(excinfo.value)
        assert "--branch" in message
        assert "!9" in message
        assert excinfo.value.mode == MODE_PLAN


class TestLabelRoutingExecute:
    def test_approved_with_single_implement_mr_selects_execute(self, settings):
        mr = make_mr(source_branch="feature/greeting", labels=["agent::implement"])
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[mr]
        )

        result = route(transport)

        assert result.mode == MODE_EXECUTE
        assert result.mr["iid"] == 9
        assert result.mr["source_branch"] == "feature/greeting"
        assert result.open_mrs == [result.mr]
        # The related-MR read happens through the documented relationship
        # endpoint; no branch-filtered lookup and no creation path exist.
        assert transport.read_calls() == [
            "get_project",
            "get_issue",
            "get_issue_merge_requests",
        ]
        transport.assert_only_reads()

    def test_approved_alone_rejects_when_no_related_mr(self, settings):
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[]
        )

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        message = str(excinfo.value)
        assert "agent::approved" in message
        assert "agent::implement" in message
        assert "merge request" in message.lower()
        assert excinfo.value.mode == MODE_EXECUTE
        assert excinfo.value.status == STATUS_REJECTED
        transport.assert_only_reads()

    def test_approved_rejects_when_several_open_mrs(self, settings):
        mrs = [
            make_mr(iid=9, labels=["agent::implement"]),
            make_mr(iid=10, labels=["agent::implement"], source_branch="feature/other"),
        ]
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=mrs
        )

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        message = str(excinfo.value)
        assert "2 open merge requests" in message
        assert "!9" in message and "!10" in message
        assert excinfo.value.mode == MODE_EXECUTE

    def test_approved_rejects_when_open_mr_misses_implement(self, settings):
        mr = make_mr(labels=["backend"])
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[mr]
        )

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        message = str(excinfo.value)
        assert "!9" in message
        assert "agent::implement" in message
        assert "backend" in message
        assert excinfo.value.mode == MODE_EXECUTE

    @pytest.mark.parametrize("mr_label", ["agent::in-progress", "agent::needs-review"])
    def test_approved_rejects_in_progress_or_review_mr(self, settings, mr_label):
        mr = make_mr(labels=[mr_label])
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[mr]
        )

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        assert mr_label in str(excinfo.value)
        assert excinfo.value.mode == MODE_EXECUTE

    def test_approved_rejects_conflicting_mr_labels(self, settings):
        mr = make_mr(labels=["agent::implement", "agent::in-progress"])
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[mr]
        )

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        message = str(excinfo.value)
        assert "conflicting agent labels" in message
        assert "agent::implement" in message and "agent::in-progress" in message

    def test_approved_ignores_closed_related_mrs(self, settings):
        closed = make_mr(iid=5, state="closed", labels=["agent::needs-review"])
        opened = make_mr(iid=9, labels=["agent::implement"])
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[closed, opened]
        )

        result = route(transport)

        assert result.mode == MODE_EXECUTE
        assert result.mr["iid"] == 9

    def test_approved_rejects_cross_project_mr(self, settings):
        mr = make_mr(
            project_id=999, target_project_id=999, source_project_id=999
        )
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[mr]
        )

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        message = str(excinfo.value)
        assert "999" in message and "101" in message
        assert "same" in message.lower() or "within" in message.lower()

    def test_approved_rejects_fork_mr_targeting_issue_project(self, settings):
        # A fork MR targets the Issue project but its source lives elsewhere;
        # all project identities on the MR must equal the Issue project.
        mr = make_mr(
            project_id=101,
            target_project_id=101,
            source_project_id=999,
        )
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[mr]
        )

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        message = str(excinfo.value)
        assert "999" in message and "101" in message
        assert "fork" in message.lower()
        assert excinfo.value.mode == MODE_EXECUTE

    def test_approved_rejects_mr_with_missing_project_identity(self, settings):
        # A missing project identity must reject, never pass silently.
        mr = make_mr()
        del mr["source_project_id"]
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[mr]
        )

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        assert "source_project_id" in str(excinfo.value)
        assert excinfo.value.mode == MODE_EXECUTE


class TestLabelRoutingRejections:
    @pytest.mark.parametrize(
        "labels",
        [
            [],
            ["agent::in-progress"],
            ["agent::needs-review"],
            ["agent::plan", "agent::approved"],
            ["agent::approved", "agent::needs-review"],
            ["agent::plan", "agent::in-progress", "agent::needs-review"],
        ],
    )
    def test_invalid_issue_states_reject_with_actionable_message(self, settings, labels):
        transport = FakeGitLabTransport(make_project(), make_issue(labels))

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        rejection = excinfo.value
        assert rejection.status == STATUS_REJECTED
        assert rejection.mode is None
        message = str(rejection)
        for label in labels:
            assert label in message
        assert "agent::plan" in message  # what to do next is always named
        # No MR read is even attempted for invalid Issue states.
        assert transport.read_calls() == ["get_project", "get_issue"]
        transport.assert_only_reads()

    def test_unrecognized_agent_labels_are_reported_not_mutated(self, settings):
        labels = ["agent::queued", "agent::blocked"]
        transport = FakeGitLabTransport(make_project(), make_issue(labels))

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        assert "agent::queued" in str(excinfo.value)
        assert "left untouched" in str(excinfo.value)
        assert transport.issue["labels"] == labels

    def test_empty_labels_name_every_valid_trigger(self, settings):
        transport = FakeGitLabTransport(make_project(), make_issue([]))

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport)

        message = str(excinfo.value)
        assert "agent::plan" in message
        assert "agent::approved" in message
        assert "agent::implement" in message

    def test_branch_override_rejected_for_execute(self, settings):
        mr = make_mr(labels=["agent::implement"])
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[mr]
        )

        with pytest.raises(RoutingRejection) as excinfo:
            route(transport, branch_override="release/1.4")

        message = str(excinfo.value)
        assert "--branch" in message
        assert "Plan" in message
        assert excinfo.value.mode == MODE_EXECUTE
        # The rejection happens before any MR read.
        assert transport.read_calls() == ["get_project", "get_issue"]


class TestRoutedWorkflowDispatch:
    def test_plan_dispatch_invokes_plan_graph_with_routing_state(self, settings):
        transport = FakeGitLabTransport(make_project(), make_issue(["agent::plan"]))
        plan_graph = FakeModeGraph()

        result = run_routed_workflow(
            settings,
            project_id="group/demo",
            issue_iid=7,
            gitlab=transport,
            plan_graph=plan_graph,
        )

        assert result["status"] == "fake_mode_ran"
        assert len(plan_graph.states) == 1
        state = plan_graph.states[0]
        assert state["mode"] == MODE_PLAN
        assert state["status"] == "routed"
        assert state["baseline_branch"] == "main"
        assert state["issue"]["iid"] == 7
        assert state["project"]["id"] == 101
        assert state["issue_agent_labels"] == ["agent::plan"]
        assert state["verification_hints"] == {
            "setup_command": None,
            "build_command": None,
            "test_command": None,
        }

    def test_plan_dispatch_passes_verification_hints(self, tmp_path):
        settings = replace(
            make_settings(tmp_path / "runs"),
            setup_command="pip install -e .",
            build_command="make build",
            test_command="pytest -q",
        )
        transport = FakeGitLabTransport(make_project(), make_issue(["agent::plan"]))
        plan_graph = FakeModeGraph()

        run_routed_workflow(
            settings, project_id="group/demo", issue_iid=7,
            gitlab=transport, plan_graph=plan_graph,
        )

        assert plan_graph.states[0]["verification_hints"] == {
            "setup_command": "pip install -e .",
            "build_command": "make build",
            "test_command": "pytest -q",
        }

    def test_execute_dispatch_invokes_execute_graph_with_mr_state(self, settings):
        mr = make_mr(source_branch="feature/greeting", labels=["agent::implement"])
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[mr]
        )
        execute_graph = FakeModeGraph()

        result = run_routed_workflow(
            settings,
            project_id="group/demo",
            issue_iid=7,
            gitlab=transport,
            execute_graph=execute_graph,
        )

        assert result["status"] == "fake_mode_ran"
        state = execute_graph.states[0]
        assert state["mode"] == MODE_EXECUTE
        assert state["mr"]["iid"] == 9
        assert state["source_branch"] == "feature/greeting"
        # Execute state carries the MR source branch, not a Plan baseline.
        assert "baseline_branch" not in state

    def test_default_plan_graph_blocks_clearly_without_worker_endpoint(
        self, settings
    ):
        transport = FakeGitLabTransport(make_project(), make_issue(["agent::plan"]))

        result = run_routed_workflow(
            settings, project_id="group/demo", issue_iid=7, gitlab=transport
        )

        # The default plan graph now performs setup validation first: with no
        # planner worker endpoint configured it fails clearly (blocked) before
        # any clone, inspection, edit, or publication happens.
        assert result["status"] == STATUS_INSPECTION_BLOCKED
        assert "PLANNER_AGENT_SERVER_URL" in result["error_message"]
        # No product work: only routing reads happened.
        transport.assert_only_reads()

    def test_default_execute_graph_fails_clearly_as_infrastructure_error(self, settings):
        mr = make_mr(labels=["agent::implement"])
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[mr]
        )

        with pytest.raises(WorkflowUnavailableError) as excinfo:
            run_routed_workflow(
                settings, project_id="group/demo", issue_iid=7, gitlab=transport
            )

        assert excinfo.value.status == STATUS_EXECUTE_UNAVAILABLE
        assert excinfo.value.mode == MODE_EXECUTE
        assert "not implemented" in str(excinfo.value)
        transport.assert_only_reads()

    def test_rejection_prevents_dispatch_entirely(self, settings):
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::in-progress"])
        )
        plan_graph = FakeModeGraph()
        execute_graph = FakeModeGraph()

        with pytest.raises(RoutingRejection):
            run_routed_workflow(
                settings,
                project_id="group/demo",
                issue_iid=7,
                gitlab=transport,
                plan_graph=plan_graph,
                execute_graph=execute_graph,
            )

        assert plan_graph.states == []
        assert execute_graph.states == []
        transport.assert_only_reads()


class TestApplicationBoundary:
    def _run_main(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        argv: list[str],
        *,
        transport: FakeGitLabTransport | None = None,
    ) -> tuple[int, str, str, FakeGitLabTransport | None]:
        transports: list[FakeGitLabTransport] = []
        # Capture the real entry before patching so the fake can delegate.
        real_run_routed = workflow_module.run_routed_workflow

        def fake_run_routed(settings_from_main, **kwargs):
            assert transport is not None
            transports.append(transport)
            return real_run_routed(settings_from_main, gitlab=transport, **kwargs)

        monkeypatch.setattr(workflow_module, "run_routed_workflow", fake_run_routed)
        monkeypatch.setattr(sys, "argv", argv)
        import main as main_module

        code = main_module.main()
        captured = capsys.readouterr()
        return code, captured.out, captured.err, (transports[0] if transports else None)

    def base_argv(self, tmp_path: Path) -> list[str]:
        return [
            "main.py",
            "--project",
            "group/demo",
            "--issue",
            "7",
            "--output-dir",
            str(tmp_path / "runs"),
        ]

    def test_plan_request_without_worker_endpoint_blocks_with_exit_1(
        self, tmp_path, monkeypatch, capsys
    ):
        transport = FakeGitLabTransport(make_project(), make_issue(["agent::plan"]))

        code, out, err, used = self._run_main(
            monkeypatch, capsys, self.base_argv(tmp_path), transport=transport
        )

        assert code == 1
        # The actionable message names the missing configuration.
        assert "PLANNER_AGENT_SERVER_URL" in err
        report = json.loads((tmp_path / "runs" / "poc-result.json").read_text())
        assert report["mode"] == "plan"
        assert report["status"] == "inspection_blocked"
        assert "PLANNER_AGENT_SERVER_URL" in report["message"]
        assert used.read_calls() == [
            "get_project",
            "get_issue",
            "get_issue_merge_requests",
        ]
        used.assert_only_reads()

    def test_execute_request_reports_unavailable_downstream_with_exit_1(
        self, tmp_path, monkeypatch, capsys
    ):
        transport = FakeGitLabTransport(
            make_project(),
            make_issue(["agent::approved"]),
            issue_mrs=[make_mr(labels=["agent::implement"])],
        )

        code, out, err, used = self._run_main(
            monkeypatch, capsys, self.base_argv(tmp_path), transport=transport
        )

        assert code == 1
        assert "not implemented" in err
        report = json.loads((tmp_path / "runs" / "poc-result.json").read_text())
        assert report["mode"] == "execute"
        assert report["status"] == "execute_unavailable"
        assert "get_issue_merge_requests" in used.read_calls()
        used.assert_only_reads()

    def test_rejected_invocation_exits_2_with_actionable_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::in-progress"])
        )

        code, out, err, used = self._run_main(
            monkeypatch, capsys, self.base_argv(tmp_path), transport=transport
        )

        assert code == 2
        assert "REJECTED:" in err
        assert "agent::in-progress" in err
        report = json.loads((tmp_path / "runs" / "poc-result.json").read_text())
        assert report["mode"] is None
        assert report["status"] == "rejected"
        assert "agent::in-progress" in report["message"]
        # No product edits, no publication, no label writes on rejection.
        used.assert_only_reads()

    def test_execute_preflight_rejection_records_execute_mode(
        self, tmp_path, monkeypatch, capsys
    ):
        transport = FakeGitLabTransport(
            make_project(), make_issue(["agent::approved"]), issue_mrs=[]
        )

        code, out, err, _ = self._run_main(
            monkeypatch, capsys, self.base_argv(tmp_path), transport=transport
        )

        assert code == 2
        assert "agent::implement" in err
        report = json.loads((tmp_path / "runs" / "poc-result.json").read_text())
        assert report["mode"] == "execute"
        assert report["status"] == "rejected"

    def test_branch_override_accepted_for_plan(
        self, tmp_path, monkeypatch, capsys
    ):
        seen_overrides: list[str | None] = []
        real_run_routed = workflow_module.run_routed_workflow

        def fake_run_routed(settings_from_main, **kwargs):
            seen_overrides.append(kwargs.get("branch_override"))
            transport = FakeGitLabTransport(make_project(), make_issue(["agent::plan"]))
            return real_run_routed(settings_from_main, gitlab=transport, **kwargs)

        monkeypatch.setattr(workflow_module, "run_routed_workflow", fake_run_routed)
        monkeypatch.setattr(
            sys, "argv", self.base_argv(tmp_path) + ["--branch", "release/1.4"]
        )
        import main as main_module

        code = main_module.main()
        captured = capsys.readouterr()

        assert code == 1  # inspection blocked on missing worker endpoint; routing succeeded
        assert seen_overrides == ["release/1.4"]
        report = json.loads((tmp_path / "runs" / "poc-result.json").read_text())
        assert report["branch_override"] == "release/1.4"
        assert report["status"] == "inspection_blocked"

    def test_branch_override_rejected_for_execute(
        self, tmp_path, monkeypatch, capsys
    ):
        transport = FakeGitLabTransport(
            make_project(),
            make_issue(["agent::approved"]),
            issue_mrs=[make_mr(labels=["agent::implement"])],
        )

        argv = self.base_argv(tmp_path) + ["--branch", "release/1.4"]
        code, out, err, used = self._run_main(
            monkeypatch, capsys, argv, transport=transport
        )

        assert code == 2
        assert "--branch" in err
        assert "Plan" in err
        used.assert_only_reads()

    @pytest.mark.parametrize(
        "obsolete_flag",
        ["--target-branch", "--mr-title", "--mr-description", "--draft-mr"],
    )
    def test_obsolete_flags_produce_actionable_exit_2(
        self, tmp_path, monkeypatch, capsys, obsolete_flag
    ):
        argv = self.base_argv(tmp_path) + [obsolete_flag, "unused-value"]

        monkeypatch.setattr(sys, "argv", argv)
        import main as main_module

        with pytest.raises(SystemExit) as excinfo:
            main_module.main()

        err = capsys.readouterr().err
        assert excinfo.value.code == 2
        assert obsolete_flag in err
        assert "no longer creates" in err
        assert "agent::implement" in err

    def test_other_unknown_flags_still_report_unrecognized(
        self, tmp_path, monkeypatch, capsys
    ):
        argv = self.base_argv(tmp_path) + ["--mode", "execute"]

        monkeypatch.setattr(sys, "argv", argv)
        import main as main_module

        with pytest.raises(SystemExit) as excinfo:
            main_module.main()

        assert excinfo.value.code == 2
        assert "unrecognized arguments" in capsys.readouterr().err

    def test_whitespace_branch_override_rejected(
        self, tmp_path, monkeypatch, capsys
    ):
        argv = self.base_argv(tmp_path) + ["--branch", "   "]

        monkeypatch.setattr(sys, "argv", argv)
        import main as main_module

        code = main_module.main()
        captured = capsys.readouterr()

        assert code == 2
        assert "--branch" in captured.err

    def test_no_actor_or_role_lookup_for_label_only_approval(
        self, tmp_path, monkeypatch, capsys
    ):
        # The fake transport's user/role/permission/approval methods raise
        # AssertionError on any call; a successful routing proves none ran.
        transport = FakeGitLabTransport(
            make_project(),
            make_issue(["agent::approved"]),
            issue_mrs=[make_mr(labels=["agent::implement"])],
        )

        code, out, err, used = self._run_main(
            monkeypatch, capsys, self.base_argv(tmp_path), transport=transport
        )

        assert code == 1  # routed to Execute; graph unavailable
        used.assert_only_reads()


class TestEnvironmentSettings:
    def _run_main(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        argv: list[str],
    ) -> tuple[int, str, str]:
        monkeypatch.setattr(sys, "argv", argv)
        import main as main_module

        code = main_module.main()
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    def base_argv(self, tmp_path: Path) -> list[str]:
        return [
            "main.py",
            "--project",
            "group/demo",
            "--issue",
            "7",
            "--output-dir",
            str(tmp_path / "runs"),
        ]

    def test_malformed_env_integer_gives_actionable_error(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("OPENHANDS_MAX_ITERATIONS", "not-a-number")

        code, out, err = self._run_main(monkeypatch, capsys, self.base_argv(tmp_path))

        assert code == 2
        assert "ERROR:" in err
        assert "OPENHANDS_MAX_ITERATIONS" in err
        assert "Traceback" not in err

    def test_malformed_execution_runtime_gives_actionable_error(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("EXECUTION_MAX_RUNTIME_SECONDS", "soon")

        code, out, err = self._run_main(monkeypatch, capsys, self.base_argv(tmp_path))

        assert code == 2
        assert "EXECUTION_MAX_RUNTIME_SECONDS" in err
        assert "Traceback" not in err

    @pytest.mark.parametrize(
        ("env_name", "env_value", "minimum_hint"),
        [
            ("OPENHANDS_MAX_ITERATIONS", "0", ">= 1"),
            ("OPENHANDS_TIMEOUT_SECONDS", "0", ">= 1"),
            ("CHECK_TIMEOUT_SECONDS", "0", ">= 1"),
            ("EXECUTION_MAX_RUNTIME_SECONDS", "0", ">= 1"),
            ("MAX_FIX_ATTEMPTS", "-1", ">= 0"),
        ],
    )
    def test_below_minimum_env_integer_is_rejected_not_clamped(
        self, tmp_path, monkeypatch, capsys, env_name, env_value, minimum_hint
    ):
        monkeypatch.setenv(env_name, env_value)

        code, out, err = self._run_main(monkeypatch, capsys, self.base_argv(tmp_path))

        assert code == 2
        assert "ERROR:" in err
        assert env_name in err
        assert minimum_hint in err
        assert f"got '{env_value}'" in err
        assert "Traceback" not in err

    def test_negative_max_fix_attempts_gives_actionable_error(
        self, tmp_path, monkeypatch, capsys
    ):
        argv = self.base_argv(tmp_path) + ["--max-fix-attempts", "-1"]

        code, out, err = self._run_main(monkeypatch, capsys, argv)

        assert code == 2
        assert "ERROR:" in err
        assert "--max-fix-attempts" in err
        assert "Traceback" not in err


class TestSettingsDefaults:
    ENV_NAMES = [
        "OPENHANDS_MAX_ITERATIONS",
        "OPENHANDS_TIMEOUT_SECONDS",
        "MAX_FIX_ATTEMPTS",
        "CHECK_TIMEOUT_SECONDS",
        "EXECUTION_MAX_RUNTIME_SECONDS",
    ]

    def test_agreed_default_limits(self, monkeypatch, tmp_path):
        for name in self.ENV_NAMES:
            monkeypatch.delenv(name, raising=False)

        settings = Settings.from_env(output_dir=tmp_path)

        assert settings.max_iterations == 60
        assert settings.timeout_seconds == 1800
        assert settings.check_timeout_seconds == 1200
        assert settings.max_fix_attempts == 3
        assert settings.execution_max_runtime_seconds == 7200

    def test_malformed_value_raises_actionable_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EXECUTION_MAX_RUNTIME_SECONDS", "two hours")

        with pytest.raises(ValueError) as excinfo:
            Settings.from_env(output_dir=tmp_path)

        assert "EXECUTION_MAX_RUNTIME_SECONDS" in str(excinfo.value)
        assert "7200" in str(excinfo.value)  # the default is named in the message
