"""Atlassian Cloud (Jira + Confluence) 통합 클라이언트.

인증은 저장소 루트의 ``jira_api_email.txt`` / ``jira_api_token.txt``
(둘 다 .gitignore 대상)를 사용한다. 토큰은 어떤 경로로도 로그에 남기지
않으며, 진단 출력에는 마스킹된 이메일과 토큰 길이만 사용한다.

설계 원칙
---------
* 읽기(조회)는 즉시 실행한다.
* 쓰기(생성/수정/삭제)는 ``dry_run=True`` 가 기본이며, 호출자가 명시적으로
  ``dry_run=False`` 를 주어야 실제 요청이 나간다.
* 모든 응답 실패는 ``AtlassianError`` 로 정규화하고, 응답 본문은
  토큰이 섞이지 않도록 redact 후 보관한다.

사용 예::

    from atlassian_client import AtlassianClient

    cli = AtlassianClient()
    me = cli.jira_myself()
    issues = cli.jira_search("assignee = currentUser() order by updated DESC")
    spaces = cli.confluence_spaces()
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import requests
from requests.auth import HTTPBasicAuth

DEFAULT_SITE = "https://higen-rnd.atlassian.net"
DEFAULT_ROOT = Path(__file__).resolve().parent
EMAIL_FILE = "jira_api_email.txt"
TOKEN_FILE = "jira_api_token.txt"

# Jira 이슈 검색 시 기본으로 가져올 필드
DEFAULT_JIRA_FIELDS: tuple[str, ...] = (
    "summary",
    "status",
    "assignee",
    "reporter",
    "priority",
    "issuetype",
    "project",
    "created",
    "updated",
    "duedate",
    "parent",
)

# Jira Cloud 커스텀 필드 (main.py 와 동일한 매핑 유지)
CF_START_DATE = "customfield_10015"


class AtlassianError(RuntimeError):
    """Atlassian REST 호출 실패를 표현하는 예외."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        url: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body

    def __str__(self) -> str:  # pragma: no cover - 표시용
        parts = [super().__str__()]
        if self.status is not None:
            parts.append(f"HTTP {self.status}")
        if self.url:
            parts.append(self.url)
        if self.body not in (None, ""):
            text = self.body if isinstance(self.body, str) else json.dumps(
                self.body, ensure_ascii=False
            )
            parts.append(text[:800])
        return " | ".join(parts)


@dataclass(frozen=True)
class Credentials:
    """API 토큰 인증 정보. ``token`` 은 절대 출력하지 않는다."""

    email: str
    token: str
    source: str = "file"

    @property
    def masked(self) -> str:
        email = self.email or ""
        if "@" in email:
            return f"{email[:2]}***@{email.split('@')[-1]}"
        return "***"

    def describe(self) -> str:
        return f"auth={self.masked} token_len={len(self.token)} source={self.source}"

    def __repr__(self) -> str:  # pragma: no cover - 토큰 노출 방지
        return f"Credentials(email={self.masked!r}, token=<hidden>)"


