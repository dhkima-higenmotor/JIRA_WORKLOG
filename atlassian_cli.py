"""Hermes agent 에서 호출하는 Atlassian CLI.

``atlassian_client.py`` 를 감싸는 얇은 명령행 계층이다. 읽기 명령은 즉시
실행되고, 쓰기 명령은 ``--apply`` 를 주기 전까지 dry-run 으로 payload 만
출력한다.

예시::

    py atlassian_cli.py health
    py atlassian_cli.py jira search --jql "assignee = currentUser() order by updated DESC"
    py atlassian_cli.py jira issue A01NH-33
    py atlassian_cli.py conf search --cql "type=page order by lastmodified desc" --limit 5
    py atlassian_cli.py jira add-worklog A01NH-33 --time 2h --comment "설계 검토"     # dry-run
    py atlassian_cli.py jira add-worklog A01NH-33 --time 2h --comment "설계 검토" --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from atlassian_client import (
    DEFAULT_SITE,
    AtlassianClient,
    AtlassianError,
    adf_to_text,
)

# ──────────────────────────────────────────────────────────────────────
# 출력 헬퍼
# ──────────────────────────────────────────────────────────────────────


def out_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _truncate(text: Any, width: int) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= width else s[: width - 1] + "…"


def out_rows(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    """고정폭 표로 출력한다 (한글 표시폭은 단순 근사)."""

    def w(s: Any) -> int:
        return sum(2 if ord(c) > 0x2E80 else 1 for c in str(s))

    widths = [w(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], w(cell))

    def pad(s: Any, width: int) -> str:
        return str(s) + " " * max(0, width - w(s))

    print("  ".join(pad(h, widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(pad(c, widths[i]) for i, c in enumerate(row)))


def print_result(result: dict, *, as_json: bool) -> None:
    if as_json:
        out_json(result)
        return
    if result.get("dry_run"):
        print("[DRY-RUN] 실제 변경 없음 — 실행하려면 --apply 를 추가하세요.")
        print(f"{result.get('method')} {result.get('url')}")
        out_json(result.get("payload"))
        return
    out_json(result)


# ──────────────────────────────────────────────────────────────────────
# 명령 구현
# ──────────────────────────────────────────────────────────────────────


def cmd_health(cli: AtlassianClient, args: argparse.Namespace) -> int:
    out_json(cli.health())
    return 0


def cmd_whoami(cli: AtlassianClient, args: argparse.Namespace) -> int:
    me = cli.jira_myself()
    if args.json:
        out_json(me)
    else:
        print(f"site       : {cli.site}")
        print(f"auth       : {cli.credentials.describe()}")
        print(f"displayName: {me.get('displayName')}")
        print(f"accountId  : {(me.get('accountId') or '')[:8]}...")
        print(f"locale     : {me.get('locale')}  timeZone: {me.get('timeZone')}")
    return 0


def cmd_jira_projects(cli: AtlassianClient, args: argparse.Namespace) -> int:
    projects = cli.jira_projects(limit=args.limit, query=args.query)
    if args.json:
        out_json(projects)
        return 0
    out_rows(
        ["KEY", "NAME", "TYPE"],
        [
            (p.get("key"), _truncate(p.get("name"), 46), p.get("projectTypeKey", ""))
            for p in projects
        ],
    )
    print(f"\n{len(projects)}개")
    return 0


def cmd_jira_search(cli: AtlassianClient, args: argparse.Namespace) -> int:
    fields = [f.strip() for f in args.fields.split(",")] if args.fields else None
    issues = cli.jira_search(
        args.jql, fields=fields, max_results=args.limit, all_pages=args.all
    )
    if args.json:
        out_json(issues)
        return 0
    rows = []
    for iss in issues:
        f = iss.get("fields") or {}
        rows.append(
            (
                iss.get("key"),
                _truncate((f.get("summary") or ""), 52),
                (f.get("status") or {}).get("name", ""),
                (f.get("assignee") or {}).get("displayName", "-"),
                (f.get("updated") or "")[:16],
            )
        )
    out_rows(["KEY", "SUMMARY", "STATUS", "ASSIGNEE", "UPDATED"], rows)
    print(f"\n{len(issues)}건")
    return 0


def cmd_jira_issue(cli: AtlassianClient, args: argparse.Namespace) -> int:
    iss = cli.jira_issue(args.key)
    if args.json:
        out_json(iss)
        return 0
    f = iss.get("fields") or {}
    print(f"{iss.get('key')}  {f.get('summary')}")
    print(f"  프로젝트 : {(f.get('project') or {}).get('name')}")
    print(f"  유형     : {(f.get('issuetype') or {}).get('name')}")
    print(f"  상태     : {(f.get('status') or {}).get('name')}")
    print(f"  담당자   : {(f.get('assignee') or {}).get('displayName', '-')}")
    print(f"  보고자   : {(f.get('reporter') or {}).get('displayName', '-')}")
    print(f"  생성/수정: {(f.get('created') or '')[:16]} / {(f.get('updated') or '')[:16]}")
    if f.get("duedate"):
        print(f"  마감일   : {f.get('duedate')}")
    desc = adf_to_text(f.get("description")) if f.get("description") else ""
    if desc:
        print("  설명     :")
        for line in desc.splitlines():
            print(f"    {line}")
    return 0


def cmd_jira_worklogs(cli: AtlassianClient, args: argparse.Namespace) -> int:
    logs = cli.jira_worklogs(args.key)
    if args.json:
        out_json(logs)
        return 0
    rows = []
    for lg in logs:
        rows.append(
            (
                lg.get("id"),
                (lg.get("author") or {}).get("displayName", ""),
                lg.get("timeSpent", ""),
                (lg.get("started") or "")[:16],
                _truncate(adf_to_text(lg.get("comment")) if lg.get("comment") else "", 40),
            )
        )
    out_rows(["ID", "AUTHOR", "SPENT", "STARTED", "COMMENT"], rows)
    total = sum(int((lg.get("timeSpentSeconds") or 0)) for lg in logs)
    print(f"\n{len(logs)}건, 합계 {total // 3600}h {(total % 3600) // 60}m")
    return 0


def cmd_jira_createmeta(cli: AtlassianClient, args: argparse.Namespace) -> int:
    keys = [k.strip() for k in args.project.split(",")] if args.project else None
    meta = cli.jira_createmeta(project_keys=keys, max_projects=args.limit)
    if args.json:
        out_json(meta)
        return 0
    projects = meta.get("projects") or []
    print(f"생성 가능 프로젝트 {len(projects)}개 (조회 상한 {args.limit})\n")
    for p in projects:
        types = [t.get("name") for t in (p.get("issuetypes") or [])]
        print(f"[{p.get('key')}] {p.get('name')}")
        print(f"    이슈유형: {', '.join(types) if types else '-'}")
        for t in (p.get("issuetypes") or [])[:1]:
            req = [
                f"{k}({v.get('name')})"
                for k, v in (t.get("fields") or {}).items()
                if v.get("required")
            ]
            if req:
                print(f"    '{t.get('name')}' 필수 필드: {', '.join(req)}")
    return 0


def cmd_jira_create_issue(cli: AtlassianClient, args: argparse.Namespace) -> int:
    result = cli.jira_create_issue(
        args.project,
        args.summary,
        issue_type=args.type,
        description=args.description,
        assignee_account_id=args.assignee,
        reporter_account_id=args.reporter,
        start_date=args.start,
        due_date=args.due,
        labels=args.labels.split(",") if args.labels else None,
        parent_key=args.parent,
        dry_run=not args.apply,
    )
    print_result(result, as_json=args.json)
    return 0


def cmd_jira_add_worklog(cli: AtlassianClient, args: argparse.Namespace) -> int:
    result = cli.jira_add_worklog(
        args.key,
        args.time,
        comment=args.comment,
        started=args.started,
        dry_run=not args.apply,
    )
    print_result(result, as_json=args.json)
    return 0


def cmd_jira_update_worklog(cli: AtlassianClient, args: argparse.Namespace) -> int:
    result = cli.jira_update_worklog(
        args.key,
        args.worklog_id,
        time_spent=args.time,
        comment=args.comment,
        started=args.started,
        dry_run=not args.apply,
    )
    print_result(result, as_json=args.json)
    return 0


def cmd_jira_comment(cli: AtlassianClient, args: argparse.Namespace) -> int:
    result = cli.jira_add_comment(args.key, args.text, dry_run=not args.apply)
    print_result(result, as_json=args.json)
    return 0


def cmd_jira_comments(cli: AtlassianClient, args: argparse.Namespace) -> int:
    comments = cli.jira_comments(args.key)
    if args.json:
        out_json(comments)
        return 0
    rows = [
        (
            c.get("id"),
            (c.get("author") or {}).get("displayName", ""),
            (c.get("created") or "")[:16],
            _truncate(adf_to_text(c.get("body")) if c.get("body") else "", 60),
        )
        for c in comments
    ]
    out_rows(["ID", "AUTHOR", "CREATED", "BODY"], rows)
    print(f"\n{len(comments)}건")
    return 0


def cmd_jira_update_issue(cli: AtlassianClient, args: argparse.Namespace) -> int:
    result = cli.jira_update_issue(
        args.key,
        summary=args.summary,
        description=args.description,
        labels=args.labels.split(",") if args.labels else None,
        due_date=args.due,
        start_date=args.start,
        assignee_account_id=args.assignee,
        dry_run=not args.apply,
    )
    print_result(result, as_json=args.json)
    return 0


def cmd_jira_permissions(cli: AtlassianClient, args: argparse.Namespace) -> int:
    perms = args.permissions.split(",") if args.permissions else None
    result = cli.jira_my_permissions(perms, project_key=args.project)
    if args.json:
        out_json(result)
    else:
        print(f"scope: {result['scope']}")
        for k, v in result["permissions"].items():
            print(f"  {k:22} {'허용' if v else '불가'}")
    return 0


def cmd_jira_delete_issue(cli: AtlassianClient, args: argparse.Namespace) -> int:
    result = cli.jira_delete_issue(
        args.key, delete_subtasks=args.subtasks, dry_run=not args.apply
    )
    print_result(result, as_json=args.json)
    return 0


def cmd_jira_transition(cli: AtlassianClient, args: argparse.Namespace) -> int:
    targets = [t.strip() for t in args.to.split(",")]
    result = cli.jira_transition(
        args.key, targets, comment=args.comment, dry_run=not args.apply
    )
    print_result(result, as_json=args.json)
    return 0


def cmd_jira_attach(cli: AtlassianClient, args: argparse.Namespace) -> int:
    result = cli.jira_add_attachment(args.key, args.file, dry_run=not args.apply)
    print_result(result, as_json=args.json)
    return 0


def cmd_jira_boards(cli: AtlassianClient, args: argparse.Namespace) -> int:
    boards = cli.jira_boards(limit=args.limit)
    if args.json:
        out_json(boards)
        return 0
    out_rows(
        ["ID", "NAME", "TYPE"],
        [(b.get("id"), _truncate(b.get("name"), 40), b.get("type", "")) for b in boards],
    )
    return 0


def cmd_jira_sprints(cli: AtlassianClient, args: argparse.Namespace) -> int:
    try:
        sprints = cli.jira_sprints(args.board, state=args.state)
    except AtlassianError as exc:
        if exc.status == 400:
            print(
                f"보드 {args.board} 는 스프린트를 지원하지 않습니다 (kanban 보드). "
                "scrum 보드를 지정하십시오."
            )
            return 0
        raise
    if args.json:
        out_json(sprints)
        return 0
    out_rows(
        ["ID", "NAME", "STATE", "START", "END"],
        [
            (
                s.get("id"),
                _truncate(s.get("name"), 34),
                s.get("state", ""),
                (s.get("startDate") or "")[:10],
                (s.get("endDate") or "")[:10],
            )
            for s in sprints
        ],
    )
    return 0


def cmd_conf_spaces(cli: AtlassianClient, args: argparse.Namespace) -> int:
    spaces = cli.confluence_spaces(limit=args.limit, space_type=args.type)
    if args.json:
        out_json(spaces)
        return 0
    out_rows(
        ["KEY", "NAME", "TYPE", "ID"],
        [
            (s.get("key"), _truncate(s.get("name"), 44), s.get("type", ""), s.get("id"))
            for s in spaces
        ],
    )
    print(f"\n{len(spaces)}개 (type={args.type})")
    return 0


def cmd_conf_search(cli: AtlassianClient, args: argparse.Namespace) -> int:
    results = cli.confluence_search(args.cql, limit=args.limit, all_pages=args.all)
    if args.json:
        out_json(results)
        return 0
    rows = []
    for r in results:
        content = r.get("content") or {}
        ctype = content.get("type") or r.get("entityType") or r.get("type") or ""
        cid = content.get("id") or r.get("id") or ""
        title = r.get("title") or content.get("title") or ""
        space = (r.get("resultGlobalContainer") or {}).get("title") or ""
        rows.append(
            (
                ctype,
                cid,
                _truncate(title, 46),
                _truncate(space, 18),
                str(r.get("lastModified") or r.get("friendlyLastModified") or "")[:16],
            )
        )
    out_rows(["TYPE", "ID", "TITLE", "SPACE", "UPDATED"], rows)
    print(f"\n{len(results)}건")
    return 0


def cmd_conf_page(cli: AtlassianClient, args: argparse.Namespace) -> int:
    page = cli.confluence_page(args.id, body=not args.no_body)
    if page is None:
        print(f"페이지를 찾을 수 없습니다: {args.id}", file=sys.stderr)
        return 2
    if args.json:
        out_json(page)
        return 0
    print(f"{page.get('title')}  (id={page.get('id')})")
    print(f"  Space   : {(page.get('space') or {}).get('key')} / {(page.get('space') or {}).get('name')}")
    print(f"  Version : {(page.get('version') or {}).get('number')}")
    links = page.get("_links") or {}
    if links.get("webui"):
        print(f"  URL     : {cli.site}/wiki{links['webui']}")
    body = ((page.get("body") or {}).get("storage") or {}).get("value") or ""
    if body and not args.no_body:
        limit = args.body_chars
        print(f"  본문(storage, {len(body)}자):")
        print(body[:limit] + ("\n  …(생략)" if len(body) > limit else ""))
    return 0


def cmd_conf_page_by_title(cli: AtlassianClient, args: argparse.Namespace) -> int:
    page = cli.confluence_page_by_title(args.space, args.title)
    if page is None:
        print(f"페이지가 없습니다: [{args.space}] {args.title}", file=sys.stderr)
        return 2
    if args.json:
        out_json(page)
    else:
        print(f"id={page.get('id')}  version={(page.get('version') or {}).get('number')}")
        print(f"title={page.get('title')}")
        links = page.get("_links") or {}
        if links.get("webui"):
            print(f"url={cli.site}/wiki{links['webui']}")
    return 0


def cmd_conf_children(cli: AtlassianClient, args: argparse.Namespace) -> int:
    kids = cli.confluence_children(args.id, limit=args.limit)
    if args.json:
        out_json(kids)
        return 0
    out_rows(
        ["ID", "TITLE", "VERSION"],
        [(k.get("id"), _truncate(k.get("title"), 50), (k.get("version") or {}).get("number")) for k in kids],
    )
    return 0


def cmd_conf_blogposts(cli: AtlassianClient, args: argparse.Namespace) -> int:
    posts = cli.confluence_blogposts(args.space, limit=args.limit)
    if args.json:
        out_json(posts)
        return 0
    out_rows(
        ["ID", "TITLE", "CREATED", "VERSION"],
        [
            (
                p.get("id"),
                _truncate(p.get("title"), 46),
                str(((p.get("history") or {}).get("createdDate")) or p.get("createdDate") or "")[:16],
                (p.get("version") or {}).get("number"),
            )
            for p in posts
        ],
    )
    print(f"\n{len(posts)}건")
    return 0


def _read_body(args: argparse.Namespace) -> str:
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8")
    if args.body:
        return args.body
    raise AtlassianError("--body 또는 --body-file 중 하나가 필요합니다.")


def cmd_conf_create_page(cli: AtlassianClient, args: argparse.Namespace) -> int:
    result = cli.confluence_create_page(
        args.space, args.title, _read_body(args), parent_id=args.parent, dry_run=not args.apply
    )
    print_result(result, as_json=args.json)
    return 0


def cmd_conf_update_page(cli: AtlassianClient, args: argparse.Namespace) -> int:
    result = cli.confluence_update_page(
        args.id,
        args.title,
        _read_body(args),
        version_comment=args.version_comment,
        dry_run=not args.apply,
    )
    print_result(result, as_json=args.json)
    return 0


def cmd_conf_delete_page(cli: AtlassianClient, args: argparse.Namespace) -> int:
    result = cli.confluence_delete_page(args.id, dry_run=not args.apply)
    print_result(result, as_json=args.json)
    return 0


def cmd_conf_download(cli: AtlassianClient, args: argparse.Namespace) -> int:
    path = cli.confluence_download(args.path, dest=args.dest)
    print(f"saved: {path} ({path.stat().st_size} bytes)")
    return 0


# ──────────────────────────────────────────────────────────────────────
# 파서
# ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="atlassian_cli",
        description="Jira/Confluence Cloud 조회·쓰기 CLI (Hermes agent 용)",
    )
    p.add_argument("--site", default=DEFAULT_SITE, help="Atlassian 사이트 URL")
    p.add_argument("--json", action="store_true", help="원본 JSON 으로 출력")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="인증/도달/쓰기권한 일괄 점검").set_defaults(func=cmd_health)
    sub.add_parser("whoami", help="현재 인증 사용자").set_defaults(func=cmd_whoami)

    # Jira
    j = sub.add_parser("jira", help="Jira 명령").add_subparsers(dest="jira_cmd", required=True)

    sp = j.add_parser("projects", help="프로젝트 목록")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--query", default=None)
    sp.set_defaults(func=cmd_jira_projects)

    sp = j.add_parser("search", help="JQL 검색")
    sp.add_argument("--jql", required=True)
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--fields", default=None, help="쉼표 구분 필드 목록")
    sp.add_argument("--all", action="store_true", help="전체 페이지 수집")
    sp.set_defaults(func=cmd_jira_search)

    sp = j.add_parser("issue", help="이슈 상세")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_jira_issue)

    sp = j.add_parser("worklogs", help="이슈 worklog 목록")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_jira_worklogs)

    sp = j.add_parser("createmeta", help="생성 가능 프로젝트/이슈유형/필수필드")
    sp.add_argument("--project", default=None, help="쉼표 구분 프로젝트 키")
    sp.add_argument("--limit", type=int, default=5)
    sp.set_defaults(func=cmd_jira_createmeta)

    sp = j.add_parser("create-issue", help="이슈 생성 (기본 dry-run)")
    sp.add_argument("--project", required=True)
    sp.add_argument("--summary", required=True)
    sp.add_argument("--type", default="작업")
    sp.add_argument("--description", default=None)
    sp.add_argument("--assignee", default=None, help="accountId")
    sp.add_argument("--reporter", default=None, help="accountId")
    sp.add_argument("--start", default=None, help="YYYY-MM-DD (customfield_10015)")
    sp.add_argument("--due", default=None, help="YYYY-MM-DD")
    sp.add_argument("--labels", default=None, help="쉼표 구분")
    sp.add_argument("--parent", default=None, help="상위 이슈 키")
    sp.add_argument("--apply", action="store_true", help="실제 반영")
    sp.set_defaults(func=cmd_jira_create_issue)

    sp = j.add_parser("add-worklog", help="worklog 등록 (기본 dry-run)")
    sp.add_argument("key")
    sp.add_argument("--time", required=True, help='예: "1h", "90m", "1d 2h"')
    sp.add_argument("--comment", default=None)
    sp.add_argument("--started", default=None, help="ISO8601 예: 2026-11-19T09:00:00.000+0900")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_jira_add_worklog)

    sp = j.add_parser("update-worklog", help="worklog 수정 (기본 dry-run)")
    sp.add_argument("key")
    sp.add_argument("--worklog-id", dest="worklog_id", required=True)
    sp.add_argument("--time", default=None)
    sp.add_argument("--comment", default=None)
    sp.add_argument("--started", default=None)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_jira_update_worklog)

    sp = j.add_parser("comment", help="코멘트 추가 (기본 dry-run)")
    sp.add_argument("key")
    sp.add_argument("--text", required=True)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_jira_comment)

    sp = j.add_parser("comments", help="코멘트 목록")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_jira_comments)

    sp = j.add_parser("delete-issue", help="이슈 삭제 (기본 dry-run)")
    sp.add_argument("key")
    sp.add_argument("--subtasks", action="store_true", help="하위 작업도 삭제")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_jira_delete_issue)

    sp = j.add_parser("update-issue", help="이슈 필드 수정 (기본 dry-run)")
    sp.add_argument("key")
    sp.add_argument("--summary", default=None)
    sp.add_argument("--description", default=None)
    sp.add_argument("--labels", default=None, help="쉼표 구분")
    sp.add_argument("--due", default=None, help="YYYY-MM-DD")
    sp.add_argument("--start", default=None, help="YYYY-MM-DD")
    sp.add_argument("--assignee", default=None, help="accountId")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_jira_update_issue)

    sp = j.add_parser("permissions", help="프로젝트/전역 권한 확인")
    sp.add_argument("--project", default=None, help="프로젝트 키 (없으면 전역)")
    sp.add_argument("--permissions", default=None, help="쉼표 구분 권한명")
    sp.set_defaults(func=cmd_jira_permissions)

    sp = j.add_parser("transition", help="상태 전이 (기본 dry-run)")
    sp.add_argument("key")
    sp.add_argument("--to", required=True, help="대상 상태명 (쉼표 구분 후보)")
    sp.add_argument("--comment", default=None)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_jira_transition)

    sp = j.add_parser("attach", help="첨부 업로드 (기본 dry-run)")
    sp.add_argument("key")
    sp.add_argument("--file", required=True)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_jira_attach)

    sp = j.add_parser("boards", help="보드 목록")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_jira_boards)

    sp = j.add_parser("sprints", help="보드의 스프린트")
    sp.add_argument("--board", type=int, required=True)
    sp.add_argument("--state", default=None, help="active,future,closed")
    sp.set_defaults(func=cmd_jira_sprints)

    # Confluence
    c = sub.add_parser("conf", help="Confluence 명령").add_subparsers(
        dest="conf_cmd", required=True
    )

    sp = c.add_parser("spaces", help="스페이스 목록")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--type", default="global", help="global | personal | all")
    sp.set_defaults(func=cmd_conf_spaces)

    sp = c.add_parser("search", help="CQL 검색")
    sp.add_argument("--cql", required=True)
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_conf_search)

    sp = c.add_parser("page", help="페이지 조회")
    sp.add_argument("id")
    sp.add_argument("--no-body", action="store_true")
    sp.add_argument("--body-chars", dest="body_chars", type=int, default=4000)
    sp.set_defaults(func=cmd_conf_page)

    sp = c.add_parser("page-by-title", help="제목으로 페이지 조회")
    sp.add_argument("--space", required=True)
    sp.add_argument("--title", required=True)
    sp.set_defaults(func=cmd_conf_page_by_title)

    sp = c.add_parser("children", help="하위 페이지")
    sp.add_argument("id")
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_conf_children)

    sp = c.add_parser("blogposts", help="블로그 포스트 목록")
    sp.add_argument("--space", required=True)
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_conf_blogposts)

    sp = c.add_parser("create-page", help="페이지 생성 (기본 dry-run)")
    sp.add_argument("--space", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--body", default=None, help="Confluence storage HTML")
    sp.add_argument("--body-file", dest="body_file", default=None)
    sp.add_argument("--parent", default=None)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_conf_create_page)

    sp = c.add_parser("update-page", help="페이지 수정 (기본 dry-run)")
    sp.add_argument("--id", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--body", default=None)
    sp.add_argument("--body-file", dest="body_file", default=None)
    sp.add_argument("--version-comment", dest="version_comment", default=None)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_conf_update_page)

    sp = c.add_parser("delete-page", help="페이지 삭제 (기본 dry-run)")
    sp.add_argument("--id", required=True)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_conf_delete_page)

    sp = c.add_parser("download", help="첨부/리소스 다운로드")
    sp.add_argument("--path", required=True, help="_links.download 값 또는 전체 URL")
    sp.add_argument("--dest", required=True)
    sp.set_defaults(func=cmd_conf_download)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cli = AtlassianClient(site=args.site)
    except AtlassianError as exc:
        print(f"[설정 오류] {exc}", file=sys.stderr)
        return 2

    if not hasattr(args, "func"):
        parser.print_help()
        return 2

    try:
        return int(args.func(cli, args) or 0)
    except AtlassianError as exc:
        print(f"[API 오류] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
