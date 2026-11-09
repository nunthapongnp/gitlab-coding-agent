"""Offline client tests for Issue context reads and publication (ticket 04, WP-A).

Covers the ``src.gitlab_client`` additions required to read complete Issue
context and publish plan records (P0-SPEC-002):

- paginated ``get_issue_notes`` (X-Next-Page, bounded, no note lost at a page
  boundary),
- append-only ``add_issue_note``,
- explicit ``update_issue_labels`` add/remove transitions that never use the
  exclusive ``labels=`` replacement,
- token-safe, actionable ``GitLabAPIError`` failures.

Everything runs against a local recording HTTP server (same fixture style as
``tests/test_plan_inspection.py``) — no real GitLab is contacted. Every
request the client makes is recorded so the tests can also assert that these
operations never touch branch-creation, MR-creation, merge, or approval
endpoints.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from src.gitlab_client import GitLabClient, GitLabAPIError


TOKEN = "glpat-secret-do-not-leak-0f3a9"

PROJECT_PATH = "group%2Fdemo"
ISSUE_IID = 7


class RecordingGitLabServer:
    """Local HTTP fixture that records requests and serves scripted responses.

    Tests queue responses with ``enqueue(method, path, ...)``; the handler
    answers the next request matching that (method, path-without-query) and
    records every request (method, full path with query, JSON body, and the
    PRIVATE-TOKEN header) in ``requests``. Unscripted requests get a 500 so
    accidental endpoint use fails loudly instead of passing silently.
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._scripted: dict[tuple[str, str], list[tuple]] = {}
        self._http = self._start()

    def _start(self) -> HTTPServer:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _respond(self):  # noqa: ANN202
                length = int(self.headers.get("Content-Length") or 0)
                raw_body = self.rfile.read(length).decode() if length else ""
                split = urlsplit(self.path)
                server.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "path_only": split.path,
                        "query": parse_qs(split.query, keep_blank_values=True),
                        "body": raw_body,
                        "token": self.headers.get("PRIVATE-TOKEN"),
                    }
                )
                queue = server._scripted.get((self.command, split.path))
                if not queue:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps(
                            {
                                "error": (
                                    f"no scripted response for "
                                    f"{self.command} {split.path}"
                                )
                            }
                        ).encode()
                    )
                    return
                status, payload, headers = queue.pop(0)
                encoded = (
                    json.dumps(payload).encode() if payload is not None else b""
                )
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                if encoded:
                    self.send_header("Content-Type", "application/json")
                self.end_headers()
                if encoded:
                    self.wfile.write(encoded)

            do_GET = do_POST = do_PUT = _respond  # noqa: N815

            def log_message(self, *args):  # silence
                pass

        http = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        return http

    def enqueue(
        self,
        method: str,
        path: str,
        *,
        status: int = 200,
        payload=None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._scripted.setdefault((method.upper(), path), []).append(
            (status, payload, headers)
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._http.server_port}"

    def shutdown(self) -> None:
        self._http.shutdown()
        self._http.server_close()


@pytest.fixture
def fake_gitlab() -> RecordingGitLabServer:
    server = RecordingGitLabServer()
    yield server
    server.shutdown()


@pytest.fixture
def client(fake_gitlab: RecordingGitLabServer) -> GitLabClient:
    return GitLabClient(fake_gitlab.url, token=TOKEN)


def notes_path() -> str:
    return f"/api/v4/projects/{PROJECT_PATH}/issues/{ISSUE_IID}/notes"


def issue_path() -> str:
    return f"/api/v4/projects/{PROJECT_PATH}/issues/{ISSUE_IID}"


def note(note_id: int, body: str, *, system: bool = False) -> dict:
    return {
        "id": note_id,
        "body": body,
        "system": system,
        "author": {"id": 42, "username": "agent-bot"},
        "created_at": "2026-11-09T00:00:0"
        f"{note_id % 10}Z",
        "noteable_iid": ISSUE_IID,
    }


class TestPaginatedNotesRead:
    def test_reads_every_note_across_page_boundaries(self, fake_gitlab, client):
        # Five notes, two per page: the third page is partial. Requirements
        # recorded on pages 2 and 3 must survive the read.
        pages = [
            [note(1, "First requirement"), note(2, "Second requirement")],
            [note(3, "Third requirement"), note(4, "system note", system=True)],
            [note(5, "Fifth requirement")],
        ]
        for page_number, page in enumerate(pages, start=1):
            headers = {"X-Next-Page": str(page_number + 1)}
            if page_number == len(pages):
                headers = {"X-Next-Page": ""}
            fake_gitlab.enqueue(
                "GET", notes_path(), payload=page, headers=headers
            )

        notes = client.get_issue_notes("group/demo", ISSUE_IID, per_page=2)

        assert [n["id"] for n in notes] == [1, 2, 3, 4, 5]
        assert notes[3]["system"] is True
        assert notes[0]["author"]["username"] == "agent-bot"
        note_requests = [
            r for r in fake_gitlab.requests if r["path_only"] == notes_path()
        ]
        assert len(note_requests) == 3
        assert [r["query"]["page"] for r in note_requests] == [["1"], ["2"], ["3"]]
        assert all(r["query"]["per_page"] == ["2"] for r in note_requests)

    def test_single_page_read_stops_when_next_page_is_empty(
        self, fake_gitlab, client
    ):
        fake_gitlab.enqueue(
            "GET", notes_path(), payload=[note(1, "only note")], headers={}
        )

        notes = client.get_issue_notes("group/demo", ISSUE_IID)

        assert [n["id"] for n in notes] == [1]
        assert len(fake_gitlab.requests) == 1

    def test_notes_are_returned_exactly_as_gitlab_shapes_them(
        self, fake_gitlab, client
    ):
        shaped = note(9, "payload-like body")
        fake_gitlab.enqueue("GET", notes_path(), payload=[shaped], headers={})

        notes = client.get_issue_notes("group/demo", ISSUE_IID)

        assert notes == [shaped]

    def test_page_enumeration_that_never_ends_fails_bounded(self, fake_gitlab, client):
        # More pages keep arriving than max_pages allows: the read must fail
        # clearly instead of silently truncating (no note is lost quietly).
        for page_number in range(1, 5):
            fake_gitlab.enqueue(
                "GET",
                notes_path(),
                payload=[note(1, "loop")],
                headers={"X-Next-Page": str(page_number + 1)},
            )

        with pytest.raises(GitLabAPIError) as excinfo:
            client.get_issue_notes("group/demo", ISSUE_IID, per_page=1, max_pages=3)

        assert "exceeded 3 pages" in str(excinfo.value)
        assert len(fake_gitlab.requests) == 3

    def test_non_list_note_payload_is_rejected_not_trusted(self, fake_gitlab, client):
        fake_gitlab.enqueue("GET", notes_path(), payload={"id": 1}, headers={})

        with pytest.raises(GitLabAPIError) as excinfo:
            client.get_issue_notes("group/demo", ISSUE_IID)

        assert "unexpected Issue note list" in str(excinfo.value)


class TestAppendOnlyNotePublication:
    def test_adds_one_note_and_returns_the_created_note(self, fake_gitlab, client):
        created = note(11, "PLAN-v1 publication body")
        fake_gitlab.enqueue("POST", notes_path(), status=201, payload=created)

        result = client.add_issue_note("group/demo", ISSUE_IID, "PLAN-v1 publication body")

        assert result == created
        posts = [r for r in fake_gitlab.requests if r["method"] == "POST"]
        assert len(posts) == 1
        assert posts[0]["path_only"] == notes_path()
        assert json.loads(posts[0]["body"]) == {"body": "PLAN-v1 publication body"}

    def test_empty_body_is_rejected_locally_without_a_request(
        self, fake_gitlab, client
    ):
        for body in ("", "   "):
            with pytest.raises(ValueError) as excinfo:
                client.add_issue_note("group/demo", ISSUE_IID, body)
            assert "non-empty" in str(excinfo.value)
        assert fake_gitlab.requests == []

    def test_non_string_body_is_rejected_locally(self, fake_gitlab, client):
        with pytest.raises(ValueError) as excinfo:
            client.add_issue_note("group/demo", ISSUE_IID, 12345)  # type: ignore[arg-type]
        assert "non-empty" in str(excinfo.value)
        assert fake_gitlab.requests == []


class TestLabelTransitions:
    @staticmethod
    def put_query(fake_gitlab: RecordingGitLabServer) -> dict:
        puts = [r for r in fake_gitlab.requests if r["method"] == "PUT"]
        assert len(puts) == 1
        return puts[0]["query"]

    def test_add_and_remove_leave_unrelated_labels_alone(self, fake_gitlab, client):
        # The server applies add/remove to the existing label set; unrelated
        # labels survive because the client never sends an exclusive list.
        updated = {
            "iid": ISSUE_IID,
            "labels": ["agent::in-progress", "priority::high", "backend"],
        }
        fake_gitlab.enqueue("PUT", issue_path(), payload=updated)

        result = client.update_issue_labels(
            "group/demo",
            ISSUE_IID,
            add_labels=["agent::in-progress"],
            remove_labels=["agent::plan"],
        )

        assert result == updated
        query = self.put_query(fake_gitlab)
        assert query["add_labels"] == ["agent::in-progress"]
        assert query["remove_labels"] == ["agent::plan"]
        # The exclusive replacement field must never be used.
        assert "labels" not in query

    def test_add_only_transition_omits_remove_parameter(self, fake_gitlab, client):
        updated = {"iid": ISSUE_IID, "labels": ["agent::needs-review"]}
        fake_gitlab.enqueue("PUT", issue_path(), payload=updated)

        result = client.update_issue_labels(
            "group/demo", ISSUE_IID, add_labels=["agent::needs-review"]
        )

        assert result == updated
        query = self.put_query(fake_gitlab)
        assert query["add_labels"] == ["agent::needs-review"]
        assert "remove_labels" not in query
        assert "labels" not in query

    def test_remove_only_transition_omits_add_parameter(self, fake_gitlab, client):
        updated = {"iid": ISSUE_IID, "labels": ["priority::high"]}
        fake_gitlab.enqueue("PUT", issue_path(), payload=updated)

        result = client.update_issue_labels(
            "group/demo", ISSUE_IID, remove_labels=["agent::needs-review"]
        )

        assert result == updated
        query = self.put_query(fake_gitlab)
        assert query["remove_labels"] == ["agent::needs-review"]
        assert "add_labels" not in query
        assert "labels" not in query

    def test_empty_add_and_empty_remove_is_rejected_without_a_request(
        self, fake_gitlab, client
    ):
        with pytest.raises(ValueError) as excinfo:
            client.update_issue_labels("group/demo", ISSUE_IID)
        assert "at least one label" in str(excinfo.value)
        assert fake_gitlab.requests == []

    def test_bare_string_labels_are_rejected_to_avoid_character_splitting(
        self, fake_gitlab, client
    ):
        with pytest.raises(ValueError) as excinfo:
            client.update_issue_labels(
                "group/demo", ISSUE_IID, add_labels="agent::plan"  # type: ignore[arg-type]
            )
        assert "sequence of label names" in str(excinfo.value)
        assert fake_gitlab.requests == []

    def test_multiple_labels_join_into_one_parameter(self, fake_gitlab, client):
        updated = {"iid": ISSUE_IID, "labels": ["a", "b", "c"]}
        fake_gitlab.enqueue("PUT", issue_path(), payload=updated)

        client.update_issue_labels(
            "group/demo",
            ISSUE_IID,
            add_labels=["a", "b"],
            remove_labels=["c"],
        )

        query = self.put_query(fake_gitlab)
        assert query["add_labels"] == ["a,b"]
        assert query["remove_labels"] == ["c"]


class TestFailedResponsesAreTokenSafe:
    def test_http_422_names_the_api_detail_without_leaking_the_token(
        self, fake_gitlab, client
    ):
        fake_gitlab.enqueue(
            "PUT",
            issue_path(),
            status=422,
            payload={"message": "400 Bad request - add_labels is invalid"},
        )

        with pytest.raises(GitLabAPIError) as excinfo:
            client.update_issue_labels(
                "group/demo",
                ISSUE_IID,
                add_labels=["agent::in-progress"],
                remove_labels=["agent::plan"],
            )

        message = str(excinfo.value)
        assert "HTTP 422" in message
        assert "add_labels is invalid" in message
        assert TOKEN not in message
        assert TOKEN not in repr(excinfo.value)

    def test_http_401_fails_actionably_without_leaking_the_token(
        self, fake_gitlab, client
    ):
        fake_gitlab.enqueue("GET", notes_path(), status=401, payload={"message": "401 Unauthorized"})

        with pytest.raises(GitLabAPIError) as excinfo:
            client.get_issue_notes("group/demo", ISSUE_IID)

        message = str(excinfo.value)
        assert "HTTP 401" in message
        assert TOKEN not in message

    def test_non_json_error_body_still_produces_an_actionable_message(
        self, fake_gitlab, client
    ):
        fake_gitlab.enqueue("POST", notes_path(), status=500, payload=None)

        with pytest.raises(GitLabAPIError) as excinfo:
            client.add_issue_note("group/demo", ISSUE_IID, "body")

        assert "HTTP 500" in str(excinfo.value)
        assert TOKEN not in str(excinfo.value)

    def test_token_is_sent_on_every_request_but_never_in_any_error(
        self, fake_gitlab, client
    ):
        fake_gitlab.enqueue("GET", notes_path(), payload=[note(1, "x")], headers={})

        client.get_issue_notes("group/demo", ISSUE_IID)

        assert all(r["token"] == TOKEN for r in fake_gitlab.requests)


class TestNoForbiddenEndpoints:
    def test_context_operations_never_touch_branch_mr_or_approval_endpoints(
        self, fake_gitlab, client
    ):
        fake_gitlab.enqueue("GET", notes_path(), payload=[note(1, "req")], headers={})
        fake_gitlab.enqueue("POST", notes_path(), status=201, payload=note(2, "plan"))
        fake_gitlab.enqueue(
            "PUT", issue_path(), payload={"iid": ISSUE_IID, "labels": ["agent::in-progress"]}
        )

        client.get_issue_notes("group/demo", ISSUE_IID)
        client.add_issue_note("group/demo", ISSUE_IID, "plan body")
        client.update_issue_labels(
            "group/demo",
            ISSUE_IID,
            add_labels=["agent::in-progress"],
            remove_labels=["agent::plan"],
        )

        paths = [r["path_only"] for r in fake_gitlab.requests]
        forbidden_fragments = (
            "merge_requests",
            "branches",
            "merge",
            "approve",
            "approvals",
            "users",
            "members",
        )
        for fragment in forbidden_fragments:
            assert all(fragment not in path for path in paths), (
                f"forbidden endpoint fragment '{fragment}' was requested: {paths}"
            )
        # Exactly the three documented endpoints were used, nothing else.
        assert paths == [notes_path(), notes_path(), issue_path()]
