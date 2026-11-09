from __future__ import annotations

from urllib.parse import quote
from typing import Any

import requests


class GitLabAPIError(RuntimeError):
    pass


class GitLabClient:
    """Small GitLab REST API client for projects, issues, and merge requests."""

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

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/api/v4/{path.lstrip('/')}"
        try:
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
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

    def find_open_merge_request(
        self, project: str | int, source_branch: str, target_branch: str
    ) -> dict | None:
        payload = self._request(
            "GET",
            f"projects/{self._project_path(project)}/merge_requests",
            params={
                "state": "opened",
                "source_branch": source_branch,
                "target_branch": target_branch,
                "per_page": 100,
            },
        )
        if not isinstance(payload, list):
            raise GitLabAPIError("GitLab returned an unexpected merge request list")
        return payload[0] if payload else None

    def create_merge_request(
        self,
        project: str | int,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
        draft: bool = False,
        remove_source_branch: bool = True,
        squash: bool = True,
    ) -> dict:
        final_title = title.strip() or "Automated code changes"
        if draft and not final_title.lower().startswith("draft:"):
            final_title = f"Draft: {final_title}"
        payload = self._request(
            "POST",
            f"projects/{self._project_path(project)}/merge_requests",
            json={
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": final_title,
                "description": description,
                "remove_source_branch": remove_source_branch,
                "squash": squash,
            },
        )
        if not isinstance(payload, dict):
            raise GitLabAPIError("GitLab returned an unexpected merge request response")
        return payload
