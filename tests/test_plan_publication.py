"""Plan-publication integration tests (ticket 04, WP-C).

Fully offline end-to-end runs of the Plan workflow through the real seams:
``route_issue_request`` (baseline matrix) → ``build_plan_graph`` (in-progress
transition, read-only inspection, synthesis, publication under the Issue
lock, needs-review transition) → truthful terminal status. The GitLab side
is the shared ``FakeGitLabTransport`` (one instance shared by routing and the
graph) and the Planner role runs through the controlled ``FakePlannerTransport``
(no Docker, no LLM, no GitLab, no network); the fixture repository is a local
bare Git repo, so the real ``git_ops.clone_repository`` still runs.

Checkbox coverage (docs/p0/specs/issues/04-publish-the-first-complete-execution-plan.md):
- checkbox 1: multi-page comment reads reach synthesis and the published plan
- checkbox 2: full baseline matrix through the complete workflow
- checkbox 5: one append-only plan version per publication UID, dedup before
  POST and after a lost response, version allocation across existing records
- checkbox 6: label transitions via add/remove, unrelated labels survive,
  needs-review failure recorded as a distinct outcome without erasing the
  published plan
- checkbox 7: no setup/Build/Test execution, no product edits, no branch/
  commit/push/MR creation anywhere in the run (the fake transport's unknown
  write methods raise AssertionError, so any such call fails the test)
- checkbox 8: acceptance case — a complete executable plan for a legacy
  repository without AGENTS.md or configured commands

Malformed planner output (one correction retry, then truthful failure with
nothing published) and the application-boundary report/exit-code semantics
(executable vs clarification-required both exit 0, synthesis/publication
failures exit 1) close out the WP-C surface.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import src.workflow as workflow_module
from src.gitlab_client import GitLabAPIError
from src.planner_runner import PLAN_SYNTHESIZER_ROLE_HEADER
from src.planning import parse_plan_note
from src.workflow import (
    STATUS_INSPECTION_BLOCKED,
    STATUS_PLAN_NEEDS_CLARIFICATION,
    STATUS_PLAN_PUBLISHED,
    STATUS_PLAN_SYNTHESIS_FAILED,
    RoutingRejection,
    build_plan_graph,
    run_routed_workflow,
)
from src.workspace import OperationContext
from tests.test_plan_inspection import (
    FakePlannerTransport,
    _git,
    make_inspection_settings,
    make_plan_content,
    make_project_with_origin,
)
from tests.test_planning import make_plan, plan_note_body
from tests.test_workflow_routing import (
    FakeGitLabTransport,
    make_issue,
    make_mr,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

PROJECT_ID = 101
ISSUE_IID = 7

# A requirement recorded only on the third page of comments (page size 1).
REQUIREMENT_MARKER = "LOCALIZED-GREETING-REQ-MARKER"


@pytest.fixture
def origin_repo(tmp_path: Path) -> Path:
    """A local bare Git repository used as the fixture 'GitLab' origin.

    A deliberate legacy-repo shape: no AGENTS.md, no configured commands —
    the acceptance case (checkbox 8) publishes a plan for it anyway. Branches
    `main` and `release/1.4` exist so the baseline matrix can select either.
    """
    work = tmp_path / "origin-work"
    work.mkdir()
    (work / "src").mkdir()
    (work / "src" / "app.py").write_text("print('hello')\n")
    (work / "docs").mkdir()
    (work / "docs" / "guide.md").write_text("# guide\n")
    (work / "requirements.txt").write_text("requests>=2\n")
    (work / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    (work / ".gitlab-ci.yml").write_text("test:\n  script: pytest\n")
    _git(["init", "-b", "main"], cwd=work)
    _git(["add", "."], cwd=work)
    _git(["commit", "-m", "initial"], cwd=work)

    bare = tmp_path / "origin.git"
    _git(["clone", "--bare", str(work), str(bare)])
    release_work = tmp_path / "release-work"
    _git(["init", "-b", "release/1.4", str(release_work)])
    (release_work / "release.txt").write_text("release notes\n")
    _git(["add", "."], cwd=release_work)
    _git(["commit", "-m", "release branch"], cwd=release_work)
    _git(["push", str(bare), "release/1.4"], cwd=release_work)
    return bare


def seeded_plan_note(*, version: int, publication_uid: str, **overrides) -> dict:
    """A historical plan comment in the fixture Issue's identity (id auto)."""
    plan = make_plan(
        project_id=PROJECT_ID,
        project_path="group/demo",
        issue_iid=ISSUE_IID,
        version=version,
        plan_id=f"PLAN-v{version}",
        publication_uid=publication_uid,
        **overrides,
    )
    return {"body": plan_note_body(plan)}


