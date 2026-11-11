from __future__ import annotations

from urllib.parse import quote
from typing import Any

import requests

RECOGNIZED_LABELS = {'agent::explore', 'agent::implement', 'agent::in-progress',
                     'agent::needs-review', 'agent::approved'}


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

    def get_issue_notes(self, project: str | int, issue_iid: int) -> list[dict]:
        """Read the complete discussion, including pages beyond the first 100."""
        notes = []
        page = 1
        while True:
            payload = self._request('GET',
                f'projects/{self._project_path(project)}/issues/{issue_iid}/notes',
                params={'page': page, 'per_page': 100, 'order_by': 'id', 'sort': 'asc'})
            if not isinstance(payload, list):
                raise GitLabAPIError('GitLab returned an unexpected Issue comment list')
            if not payload:
                return notes
            notes.extend(payload)
            page += 1

    def replace_issue_status(self, project, issue_iid, expected, replacement):
        issue = self.get_issue(project, issue_iid)
        if set(issue.get('labels', [])) & RECOGNIZED_LABELS != {expected}:
            raise GitLabAPIError('Issue workflow labels changed; manual review required')
        payload = self._request('PUT',
            f'projects/{self._project_path(project)}/issues/{issue_iid}',
            json={'remove_labels': expected, 'add_labels': replacement})
        if not isinstance(payload, dict) or set(payload.get('labels', [])) & RECOGNIZED_LABELS != {replacement}:
            raise GitLabAPIError('Issue label replacement was not confirmed')
        return payload

    def create_issue_note(self, project, issue_iid, body):
        return self._request('POST', f'projects/{self._project_path(project)}/issues/{issue_iid}/notes',
                             json={'body': body})

    def _all_pages(self, path):
        items, page = [], 1
        while True:
            payload = self._request('GET', path, params={'page': page, 'per_page': 100})
            if not isinstance(payload, list):
                raise GitLabAPIError('GitLab returned an unexpected paginated response')
            if not payload:
                return items
            items.extend(payload)
            page += 1

    def get_merge_request(self, project, mr_iid):
        payload = self._request('GET', f'projects/{self._project_path(project)}/merge_requests/{mr_iid}')
        if not isinstance(payload, dict):
            raise GitLabAPIError('GitLab returned an unexpected MR response')
        return payload

    def get_merge_request_notes(self, project, mr_iid):
        return self._all_pages(f'projects/{self._project_path(project)}/merge_requests/{mr_iid}/notes')

    def get_issue_related_merge_requests(self, project, issue_iid):
        return self._all_pages(f'projects/{self._project_path(project)}/issues/{issue_iid}/related_merge_requests')

    def get_branch(self, project, branch):
        payload = self._request('GET', f'projects/{self._project_path(project)}/repository/branches/{quote(branch, safe="")}')
        if not isinstance(payload, dict):
            raise GitLabAPIError('GitLab returned an unexpected branch response')
        return payload

    def replace_merge_request_status(self, project, mr_iid, expected, replacement):
        mr = self.get_merge_request(project, mr_iid)
        if set(mr.get('labels', [])) & RECOGNIZED_LABELS != {expected}:
            raise GitLabAPIError('MR workflow labels changed; manual review required')
        payload = self._request('PUT', f'projects/{self._project_path(project)}/merge_requests/{mr_iid}',
            json={'remove_labels': expected, 'add_labels': replacement})
        if not isinstance(payload, dict) or set(payload.get('labels', [])) & RECOGNIZED_LABELS != {replacement}:
            raise GitLabAPIError('MR label replacement was not confirmed')
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
