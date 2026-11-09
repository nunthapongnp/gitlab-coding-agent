from __future__ import annotations

from urllib.parse import quote
from typing import Any

import requests


class GitLabAPIError(RuntimeError):
    pass


class GitLabClient:
    """Small read-oriented GitLab REST API client.

    Covers project, Issue, and related-merge-request reads. The workflow never
    creates branches or merge requests and never merges or approves through
    this client; label transitions arrive in a later ticket.
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
        if issue_iid < 1:
            raise ValueError("Issue IID must be a positive integer")
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
        if issue_iid < 1:
            raise ValueError("Issue IID must be a positive integer")
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
            raise GitLabAPIError(
                f"GitLab Issue merge-request listing exceeded {max_pages} pages; "
                "refusing to continue without inspecting every related MR."
            )
        return items