def _read_first_line(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def load_credentials(
    root: str | Path = DEFAULT_ROOT, *, allow_env: bool = True
) -> Credentials:
    """저장소 파일에서 자격증명을 읽는다.

    파일이 없고 ``allow_env`` 가 참이면 ``JIRA_API_EMAIL`` /
    ``JIRA_API_TOKEN`` 환경변수로 대체한다.
    """

    root = Path(root)
    email_path = root / EMAIL_FILE
    token_path = root / TOKEN_FILE

    if email_path.is_file() and token_path.is_file():
        email = _read_first_line(email_path)
        token = _read_first_line(token_path)
        if not email or not token:
            raise AtlassianError(
                f"자격증명 파일이 비어 있습니다: {email_path.name} / {token_path.name}"
            )
        return Credentials(email=email, token=token, source="file")

    if allow_env:
        email = (os.environ.get("JIRA_API_EMAIL") or "").strip()
        token = (os.environ.get("JIRA_API_TOKEN") or "").strip()
        if email and token:
            return Credentials(email=email, token=token, source="env")

    raise AtlassianError(
        "자격증명을 찾을 수 없습니다. "
        f"{email_path} 와 {token_path} 를 생성하거나 "
        "JIRA_API_EMAIL / JIRA_API_TOKEN 환경변수를 설정하세요."
    )


def redact(value: Any) -> Any:
    """응답/요청 구조에서 토큰 흔적을 지운다 (Authorization 헤더 대응)."""

    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if str(k).lower() in {"authorization", "token", "api_token", "password"}:
                out[k] = "<hidden>"
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def to_adf(*blocks: str) -> dict:
    """평문 여러 개를 ADF ``doc`` 으로 변환한다 (문단 구분).

    Jira Cloud v3 의 description/comment 는 ADF 를 요구한다. 빈 줄로
    구분된 덩어리를 문단으로, ``- `` 로 시작하는 연속 줄은 불릿 목록으로
    변환한다.
    """

    content: list[dict] = []
    bullets: list[dict] = []

    def flush_bullets() -> None:
        if bullets:
            content.append({"type": "bulletList", "content": list(bullets)})
            bullets.clear()

    text = "\n".join(b for b in blocks if b is not None)
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith(("- ", "* ")):
            bullets.append(
                {
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": line[2:]}],
                        }
                    ],
                }
            )
            continue
        flush_bullets()
        if not line.strip():
            continue
        content.append(
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": line}],
            }
        )
    flush_bullets()

    if not content:
        content = [{"type": "paragraph", "content": [{"type": "text", "text": ""}]}]
    return {"type": "doc", "version": 1, "content": content}


def adf_to_text(node: Any) -> str:
    """ADF 를 평문으로 환원한다 (Jira description/comment 표시용)."""

    texts: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            ntype = n.get("type")
            if ntype == "text" and n.get("text"):
                texts.append(str(n["text"]))
            elif ntype == "hardBreak":
                texts.append("\n")
            elif ntype in {"paragraph", "heading", "listItem", "blockquote"}:
                for child in n.get("content") or []:
                    walk(child)
                texts.append("\n")
            elif ntype in {"bulletList", "orderedList", "codeBlock"}:
                for child in n.get("content") or []:
                    walk(child)
            else:
                for child in n.get("content") or []:
                    walk(child)
        elif isinstance(n, list):
            for child in n:
                walk(child)

    walk(node)
    return "".join(texts).strip()