def run_plan_workflow(
    origin: Path,
    settings,
    transport,
    *,
    gitlab: FakeGitLabTransport | None = None,
    labels: list[str] | None = None,
    mrs: list[dict] | None = None,
    notes: list[dict | str] | None = None,
    notes_page_size: int | None = None,
    branch_override: str | None = None,
    **kwargs,
):
    """Run the FULL workflow (routing + plan graph) and return (result, gitlab).

    One FakeGitLabTransport instance is shared by routing AND the plan graph,
    so label transitions, comment reads, and publication all land in the same
    recorded state. Extra ``kwargs`` forward to ``build_plan_graph``
    (e.g. ``operation_factory``).
    """
    if gitlab is None:
        gitlab = FakeGitLabTransport(
            make_project_with_origin(origin),
            make_issue(labels if labels is not None else ["agent::plan"]),
            issue_mrs=list(mrs or []),
            notes=notes,
            notes_page_size=notes_page_size,
        )
    graph = build_plan_graph(settings, planner_transport=transport, gitlab=gitlab, **kwargs)
    result = run_routed_workflow(
        settings,
        project_id="group/demo",
        issue_iid=ISSUE_IID,
        branch_override=branch_override,
        gitlab=gitlab,
        plan_graph=graph,
    )
    return result, gitlab


def note_posts(gitlab: FakeGitLabTransport) -> list[tuple]:
    return [call for call in gitlab.calls if call[0] == "add_issue_note"]


def label_writes(gitlab: FakeGitLabTransport) -> list[tuple]:
    return [call for call in gitlab.calls if call[0] == "update_issue_labels"]


class MarkerEchoPlanner(FakePlannerTransport):
    """Echoes a requirement marker from the synthesis prompt into the plan.

    Proves the marker really flowed issue → prompt → validated plan →
    published note (end to end), not merely that the prompt contained it.
    """

    def run(self, *, prompt, repo_dir, worker_working_dir, operation, settings):
        result = super().run(
            prompt=prompt,
            repo_dir=repo_dir,
            worker_working_dir=worker_working_dir,
            operation=operation,
            settings=settings,
        )
        if PLAN_SYNTHESIZER_ROLE_HEADER in prompt and REQUIREMENT_MARKER in prompt:
            content = json.loads(result["summary"])
            content["requirement_understanding"] = (
                f"Understood requirement includes {REQUIREMENT_MARKER}"
            )
            result["summary"] = json.dumps(content, sort_keys=True)
        return result


class NeedsReviewTransitionFailureTransport(FakeGitLabTransport):
    """Fails ONLY the second label transition (in-progress → needs-review).

    The first transition (agent::plan → agent::in-progress) must succeed so
    the run reaches publication; the second one simulates a GitLab API error
    after the plan note is already posted.
    """

    def update_issue_labels(self, project_id, issue_iid, *, add_labels=(), remove_labels=()):
        prior_label_writes = [call for call in self.calls if call[0] == "update_issue_labels"]
        if len(prior_label_writes) == 1:
            self.calls.append(
                (
                    "update_issue_labels",
                    (
                        project_id,
                        issue_iid,
                        tuple(sorted(add_labels)),
                        tuple(sorted(remove_labels)),
                    ),
                )
            )
            raise GitLabAPIError(
                "simulated GitLab 500 on the agent::needs-review transition"
            )
        return super().update_issue_labels(
            project_id, issue_iid, add_labels=add_labels, remove_labels=remove_labels
        )


class LabelMutationTransport(FakeGitLabTransport):
    """Swaps the Issue labels on the SECOND get_issue call.

    Routing reads the Issue WITHOUT the lock (first read); activate_issue
    re-reads it UNDER the Issue lock (second read) — that second read sees
    the mutation a human made between routing and the transition (e.g. an
    agent::approved label added).
    """

    def __init__(self, *args, mutated_labels: list[str], **kwargs):
        super().__init__(*args, **kwargs)
        self._mutated_labels = list(mutated_labels)

    def get_issue(self, project_id, issue_iid):
        prior_reads = [name for name, _ in self.calls if name == "get_issue"]
        if prior_reads:  # this is the second+ read: labels mutated meanwhile
            self.issue["labels"] = list(self._mutated_labels)
        return super().get_issue(project_id, issue_iid)


# ---------------------------------------------------------------------------
# Checkbox 8 — acceptance case: legacy repository, one complete plan
# ---------------------------------------------------------------------------


