from __future__ import annotations

from urllib.parse import quote
from typing import Any

import requests


class GitLabAPIError(RuntimeError):
    pass


def _check_issue_iid(issue_iid: int) -> None:
    """Validate an Issue IID argument (positive integer) before any request.

    Shared by every Issue-scoped endpoint so the rejection contract is
    identical everywhere; a non-integer input still fails via the natural
    ``TypeError`` of the ``<`` comparison, before any HTTP round trip.
    """
    if issue_iid < 1:
        raise ValueError("Issue IID must be a positive integer")


class GitLabClient:
    """Small read-and-record GitLab REST API client.

    Covers project, Issue, related-merge-request, and Issue-note reads plus
    append-only note publication and explicit Issue label transitions. The
    workflow never creates branches or merge requests and never merges or
    approves through this client. Label transitions use explicit
    add/remove parameters so unrelated labels are preserved; note
    publication is append-only and never edits or deletes existing notes.
    """

    def __init__(self, base_url: str, token: str | None = None, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if token:
            self.session.headers["PRIVATE-TOKEN"] = token

    @staticmethod
    def _project_path(project: str | int) -> str:
        return quote(str(project), safe="")

    def _send(self, method: str, path: str, **kwargs: Any):
        """Perform one HTTP request and return the raw response.

        Raises GitLabAPIError with a token-safe, actionable message on any
        transport or HTTP failure.
        """
        url = f"{self.base_url}/api/v4/{path.lstrip('/')}"
        try:
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response
        except requests.HTTPError as exc:
            response = exc.response
            status = getattr(response, "status_code", None)
            detail = ""
            if response is not None:
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        detail = str(payload.get("message") or payload.get("error") or payload)
                    else:
                        detail = str(payload)
                except ValueError:
                    detail = (response.text or "")[:1000]
            suffix = f" (HTTP {status})" if status else ""
            raise GitLabAPIError(f"GitLab API request failed{suffix}: {detail or exc}") from exc
        except requests.RequestException as exc:
            raise GitLabAPIError(f"GitLab API request failed: {exc}") from exc

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._send(method, path, **kwargs)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise GitLabAPIError("GitLab API returned invalid JSON") from exc

    def get_project(self, project: str | int) -> dict:
        payload = self._request("GET", f"projects/{self._project_path(project)}")
        if not isinstance(payload, dict):
            raise GitLabAPIError("GitLab returned an unexpected project response")
        return payload

    def get_issue(self, project: str | int, issue_iid: int) -> dict:
        _check_issue_iid(issue_iid)
        payload = self._request(
            "GET", f"projects/{self._project_path(project)}/issues/{issue_iid}"
        )
        if not isinstance(payload, dict):
            raise GitLabAPIError("GitLab returned an unexpected issue response")
        return payload

    def get_issue_merge_requests(
        self,
        project: str | int,
        issue_iid: int,
        *,
        per_page: int = 50,
        max_pages: int = 100,
    ) -> list[dict]:
        """Read every merge request related to an Issue, across pages.

        Uses the documented Issue/MR relationship endpoint, not branch or
        title guesses. Pagination follows ``X-Next-Page`` so no related MR is
        lost at an API page boundary; the page count is bounded so a broken
        response cannot loop forever.
        """
        _check_issue_iid(issue_iid)
        path = (
            f"projects/{self._project_path(project)}/issues/{issue_iid}/merge_requests"
        )
        items: list[dict] = []
        page = 1
        while page <= max_pages:
            response = self._send("GET", path, params={"page": page, "per_page": per_page})
            try:
                payload = response.json()
            except ValueError as exc:
                raise GitLabAPIError("GitLab API returned invalid JSON") from exc
            if not isinstance(payload, list):
                raise GitLabAPIError("GitLab returned an unexpected merge request list")
            items.extend(payload)
            next_page = (response.headers.get("X-Next-Page") or "").strip()
            if not next_page or not payload:
                break
            try:
                following = int(next_page)
            except ValueError:
                break
            if following <= page:
                break
            page = following
        else:
            # Reached only when the while condition failed, i.e. every
            # ``max_pages`` pages were consumed while more pages kept
            # arriving — a bounded failure, not a silent truncation.
            raise GitLabAPIError(
                f"GitLab Issue merge-request listing exceeded {max_pages} pages; "
                "refusing to continue without inspecting every related MR."
            )
        return items

    def get_issue_notes(
        self,
        project: str | int,
        issue_iid: int,
        *,
        per_page: int = 50,
        max_pages: int = 100,
    ) -> list[dict]:
        """Read every note on an Issue, across pages.

        Uses the documented Issue notes endpoint. Pagination follows
        ``X-Next-Page`` so requirements recorded beyond the first API page
        survive; the page count is bounded so a broken response cannot loop
        forever. Notes are returned exactly as GitLab shapes them (id, body,
        author, system, created_at, ...) in the order the pages arrive.
        """
        _check_issue_iid(issue_iid)
        path = f"projects/{self._project_path(project)}/issues/{issue_iid}/notes"
        items: list[dict] = []
        page = 1
        while page <= max_pages:
            response = self._send("GET", path, params={"page": page, "per_page": per_page})
            try:
                payload = response.json()
            except ValueError as exc:
                raise GitLabAPIError("GitLab API returned invalid JSON") from exc
            if not isinstance(payload, list):
                raise GitLabAPIError("GitLab returned an unexpected Issue note list")
            items.extend(payload)
            next_page = (response.headers.get("X-Next-Page") or "").strip()
            if not next_page or not payload:
                break
            try:
                following = int(next_page)
            except ValueError:
                break
            if following <= page:
                break
            page = following
        else:
            # Reached only when the while condition failed, i.e. every
            # ``max_pages`` pages were consumed while more pages kept
            # arriving — a bounded failure, not a silent truncation.
            raise GitLabAPIError(
                f"GitLab Issue note listing exceeded {max_pages} pages; "
                "refusing to continue without reading every Issue comment."
            )
        return items

    def add_issue_note(self, project: str | int, issue_iid: int, body: str) -> dict:
        """Append one comment to an Issue and return the created note.

        Append-only by contract: this client exposes no edit or delete of
        notes, and callers deduplicate by scanning ``get_issue_notes`` for a
        publication identifier before reposting an uncertain delivery.
        """
        _check_issue_iid(issue_iid)
        if not isinstance(body, str) or not body.strip():
            raise ValueError("Note body must be a non-empty string")
        payload = self._request(
            "POST",
            f"projects/{self._project_path(project)}/issues/{issue_iid}/notes",
            json={"body": body},
        )
        if not isinstance(payload, dict):
            raise GitLabAPIError("GitLab returned an unexpected note response")
        return payload

    @staticmethod
    def _labels_param(value: Any, *, direction: str) -> str:
        if isinstance(value, str):
            raise ValueError(
                f"{direction} must be a sequence of label names, not a single "
                "string; a bare string would be split into characters"
            )
        try:
            labels = list(value)
        except TypeError as exc:
            raise ValueError(f"{direction} must be a sequence of label names") from exc
        for label in labels:
            if not isinstance(label, str) or not label.strip():
                raise ValueError(f"{direction} must contain only non-empty label names")
        return ",".join(labels)

    def update_issue_labels(
        self,
        project: str | int,
        issue_iid: int,
        *,
        add_labels: Any = (),
        remove_labels: Any = (),
    ) -> dict:
        """Transition Issue labels with explicit add/remove operations.

        Uses the documented ``add_labels``/``remove_labels`` PUT parameters
        (never the exclusive ``labels=`` replacement) so unrelated labels are
        preserved regardless of GitLab tier. Empty add or empty remove is
        supported; an update with nothing to change is rejected locally
        without an HTTP round trip.
        """
        _check_issue_iid(issue_iid)
        params: dict[str, str] = {}
        add = self._labels_param(add_labels, direction="add_labels")
        if add:
            params["add_labels"] = add
        remove = self._labels_param(remove_labels, direction="remove_labels")
        if remove:
            params["remove_labels"] = remove
        if not params:
            raise ValueError(
                "update_issue_labels requires at least one label to add or remove"
            )
        payload = self._request(
            "PUT",
            f"projects/{self._project_path(project)}/issues/{issue_iid}",
            params=params,
        )
        if not isinstance(payload, dict):
            raise GitLabAPIError("GitLab returned an unexpected issue response")
        return payload