class AtlassianClient:
    """Jira + Confluence REST 클라이언트 (단일 세션 공유)."""

    def __init__(
        self,
        site: str = DEFAULT_SITE,
        *,
        root: str | Path = DEFAULT_ROOT,
        credentials: Credentials | None = None,
        timeout: int = 30,
        max_retries: int = 2,
        session: requests.Session | None = None,
    ) -> None:
        self.site = site.rstrip("/")
        self.jira_base = f"{self.site}/rest/api/3/"
        self.agile_base = f"{self.site}/rest/agile/1.0/"
        self.confluence_base = f"{self.site}/wiki/rest/api/"
        self.timeout = timeout
        self.max_retries = max_retries
        self.credentials = credentials or load_credentials(root)

        self.session = session or requests.Session()
        self.session.auth = HTTPBasicAuth(
            self.credentials.email, self.credentials.token
        )
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "hermes-atlassian-client/1.0",
            }
        )

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        data: Any = None,
        files: Any = None,
        headers: dict | None = None,
        raw: bool = False,
        allow_404: bool = False,
    ) -> Any:
        """재시도(연결 오류/5xx/429) 포함 단일 요청 실행."""

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    data=data,
                    files=files,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:  # 네트워크 계층 실패
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise AtlassianError(
                    f"네트워크 오류: {type(exc).__name__}", url=url
                ) from exc

            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if (retry_after or "").isdigit() else 1.5 * (attempt + 1)
                time.sleep(min(delay, 10.0))
                continue

            if resp.status_code == 404 and allow_404:
                return None

            if not resp.ok:
                body: Any
                try:
                    body = resp.json()
                except ValueError:
                    body = resp.text
                raise AtlassianError(
                    f"{method} 요청 실패", status=resp.status_code, url=url, body=redact(body)
                )

            if raw:
                return resp
            if not resp.content:
                return None
            try:
                return resp.json()
            except ValueError:
                return resp.text

        raise AtlassianError("재시도 한도를 초과했습니다", url=url) from last_error

    # 그래프QL/검색 공통 페이지네이션
    def _paginate(
        self,
        fetch: Any,
        extract: Any,
        *,
        max_results: int | None,
        page_size: int = 50,
    ) -> list[Any]:
        collected: list[Any] = []
        token: str | None = None
        start = 0
        while True:
            batch, token, start = fetch(page_size=page_size, token=token, start=start)
            collected.extend(extract(batch))
            if max_results is not None and len(collected) >= max_results:
                return collected[:max_results]
            if not batch:
                return collected
            if len(batch) < page_size and token is None:
                return collected

    # ------------------------------------------------------------------
    # Jira - 읽기
    # ------------------------------------------------------------------
    def jira_myself(self) -> dict:
        return self._request("GET", self.jira_base + "myself")

    def jira_my_permissions(
        self, permissions: Sequence[str] | None = None, *, project_key: str | None = None
    ) -> dict:
        """현재 사용자의 권한을 조회한다 (부작용 없음).

        ``permissions`` 예: ``["CREATE_ISSUES", "EDIT_ISSUES", "DELETE_ISSUES",
        "TRANSITION_ISSUES", "WORK_ON_ISSUES", "ADD_COMMENTS"]``.
        ``project_key`` 를 주면 해당 프로젝트 범위로 평가한다. 권한은
        프로젝트 단위로 갈리므로 삭제/편집 전에는 반드시 프로젝트 스코프로
        확인해야 한다.
        """

        perms = ",".join(permissions or ["CREATE_ISSUES", "EDIT_ISSUES", "WORK_ON_ISSUES"])
        params: dict[str, Any] = {"permissions": perms}
        if project_key:
            params["projectKey"] = project_key
        data = self._request("GET", self.jira_base + "mypermissions", params=params) or {}
        return {
            "scope": project_key or "global",
            "permissions": {
                k: bool((v or {}).get("havePermission"))
                for k, v in (data.get("permissions") or {}).items()
            },
        }

    def jira_can(self, project_key: str, permission: str) -> bool:
        """프로젝트 범위 단일 권한 확인 편의 함수."""

        result = self.jira_my_permissions([permission], project_key=project_key)
        return bool(result["permissions"].get(permission))

    def jira_projects(self, *, limit: int = 50, query: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"maxResults": min(limit, 1000), "orderBy": "name"}
        if query:
            params["query"] = query
        data = self._request("GET", self.jira_base + "project/search", params=params)
        return list((data or {}).get("values") or [])

    def jira_createmeta(
        self, *, project_keys: Sequence[str] | None = None, max_projects: int = 5
    ) -> dict:
        """생성 가능한 프로젝트/이슈유형/필수 필드를 조회한다 (쓰기 범위 증명)."""

        params: dict[str, Any] = {
            "expand": "projects.issuetypes.fields",
            "maxResults": max_projects,
        }
        if project_keys:
            params["projectKeys"] = ",".join(project_keys)
        return self._request("GET", self.jira_base + "issue/createmeta", params=params)

    def jira_search(
        self,
        jql: str,
        *,
        fields: Sequence[str] | None = None,
        max_results: int = 50,
        expand: str | None = None,
        all_pages: bool = False,
    ) -> list[dict]:
        """``POST /search/jql`` 로 이슈를 조회한다.

        Cloud 신형 엔드포인트는 ``total`` 을 주지 않으므로 ``issues[]`` 와
        ``nextPageToken`` 만 신뢰한다.
        """

        issues: list[dict] = []
        token: str | None = None
        page_size = min(max_results, 100) if not all_pages else 100
        field_list = list(fields or DEFAULT_JIRA_FIELDS)

        while True:
            payload: dict[str, Any] = {
                "jql": jql,
                "fields": field_list,
                "maxResults": page_size,
            }
            if expand:
                payload["expand"] = expand
            if token:
                payload["nextPageToken"] = token

            data = self._request("POST", self.jira_base + "search/jql", json_body=payload) or {}
            batch = data.get("issues") or []
            issues.extend(batch)

            if not all_pages and len(issues) >= max_results:
                return issues[:max_results]

            token = data.get("nextPageToken")
            if not batch or not token or data.get("isLast") is True:
                return issues

    def jira_issue(self, issue_key: str, *, fields: Sequence[str] | None = None) -> dict:
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)
        data = self._request(
            "GET", self.jira_base + f"issue/{issue_key}", params=params or None, allow_404=True
        )
        if data is None:
            raise AtlassianError(f"이슈를 찾을 수 없습니다: {issue_key}", status=404)
        return data

    def jira_worklogs(self, issue_key: str, *, all_pages: bool = True) -> list[dict]:
        entries: list[dict] = []
        start = 0
        while True:
            data = self._request(
                "GET",
                self.jira_base + f"issue/{issue_key}/worklog",
                params={"startAt": start, "maxResults": 100},
            ) or {}
            batch = data.get("worklogs") or []
            entries.extend(batch)
            total = data.get("total") or 0
            start += len(batch)
            if not all_pages or not batch or start >= total:
                return entries

    def jira_users(self, query: str = "", *, limit: int = 200) -> list[dict]:
        users: list[dict] = []
        start = 0
        while len(users) < limit:
            data = self._request(
                "GET",
                self.jira_base + "users/search",
                params={"startAt": start, "maxResults": min(100, limit - len(users)), "query": query},
            ) or []
            if not isinstance(data, list) or not data:
                break
            users.extend(data)
            if len(data) < 100:
                break
            start += len(data)
        return users[:limit]

    def jira_transitions(self, issue_key: str) -> list[dict]:
        data = self._request(
            "GET", self.jira_base + f"issue/{issue_key}/transitions"
        ) or {}
        return list(data.get("transitions") or [])

    def jira_boards(self, *, limit: int = 50) -> list[dict]:
        data = self._request(
            "GET", self.agile_base + "board", params={"maxResults": min(limit, 50)}
        ) or {}
        return list(data.get("values") or [])

    def jira_sprints(self, board_id: int, *, state: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"maxResults": 50}
        if state:
            params["state"] = state
        data = self._request(
            "GET", self.agile_base + f"board/{board_id}/sprint", params=params
        ) or {}
        return list(data.get("values") or [])

    # ------------------------------------------------------------------
    # Jira - 쓰기 (dry_run 기본)
    # ------------------------------------------------------------------
    def jira_create_issue(
        self,
        project_key: str,
        summary: str,
        *,
        issue_type: str = "작업",
        description: str | None = None,
        assignee_account_id: str | None = None,
        reporter_account_id: str | None = None,
        start_date: str | None = None,
        due_date: str | None = None,
        labels: Sequence[str] | None = None,
        parent_key: str | None = None,
        extra_fields: dict | None = None,
        dry_run: bool = True,
    ) -> dict:
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
        if description:
            fields["description"] = to_adf(description)
        if assignee_account_id:
            fields["assignee"] = {"accountId": assignee_account_id}
        if reporter_account_id:
            fields["reporter"] = {"accountId": reporter_account_id}
        if start_date:
            fields[CF_START_DATE] = start_date
        if due_date:
            fields["duedate"] = due_date
        if labels:
            fields["labels"] = list(labels)
        if parent_key:
            fields["parent"] = {"key": parent_key}
        if extra_fields:
            fields.update(extra_fields)

        payload = {"fields": fields}
        if dry_run:
            return {"dry_run": True, "method": "POST", "url": self.jira_base + "issue", "payload": payload}
        return self._request("POST", self.jira_base + "issue", json_body=payload)

    def jira_update_issue(
        self,
        issue_key: str,
        *,
        summary: str | None = None,
        description: str | None = None,
        labels: Sequence[str] | None = None,
        due_date: str | None = None,
        start_date: str | None = None,
        assignee_account_id: str | None = None,
        extra_fields: dict | None = None,
        dry_run: bool = True,
    ) -> dict:
        """이슈 필드를 수정한다 (``EDIT_ISSUES`` 필요)."""

        fields: dict[str, Any] = {}
        if summary is not None:
            fields["summary"] = summary
        if description is not None:
            fields["description"] = to_adf(description)
        if labels is not None:
            fields["labels"] = list(labels)
        if due_date is not None:
            fields["duedate"] = due_date
        if start_date is not None:
            fields[CF_START_DATE] = start_date
        if assignee_account_id is not None:
            fields["assignee"] = {"accountId": assignee_account_id}
        if extra_fields:
            fields.update(extra_fields)
        if not fields:
            raise AtlassianError("수정할 필드가 없습니다.")

        url = self.jira_base + f"issue/{issue_key}"
        payload = {"fields": fields}
        if dry_run:
            return {"dry_run": True, "method": "PUT", "url": url, "payload": payload}
        self._request("PUT", url, json_body=payload)
        return {"ok": True, "issue": issue_key, "updated_fields": sorted(fields)}

    def jira_add_worklog(
        self,
        issue_key: str,
        time_spent: str,
        *,
        comment: str | None = None,
        started: str | None = None,
        adjust_estimate: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        payload: dict[str, Any] = {"timeSpent": time_spent}
        if comment:
            payload["comment"] = to_adf(comment)
        if started:
            payload["started"] = started
        if adjust_estimate:
            payload["adjustEstimate"] = adjust_estimate

        url = self.jira_base + f"issue/{issue_key}/worklog"
        if dry_run:
            return {"dry_run": True, "method": "POST", "url": url, "payload": payload}
        return self._request("POST", url, json_body=payload)

    def jira_update_worklog(
        self,
        issue_key: str,
        worklog_id: str,
        *,
        time_spent: str | None = None,
        comment: str | None = None,
        started: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        payload: dict[str, Any] = {}
        if time_spent is not None:
            payload["timeSpent"] = time_spent
        if comment is not None:
            payload["comment"] = to_adf(comment)
        if started is not None:
            payload["started"] = started

        url = self.jira_base + f"issue/{issue_key}/worklog/{worklog_id}"
        if dry_run:
            return {"dry_run": True, "method": "PUT", "url": url, "payload": payload}
        return self._request("PUT", url, json_body=payload)

    def jira_add_comment(
        self, issue_key: str, comment: str, *, dry_run: bool = True
    ) -> dict:
        url = self.jira_base + f"issue/{issue_key}/comment"
        payload = {"body": to_adf(comment)}
        if dry_run:
            return {"dry_run": True, "method": "POST", "url": url, "payload": payload}
        return self._request("POST", url, json_body=payload)

    def jira_comments(self, issue_key: str, *, max_results: int = 100) -> list[dict]:
        data = self._request(
            "GET",
            self.jira_base + f"issue/{issue_key}/comment",
            params={"maxResults": max_results, "orderBy": "created"},
        ) or {}
        return list(data.get("comments") or [])

    def jira_delete_issue(
        self, issue_key: str, *, delete_subtasks: bool = False, dry_run: bool = True
    ) -> dict:
        url = self.jira_base + f"issue/{issue_key}"
        params = {"deleteSubtasks": "true" if delete_subtasks else "false"}
        if dry_run:
            return {"dry_run": True, "method": "DELETE", "url": url, "params": params}
        self._request("DELETE", url, params=params)
        return {"ok": True, "deleted": issue_key}

    def jira_transition(
        self,
        issue_key: str,
        target_names: Sequence[str],
        *,
        comment: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        available = self.jira_transitions(issue_key)
        names = [t.get("to", {}).get("name", "") for t in available]
        match = next((t for t in available if t.get("to", {}).get("name") in target_names), None)
        if match is None:
            raise AtlassianError(
                f"전이 대상을 찾을 수 없습니다: {target_names} (가능: {names})"
            )

        url = self.jira_base + f"issue/{issue_key}/transitions"
        payload: dict[str, Any] = {"transition": {"id": match["id"]}}
        if comment:
            payload["update"] = {"comment": [{"add": {"body": to_adf(comment)}}]}

        if dry_run:
            return {
                "dry_run": True,
                "method": "POST",
                "url": url,
                "payload": payload,
                "target": match.get("to", {}).get("name"),
            }
        self._request("POST", url, json_body=payload)
        return {"ok": True, "issue": issue_key, "to": match.get("to", {}).get("name")}

    def jira_add_attachment(
        self, issue_key: str, file_path: str | Path, *, dry_run: bool = True
    ) -> dict:
        path = Path(file_path)
        if not path.is_file():
            raise AtlassianError(f"첨부 파일이 없습니다: {path}")
        url = self.jira_base + f"issue/{issue_key}/attachments"
        if dry_run:
            return {
                "dry_run": True,
                "method": "POST",
                "url": url,
                "payload": {
                    "file": str(path),
                    "size": path.stat().st_size,
                    "headers": {"X-Atlassian-Token": "no-check"},
                },
            }
        # 첨부는 multipart 이므로 JSON 헤더를 제거하고 X-Atlassian-Token 을 붙인다.
        headers = {"X-Atlassian-Token": "no-check", "Accept": "application/json"}
        with path.open("rb") as fh:
            return self._request(
                "POST",
                url,
                files={"file": (path.name, fh, "application/octet-stream")},
                headers=headers,
            )

    # ------------------------------------------------------------------
    # Confluence - 읽기
    # ------------------------------------------------------------------
    def confluence_spaces(
        self, *, limit: int = 25, space_type: str = "global", start: int = 0
    ) -> list[dict]:
        data = self._request(
            "GET",
            self.confluence_base + "space",
            params={"start": start, "limit": min(limit, 500), "type": space_type},
        ) or {}
        return list(data.get("results") or [])

    def confluence_space(self, space_key: str) -> dict | None:
        return self._request(
            "GET",
            self.confluence_base + f"space/{space_key}",
            params={"expand": "description.plain,homepage"},
            allow_404=True,
        )

    def confluence_search(
        self, cql: str, *, limit: int = 25, expand: str | None = None, all_pages: bool = False
    ) -> list[dict]:
        results: list[dict] = []
        start = 0
        while True:
            params: dict[str, Any] = {
                "cql": cql,
                "start": start,
                "limit": min(limit if not all_pages else 100, 100),
            }
            if expand:
                params["expand"] = expand
            data = self._request("GET", self.confluence_base + "search", params=params) or {}
            batch = data.get("results") or []
            results.extend(batch)

            if not all_pages and len(results) >= limit:
                return results[:limit]
            if not batch:
                return results
            start += len(batch)
            if start >= (data.get("totalSize") or data.get("size") or 0):
                return results

    def confluence_page(self, page_id: str | int, *, body: bool = True) -> dict | None:
        expand = "version,space,ancestors"
        if body:
            expand += ",body.storage,body.view"
        return self._request(
            "GET",
            self.confluence_base + f"content/{page_id}",
            params={"expand": expand},
            allow_404=True,
        )

    def confluence_page_by_title(self, space_key: str, title: str) -> dict | None:
        data = self._request(
            "GET",
            self.confluence_base + "content",
            params={
                "spaceKey": space_key,
                "title": title,
                "type": "page",
                "expand": "version,space,body.storage",
            },
            allow_404=True,
        )
        results = ((data or {}).get("results") or []) if data else []
        return results[0] if results else None

    def confluence_children(self, page_id: str | int, *, limit: int = 100) -> list[dict]:
        data = self._request(
            "GET",
            self.confluence_base + f"content/{page_id}/child/page",
            params={"limit": min(limit, 100), "expand": "version"},
        ) or {}
        return list(data.get("results") or [])

    def confluence_blogposts(self, space_key: str, *, limit: int = 100) -> list[dict]:
        # v1 content 는 orderBy=created 를 거부하므로 정렬은 클라이언트에서 한다.
        data = self._request(
            "GET",
            self.confluence_base + "content",
            params={
                "spaceKey": space_key,
                "type": "blogpost",
                "limit": min(limit, 100),
                "expand": "version,history",
            },
        ) or {}
        posts = list(data.get("results") or [])

        def created_key(p: dict) -> str:
            return (
                ((p.get("history") or {}).get("createdDate"))
                or p.get("createdDate")
                or ""
            )

        posts.sort(key=created_key, reverse=True)
        return posts

    def confluence_attachments(self, page_id: str | int) -> list[dict]:
        data = self._request(
            "GET", self.confluence_base + f"content/{page_id}/child/attachment"
        ) or {}
        return list(data.get("results") or [])

    def confluence_download(
        self, download_path: str, *, dest: str | Path
    ) -> Path:
        """``_links.download`` 같은 상대경로를 받아 파일로 저장한다."""

        url = download_path if download_path.startswith("http") else self.site + download_path
        resp = self._request("GET", url, raw=True)
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(resp.content)
        return dest_path

    # ------------------------------------------------------------------
    # Confluence - 쓰기 (dry_run 기본)
    # ------------------------------------------------------------------
    def confluence_create_page(
        self,
        space_key: str,
        title: str,
        body_html: str,
        *,
        parent_id: str | int | None = None,
        dry_run: bool = True,
    ) -> dict:
        payload: dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {"storage": {"value": body_html, "representation": "storage"}},
        }
        if parent_id:
            payload["ancestors"] = [{"id": str(parent_id)}]

        url = self.confluence_base + "content"
        if dry_run:
            return {"dry_run": True, "method": "POST", "url": url, "payload": payload}
        return self._request("POST", url, json_body=payload)

    def confluence_update_page(
        self,
        page_id: str | int,
        title: str,
        body_html: str,
        *,
        version_comment: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        current = self.confluence_page(page_id, body=False)
        if current is None:
            raise AtlassianError(f"페이지를 찾을 수 없습니다: {page_id}", status=404)
        next_version = int((current.get("version") or {}).get("number") or 1) + 1

        payload: dict[str, Any] = {
            "id": str(page_id),
            "type": "page",
            "title": title,
            "version": {"number": next_version},
            "body": {"storage": {"value": body_html, "representation": "storage"}},
        }
        if version_comment:
            payload["version"]["message"] = version_comment

        url = self.confluence_base + f"content/{page_id}"
        if dry_run:
            return {
                "dry_run": True,
                "method": "PUT",
                "url": url,
                "payload": payload,
                "current_version": next_version - 1,
            }
        return self._request("PUT", url, json_body=payload)

    def confluence_delete_page(self, page_id: str | int, *, dry_run: bool = True) -> dict:
        url = self.confluence_base + f"content/{page_id}"
        if dry_run:
            return {"dry_run": True, "method": "DELETE", "url": url}
        self._request("DELETE", url)
        return {"ok": True, "deleted": str(page_id)}

    # ------------------------------------------------------------------
    # 편의: 사람이 읽는 요약
    # ------------------------------------------------------------------
    def health(self) -> dict:
        """인증/도달/쓰기범위를 한 번에 점검한다 (부작용 없음)."""

        out: dict[str, Any] = {"site": self.site, "auth": self.credentials.describe()}
        try:
            me = self.jira_myself()
            out["jira_user"] = me.get("displayName")
            out["jira_account_prefix"] = (me.get("accountId") or "")[:8]
        except AtlassianError as exc:
            out["jira_user"] = f"ERROR: {exc}"

        try:
            perms = self.jira_my_permissions(
                ["CREATE_ISSUES", "EDIT_ISSUES", "DELETE_ISSUES",
                 "TRANSITION_ISSUES", "WORK_ON_ISSUES", "ADD_COMMENTS"]
            )
            out["jira_permissions_global"] = perms["permissions"]
        except AtlassianError as exc:
            out["jira_permissions_global"] = f"ERROR: {exc}"

        try:
            projects = self.jira_projects(limit=5)
            out["jira_projects_sample"] = [(p.get("key"), p.get("name")) for p in projects]
        except AtlassianError as exc:
            out["jira_projects_sample"] = f"ERROR: {exc}"

        try:
            spaces = self.confluence_spaces(limit=5)
            out["confluence_spaces_sample"] = [(s.get("key"), s.get("name")) for s in spaces]
        except AtlassianError as exc:
            out["confluence_spaces_sample"] = f"ERROR: {exc}"

        return out