class TestAcceptanceLegacyRepository:
    def test_legacy_repo_publishes_one_complete_executable_plan(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)  # no configured commands
        transport = FakePlannerTransport()

        result, gitlab = run_plan_workflow(
            origin_repo,
            settings,
            transport,
            labels=["agent::plan", "priority::high", "backend"],
        )

        # One complete executable plan, published exactly once.
        assert result["status"] == STATUS_PLAN_PUBLISHED
        publication = result["plan_publication"]
        assert publication["executable"] is True
        assert publication["plan_id"] == "PLAN-v1"
        assert publication["version"] == 1
        assert publication["unresolved_questions_count"] == 0
        assert publication["deduplicated"] is False
        assert publication["note_id"] is not None
        posts = note_posts(gitlab)
        assert len(posts) == 1
        body = posts[0][1][2]
        # Complete plan content: plan identity, both tasks with semantic
        # modules, proposed/unverified hints (in the embedded JSON payload),
        # and NO allowed_paths (ADR 0003: modules are not file paths).
        assert "PLAN-v1" in body
        assert "TASK-001" in body and "TASK-002" in body
        assert "PROPOSED" in body and "UNVERIFIED" in body
        assert "allowed_paths" not in body
        # Label path agent::plan → in-progress → needs-review with unrelated
        # labels surviving untouched.
        assert gitlab.issue["labels"] == ["priority::high", "backend", "agent::needs-review"]
        # Read/write surface stays within the documented Plan seam; anything
        # else (branch/MR creation, merge, approve, push, user lookups) would
        # have raised AssertionError inside the fake transport already.
        assert {name for name, _ in gitlab.calls} <= {
            "get_project",
            "get_issue",
            "get_issue_merge_requests",
            "get_issue_notes",
            "add_issue_note",
            "update_issue_labels",
        }
        # No setup/Build/Test execution: the plan's hints remain proposed and
        # unverified, and inspection evidence proves nothing was run.
        plan = result["plan"]
        assert plan["validation_hints"][0]["origin"] == "PROPOSED"
        assert plan["validation_hints"][0]["confirmation"] == "UNVERIFIED"
        evidence = json.loads(Path(result["evidence_path"]).read_text())
        for kind in ("build", "test"):
            hint = evidence["verification_hints"].get(kind) or {}
            if hint:
                assert hint["status"] == "UNVERIFIED"
        # Synthesis evidence is recorded outside the checkout.
        assert Path(result["plan_synthesis_path"]).is_file()


# ---------------------------------------------------------------------------
# Checkbox 1 — requirements beyond the first comment page
# ---------------------------------------------------------------------------


class TestRequirementsBeyondFirstPage:
    def test_later_page_requirement_reaches_prompt_and_published_plan(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        transport = MarkerEchoPlanner()
        notes = [
            {"body": "First page comment.", "author": {"name": "Ada"},
             "created_at": "2026-11-01T09:00:00Z"},
            {"body": "Second page comment.", "author": {"name": "Bob"},
             "created_at": "2026-11-01T09:30:00Z"},
            {"body": f"Also: the greeting must be localized ({REQUIREMENT_MARKER}).",
             "author": {"name": "Carol"}, "created_at": "2026-11-01T10:00:00Z"},
        ]

        result, gitlab = run_plan_workflow(
            origin_repo, settings, transport, notes=notes, notes_page_size=1
        )

        assert result["status"] == STATUS_PLAN_PUBLISHED
        # The synthesis prompt (planner call #1) carried ALL comment pages.
        synthesis_prompt = transport.calls[1]["prompt"]
        start = synthesis_prompt.index("BEGIN GITLAB ISSUE COMMENTS")
        end = synthesis_prompt.index("END GITLAB ISSUE COMMENTS")
        comments_section = synthesis_prompt[start:end]
        assert REQUIREMENT_MARKER in comments_section
        assert "First page comment." in comments_section
        # End-to-end: the marker flowed into the validated plan and the
        # published note body — not just into the prompt.
        assert REQUIREMENT_MARKER in result["plan"]["requirement_understanding"]
        assert REQUIREMENT_MARKER in result["plan_publication"]["note_body"]
        assert len(note_posts(gitlab)) == 1


# ---------------------------------------------------------------------------
# Checkbox 2 — baseline matrix through the full workflow
# ---------------------------------------------------------------------------


BASELINE_CASES = [
    ("explicit_override", {"branch_override": "release/1.4"}, "release/1.4"),
    ("no_related_mr", {}, "main"),
    (
        "single_merged_mr",
        {"mrs": [make_mr(iid=9, state="merged", target_branch="release/1.4")]},
        "release/1.4",
    ),
    (
        "one_open_among_several",
        {
            "mrs": [
                make_mr(iid=3, state="merged", target_branch="release/1.0"),
                make_mr(iid=9, state="opened", target_branch="release/1.4"),
                make_mr(iid=5, state="merged", target_branch="release/1.2"),
            ]
        },
        "release/1.4",
    ),
]


class TestBaselineMatrixThroughWorkflow:
    @pytest.mark.parametrize(("case_name", "kwargs", "expected"), BASELINE_CASES)
    def test_selected_baseline_drives_clone_and_published_plan(
        self, tmp_path, origin_repo, case_name, kwargs, expected
    ):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport()

        result, gitlab = run_plan_workflow(origin_repo, settings, transport, **kwargs)

        assert result["status"] == STATUS_PLAN_PUBLISHED
        assert result["plan"]["baseline"]["branch"] == expected
        evidence = json.loads(Path(result["evidence_path"]).read_text())
        assert evidence["baseline_branch"] == expected
        assert len(note_posts(gitlab)) == 1

    def test_explicit_override_clones_the_named_branch(self, tmp_path, origin_repo):
        settings = make_inspection_settings(tmp_path)

        result, _ = run_plan_workflow(
            origin_repo, settings, FakePlannerTransport(),
            branch_override="release/1.4",
        )

        repo_dir = Path(result["workspace_repo_dir"])
        assert (repo_dir / "release.txt").is_file()

    def test_multiple_open_mrs_reject_before_any_write(self, tmp_path, origin_repo):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport()
        gitlab = FakeGitLabTransport(
            make_project_with_origin(origin_repo),
            make_issue(["agent::plan"]),
            issue_mrs=[
                make_mr(iid=9, state="opened", target_branch="release/1.4"),
                make_mr(iid=10, state="opened", target_branch="main"),
            ],
        )

        with pytest.raises(RoutingRejection) as excinfo:
            run_plan_workflow(origin_repo, settings, transport, gitlab=gitlab)

        message = str(excinfo.value)
        assert "--branch" in message
        assert "!9" in message and "!10" in message
        # Routing rejects before the graph runs: no planner call, no label
        # write, no publication.
        assert transport.calls == []
        call_names = {name for name, _ in gitlab.calls}
        assert call_names <= {"get_project", "get_issue", "get_issue_merge_requests"}
        assert not note_posts(gitlab) and not label_writes(gitlab)


# ---------------------------------------------------------------------------
# Conflicting requirements → clarification-required publication
# ---------------------------------------------------------------------------


class TestConflictingRequirements:
    def test_unresolved_questions_publish_non_executable_plan_still_reviewed(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        content = {
            **make_plan_content(),
            "unresolved_questions": ["Should the greeting be localized?"],
        }
        transport = FakePlannerTransport(
            synthesis_summary=json.dumps(content, sort_keys=True)
        )

        result, gitlab = run_plan_workflow(origin_repo, settings, transport)

        # Published, but honestly non-executable and clearly marked.
        assert result["status"] == STATUS_PLAN_NEEDS_CLARIFICATION
        publication = result["plan_publication"]
        assert publication["executable"] is False
        assert publication["unresolved_questions_count"] == 1
        assert "CLARIFICATION REQUIRED" in publication["note_body"]
        assert "localized" in publication["note_body"]
        assert len(note_posts(gitlab)) == 1
        # The needs-review transition still happens for both published kinds.
        assert publication["label_outcome"] == "transitioned_to_needs_review"
        assert gitlab.issue["labels"] == ["agent::needs-review"]


# ---------------------------------------------------------------------------
# Checkbox 5 — publication-UID dedup and version allocation
# ---------------------------------------------------------------------------


class TestPublicationDedup:
    def test_preseeded_publication_uid_dedups_before_post(self, tmp_path, origin_repo):
        settings = make_inspection_settings(tmp_path)
        seeded = seeded_plan_note(version=1, publication_uid="op-fixed-uid")

        def fixed_operation_factory(factory_settings):
            return OperationContext(
                operation_id="op-fixed-uid",
                workspace_root=Path(factory_settings.workspace_root),
                artifact_root=Path(factory_settings.artifact_root),
                control_root=Path(factory_settings.control_root),
            )

        result, gitlab = run_plan_workflow(
            origin_repo,
            settings,
            FakePlannerTransport(),
            notes=[seeded],
            operation_factory=fixed_operation_factory,
        )

        # The pre-POST scan found the same publication UID: nothing posted,
        # the existing note is the truthful publication record.
        assert result["status"] == STATUS_PLAN_PUBLISHED
        publication = result["plan_publication"]
        assert publication["deduplicated"] is True
        assert publication["note_id"] == 1  # the seeded note's id
        assert note_posts(gitlab) == []
        # The run still finalized: review transition happened.
        assert publication["label_outcome"] == "transitioned_to_needs_review"

    def test_lost_response_dedup_finds_appended_note_without_duplicate(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        gitlab = FakeGitLabTransport(
            make_project_with_origin(origin_repo),
            make_issue(["agent::plan"]),
            lose_note_response=True,
        )

        result, gitlab = run_plan_workflow(
            origin_repo, settings, FakePlannerTransport(), gitlab=gitlab
        )

        # The POST was applied then its response was lost; the re-scan found
        # the appended note, so the outcome is still one published version.
        assert result["status"] == STATUS_PLAN_PUBLISHED
        publication = result["plan_publication"]
        assert publication["deduplicated"] is False
        assert publication["note_id"] is not None
        assert len(note_posts(gitlab)) == 1
        # Exactly one plan note with this publication UID exists afterwards.
        notes = gitlab.get_issue_notes(PROJECT_ID, ISSUE_IID)
        matching = [
            note
            for note in notes
            if (parsed := parse_plan_note(note)) is not None
            and parsed.attrs.get("publication_uid") == publication["publication_uid"]
        ]
        assert len(matching) == 1
        assert parsed.version == publication["version"]


class TestPlanVersionAllocation:
    def test_new_plan_allocates_after_existing_plan_records(self, tmp_path, origin_repo):
        settings = make_inspection_settings(tmp_path)
        notes = [
            seeded_plan_note(version=1, publication_uid="uid-seed-1"),
            {"body": "A plain reviewer comment.", "author": {"name": "Reviewer"},
             "created_at": "2026-11-01T11:00:00Z"},
            seeded_plan_note(version=2, publication_uid="uid-seed-2"),
        ]

        result, gitlab = run_plan_workflow(
            origin_repo, settings, FakePlannerTransport(), notes=notes
        )

        assert result["status"] == STATUS_PLAN_PUBLISHED
        assert result["plan"]["plan_id"] == "PLAN-v3"
        publication = result["plan_publication"]
        assert publication["version"] == 3
        assert publication["plan_id"] == "PLAN-v3"
        assert publication["deduplicated"] is False
        posts = note_posts(gitlab)
        assert len(posts) == 1
        assert "PLAN-v3" in posts[0][1][2]


# ---------------------------------------------------------------------------
# Checkbox 6 — label-transition failure after a successful publication
# ---------------------------------------------------------------------------


class TestLabelFailureAfterPublication:
    def test_needs_review_transition_failure_preserves_publication(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        gitlab = NeedsReviewTransitionFailureTransport(
            make_project_with_origin(origin_repo), make_issue(["agent::plan"])
        )

        result, gitlab = run_plan_workflow(
            origin_repo, settings, FakePlannerTransport(), gitlab=gitlab
        )

        # The publication outcome stands; the label failure is a distinct,
        # recorded outcome — never a false total failure, never erased.
        assert result["status"] == STATUS_PLAN_PUBLISHED
        publication = result["plan_publication"]
        assert publication["label_outcome"] == "needs_review_transition_failed"
        assert len(note_posts(gitlab)) == 1
        assert result["message"].count("needs_review_transition_failed") == 1
        # The Issue is truthfully still agent::in-progress.
        assert gitlab.issue["labels"] == ["agent::in-progress"]
        failures = [
            outcome
            for outcome in result["label_outcomes"]
            if outcome["outcome"] == "needs_review_transition_failed"
        ]
        assert len(failures) == 1
        assert "agent::needs-review" in failures[0]["error"] or "GitLab" in failures[0]["error"]


# ---------------------------------------------------------------------------
# Ticket 05 — regeneration: new full version, invalidated approval, blocks
# ---------------------------------------------------------------------------


class TestPlanRegeneration:
    """Ticket 05 acceptance: re-requested Plan on an Issue with a prior plan.

    A regeneration run reads ALL comments again, allocates the next full
    version as one new append-only comment (old plan notes are never edited),
    invalidates the prior approval/review state label, and always ends with
    the Issue at agent::needs-review — including when the content is mostly
    unchanged. Approval stays label-values only: the fake transport's
    user/role/approval methods raise AssertionError if ever called.
    """

    @pytest.mark.parametrize("prior_label", ["agent::approved", "agent::needs-review"])
    def test_regeneration_publishes_full_new_version_and_ends_needs_review(
        self, tmp_path, origin_repo, prior_label
    ):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport()
        seeded = seeded_plan_note(version=1, publication_uid="uid-seed-v1")
        notes = [
            seeded,
            {
                "body": "Clarification from the developer: localize the greeting.",
                "author": {"name": "Ada"},
                "created_at": "2026-11-02T09:00:00Z",
            },
        ]

        result, gitlab = run_plan_workflow(
            origin_repo,
            settings,
            transport,
            labels=["agent::plan", prior_label],
            notes=notes,
        )

        # A full NEW version was published as exactly ONE new comment.
        assert result["status"] == STATUS_PLAN_PUBLISHED
        publication = result["plan_publication"]
        assert publication["version"] == 2
        assert publication["plan_id"] == "PLAN-v2"
        assert publication["publication_uid"] != "uid-seed-v1"
        posts = note_posts(gitlab)
        assert len(posts) == 1
        assert "PLAN-v2" in posts[0][1][2]
        # The old plan comment is untouched, byte-identical.
        notes_after = gitlab.get_issue_notes(PROJECT_ID, ISSUE_IID)
        assert notes_after[0]["body"] == seeded["body"]
        # The prior state label was invalidated in the activation transition
        # and the run finished at agent::needs-review — no stale approval.
        activation = result["label_outcomes"][0]
        assert activation["transition"] == "plan_to_in_progress"
        assert sorted(activation["removed_labels"]) == sorted(
            ["agent::plan", prior_label]
        )
        assert activation["invalidated_labels"] == [prior_label]
        agent_labels = [
            label for label in gitlab.issue["labels"] if label.startswith("agent::")
        ]
        assert agent_labels == ["agent::needs-review"]

    def test_regeneration_from_approved_removes_approval_label_without_identity_checks(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport()

        result, gitlab = run_plan_workflow(
            origin_repo,
            settings,
            transport,
            labels=["agent::plan", "agent::approved"],
        )

        # Approval is invalidated purely by removing the agent::approved
        # label: the recorded transitions prove it, and any user/role/
        # approval lookup would have raised AssertionError inside the fake
        # transport already.
        removals = [
            sorted(call[1][3]) for call in label_writes(gitlab)
        ]
        assert "agent::approved" in removals[0]
        assert gitlab.issue["labels"] == ["agent::needs-review"]
        assert result["status"] == STATUS_PLAN_PUBLISHED


class TestRegenerationUnderLockMutation:
    def test_approval_added_between_routing_and_lock_is_invalidated_not_blocking(
        self, tmp_path, origin_repo
    ):
        # Routing saw exactly agent::plan; a human added agent::approved
        # before the lock was acquired. The under-lock re-read now sees a
        # regeneration request, so the run proceeds and invalidates the new
        # approval instead of blocking.
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport()
        gitlab = LabelMutationTransport(
            make_project_with_origin(origin_repo),
            make_issue(["agent::plan"]),
            mutated_labels=["agent::plan", "agent::approved"],
        )

        result, gitlab = run_plan_workflow(
            origin_repo, settings, transport, gitlab=gitlab
        )

        assert result["status"] == STATUS_PLAN_PUBLISHED
        agent_labels = [
            label for label in gitlab.issue["labels"] if label.startswith("agent::")
        ]
        assert agent_labels == ["agent::needs-review"]
        activation = result["label_outcomes"][0]
        assert activation["invalidated_labels"] == ["agent::approved"]


class TestRegenerationConflictingRequirements:
    def test_conflicting_requirements_regenerate_non_executable_with_explicit_questions(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        content = {
            **make_plan_content(),
            "unresolved_questions": [
                "Should the greeting be localized, and to which locales?"
            ],
        }
        transport = FakePlannerTransport(
            synthesis_summary=json.dumps(content, sort_keys=True)
        )
        notes = [
            seeded_plan_note(version=1, publication_uid="uid-seed-v1"),
            {
                "body": "Actually the greeting must ALSO support right-to-left layouts.",
                "author": {"name": "Bob"},
                "created_at": "2026-11-02T10:00:00Z",
            },
        ]

        result, gitlab = run_plan_workflow(
            origin_repo,
            settings,
            transport,
            labels=["agent::plan", "agent::approved"],
            notes=notes,
        )

        # Conflicting requirements: published, but explicitly NOT executable.
        assert result["status"] == STATUS_PLAN_NEEDS_CLARIFICATION
        publication = result["plan_publication"]
        assert publication["version"] == 2
        assert publication["executable"] is False
        assert publication["unresolved_questions_count"] == 1
        assert "CLARIFICATION REQUIRED" in publication["note_body"]
        assert "which locales" in publication["note_body"]
        # The invalidated approval still ends at needs-review.
        assert gitlab.issue["labels"] == ["agent::needs-review"]
        assert publication["label_outcome"] == "transitioned_to_needs_review"


class TestRegenerationBlockedByMalformedLatestPlan:
    def test_malformed_latest_record_fails_synthesis_without_fallback_or_publication(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport()
        # The latest record is malformed in a way that leaves its VERSION
        # undeterminable (marker attr mangled AND fenced JSON mangled):
        # allocation must refuse a possibly-colliding version instead of
        # falling back to the older v1 record.
        broken_v2 = plan_note_body(
            make_plan(
                project_id=PROJECT_ID,
                project_path="group/demo",
                issue_iid=ISSUE_IID,
                version=2,
                plan_id="PLAN-v2",
                publication_uid="uid-broken-v2",
            )
        )
        broken_v2 = broken_v2.replace(
            "version=2 ", "version=v-two "
        ).replace("```json\n{", "```json\n{broken: ,")

        result, gitlab = run_plan_workflow(
            origin_repo,
            settings,
            transport,
            labels=["agent::plan", "agent::approved"],
            notes=[
                seeded_plan_note(version=1, publication_uid="uid-seed-v1"),
                {"body": broken_v2},
            ],
        )

        # The latest record is malformed: the run fails truthfully instead
        # of falling back to v1 or publishing a duplicate.
        assert result["status"] == STATUS_PLAN_SYNTHESIS_FAILED
        assert "not fall back" in result["error_message"] or "version" in result["error_message"]
        assert note_posts(gitlab) == []
        # Nothing was published and the Issue truthfully stays in-progress;
        # no identity lookups occurred (the fake transport would raise).
        agent_labels = [
            label for label in gitlab.issue["labels"] if label.startswith("agent::")
        ]
        assert agent_labels == ["agent::in-progress"]


# ---------------------------------------------------------------------------
# Spec (a)1 — Issue labels re-read under the lock before the transition
# ---------------------------------------------------------------------------


class TestLabelsMutatedUnderLock:
    def test_label_mutation_under_lock_blocks_transition_without_publication(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport()
        gitlab = LabelMutationTransport(
            make_project_with_origin(origin_repo),
            make_issue(["agent::plan"]),
            mutated_labels=["agent::plan", "agent::in-progress"],
        )

        result, gitlab = run_plan_workflow(
            origin_repo, settings, transport, gitlab=gitlab
        )

        # Truthful blocked terminal: the Issue no longer carries a valid Plan
        # request (agent::plan, optionally with agent::approved or
        # agent::needs-review) under the lock, so nothing acts on the stale
        # routing.
        assert result["status"] == STATUS_INSPECTION_BLOCKED
        message = result["error_message"]
        assert "no longer carries a valid Plan request state" in message
        assert "agent::in-progress" in message
        # No label transition happened at all: the mutated labels survive
        # untouched and no needs-review was applied.
        assert not label_writes(gitlab)
        assert gitlab.issue["labels"] == ["agent::plan", "agent::in-progress"]
        # Nothing was published (no plan comment, no needs-review transition).
        assert note_posts(gitlab) == []
        assert "agent::needs-review" not in gitlab.issue["labels"]
        # The blocked outcome is recorded as a truthful label_outcome.
        blocked = [
            outcome
            for outcome in result["label_outcomes"]
            if outcome["outcome"] == "blocked_labels_changed"
        ]
        assert len(blocked) == 1
        assert blocked[0]["found_labels"] == ["agent::plan", "agent::in-progress"]
        # The planner never ran: blocked before the clone/inspection.
        assert transport.calls == []
        # finalize_inspection ran on the failure routing: the lock is released.
        lock_files = list((tmp_path / "control" / "locks").glob("issue-*.lock"))
        assert lock_files
        assert json.loads(lock_files[0].read_text())["released_at"]
        # get_issue is part of the accepted Plan read surface; nothing else
        # beyond it was written.
        assert {name for name, _ in gitlab.calls} <= {
            "get_project",
            "get_issue",
            "get_issue_merge_requests",
            "get_issue_notes",
            "add_issue_note",
            "update_issue_labels",
        }


# ---------------------------------------------------------------------------
# Malformed planner output → one correction retry, then truthful failure
# ---------------------------------------------------------------------------


class TestMalformedPlannerOutput:
    def test_unparseable_synthesis_retries_then_fails_without_publishing(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport(synthesis_summary="not json")

        result, gitlab = run_plan_workflow(origin_repo, settings, transport)

        assert result["status"] == STATUS_PLAN_SYNTHESIS_FAILED
        # Inspection call + original synthesis + exactly ONE correction retry.
        assert len(transport.calls) == 3
        assert "CORRECTION REQUIRED" in transport.calls[2]["prompt"]
        attempts = result["plan_synthesis"]["attempts"]
        assert len(attempts) == 2
        # Nothing was published and the Issue truthfully stays in-progress.
        assert note_posts(gitlab) == []
        assert gitlab.issue["labels"] == ["agent::in-progress"]
        # finalize ran: locks were released, retained state intact.
        lock_files = list((tmp_path / "control" / "locks").glob("issue-*.lock"))
        assert lock_files
        assert json.loads(lock_files[0].read_text())["released_at"]


# ---------------------------------------------------------------------------
# Application boundary — report + exit-code semantics for plan outcomes
# ---------------------------------------------------------------------------


def make_canned_result(*, executable: bool) -> dict:
    return {
        "mode": "plan",
        "status": (
            STATUS_PLAN_PUBLISHED if executable else STATUS_PLAN_NEEDS_CLARIFICATION
        ),
        "issue": {"iid": ISSUE_IID, "title": "Add a greeting"},
        "operation": {
            "operation_id": "op-canned-1",
            "artifact_path": "/artifacts/op-canned-1",
        },
        "inspection_evidence": {
            "source_file_count": 3,
            "modules": [{"path": "src"}],
            "config_files": ["pyproject.toml"],
        },
        "message": "Plan PLAN-v1 published.",
        "evidence_path": "/artifacts/op-canned-1/inspection/inspection-evidence.json",
        "plan_publication": {
            "version": 1,
            "plan_id": "PLAN-v1",
            "publication_uid": "op-canned-1",
            "note_id": 12,
            "note_body": "(canned)",
            "executable": executable,
            "unresolved_questions_count": 0 if executable else 2,
            "deduplicated": False,
            "label_outcome": "transitioned_to_needs_review",
        },
    }


class TestPlanPublicationReportBoundary:
    def _run_main_with_result(self, monkeypatch, capsys, tmp_path, canned):
        import main as main_module

        def fake_run_routed(settings_from_main, **kwargs):
            return canned

        monkeypatch.setattr(workflow_module, "run_routed_workflow", fake_run_routed)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "main.py",
                "--project",
                "group/demo",
                "--issue",
                str(ISSUE_IID),
                "--output-dir",
                str(tmp_path / "runs"),
            ],
        )
        code = main_module.main()
        captured = capsys.readouterr()
        report = json.loads((tmp_path / "runs" / "poc-result.json").read_text())
        return code, captured.out, captured.err, report

    def test_executable_publication_exits_0_marked_executable(
        self, tmp_path, monkeypatch, capsys
    ):
        code, out, err, report = self._run_main_with_result(
            monkeypatch, capsys, tmp_path, make_canned_result(executable=True)
        )

        assert code == 0
        assert "EXECUTABLE — ready for human review" in out
        assert "CLARIFICATION REQUIRED" not in out
        assert report["status"] == STATUS_PLAN_PUBLISHED
        assert report["plan_publication"]["executable"] is True
        assert report["exit_code_semantics"]["0"]

    def test_clarification_required_publication_exits_0_but_marked_not_executable(
        self, tmp_path, monkeypatch, capsys
    ):
        code, out, err, report = self._run_main_with_result(
            monkeypatch, capsys, tmp_path, make_canned_result(executable=False)
        )

        # Both published kinds exit 0; the explicit marking is the difference.
        assert code == 0
        assert "CLARIFICATION REQUIRED — NOT executable" in out
        assert "EXECUTABLE — ready for human review" not in out
        assert report["status"] == STATUS_PLAN_NEEDS_CLARIFICATION
        assert report["plan_publication"]["executable"] is False
        assert report["plan_publication"]["unresolved_questions_count"] == 2
        assert report["exit_code_semantics"]["0"]

    def test_synthesis_failure_exits_1_without_publication_fields(
        self, tmp_path, monkeypatch, capsys
    ):
        canned = {
            "mode": "plan",
            "status": STATUS_PLAN_SYNTHESIS_FAILED,
            "error_message": (
                "Planner did not return a valid plan content object after one "
                "correction retry"
            ),
            "operation": {"operation_id": "op-canned-failed"},
        }

        code, out, err, report = self._run_main_with_result(
            monkeypatch, capsys, tmp_path, canned
        )

        assert code == 1
        assert "ERROR:" in err
        assert report["status"] == STATUS_PLAN_SYNTHESIS_FAILED
        assert report["operation_id"] == "op-canned-failed"
        assert "plan_publication" not in report
