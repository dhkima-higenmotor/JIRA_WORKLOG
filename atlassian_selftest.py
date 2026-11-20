"""Atlassian 연동 왕복(쓰기 포함) 자체 검증 스크립트.

읽기뿐 아니라 실제 생성/수정/삭제까지 확인하고, 검증 중 만든 산출물은
반드시 삭제하여 흔적을 남기지 않는다.

* Confluence: 자격증명 사용자의 **개인 스페이스**에서 페이지만 생성/수정/삭제.
* Jira: ``--project`` 로 지정한 **테스트 프로젝트**(기본 ``VT``)에서 이슈를
  생성한 뒤 worklog/comment 를 검증하고 삭제.

사용::

    .venv/Scripts/python.exe atlassian_selftest.py
    .venv/Scripts/python.exe atlassian_selftest.py --project VT --skip-jira
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timedelta, timezone

from atlassian_client import AtlassianClient, AtlassianError

SEOUL = timezone(timedelta(hours=9))
STAMP = datetime.now(SEOUL).strftime("%Y%m%d-%H%M%S")

report: list[tuple[str, str, str]] = []


def step(scope: str, name: str, ok: bool, detail: str = "") -> None:
    report.append(("PASS" if ok else "FAIL", f"{scope}/{name}", detail))


def skip(scope: str, name: str, reason: str) -> None:
    report.append(("SKIP", f"{scope}/{name}", reason))


def check(scope: str, name: str, cond: bool, detail: str = "") -> bool:
    step(scope, name, bool(cond), detail)
    return bool(cond)


# ──────────────────────────────────────────────────────────────────────
# Confluence 왕복
# ──────────────────────────────────────────────────────────────────────
def test_confluence(cli: AtlassianClient) -> None:
    scope = "confluence"
    me = cli.jira_myself()
    account_id = me.get("accountId") or ""
    personal_key = f"~{account_id}"
    space = cli.confluence_space(personal_key)
    if space is None:
        step(scope, "personal-space", False, f"개인 스페이스 없음: {personal_key}")
        return
    step(scope, "personal-space", True, f"{personal_key} ({space.get('name')})")

    title = f"[Hermes 검증] 연동 자체테스트 {STAMP}"
    page_id: str | None = None
    try:
        created = cli.confluence_create_page(
            personal_key,
            title,
            f"<p>자체 검증 페이지입니다.</p><ul><li>시각: {STAMP}</li><li>도구: atlassian_client</li></ul>",
            dry_run=False,
        )
        page_id = str(created.get("id") or "")
        check(scope, "create-page", bool(page_id), f"id={page_id}")

        page = cli.confluence_page(page_id)
        body = ((page or {}).get("body") or {}).get("storage", {}).get("value", "")
        check(
            scope,
            "read-page",
            page is not None and page.get("title") == title and STAMP in body,
            f"title 일치={page.get('title') == title if page else False}, 본문 {len(body)}자",
        )

        updated = cli.confluence_update_page(
            page_id,
            title,
            f"<p>수정본 {STAMP}</p>",
            version_comment="Hermes 자체 검증",
            dry_run=False,
        )
        new_ver = (updated.get("version") or {}).get("number")
        check(scope, "update-page", new_ver == 2, f"version={new_ver}")

        page2 = cli.confluence_page(page_id, body=True)
        body2 = ((page2 or {}).get("body") or {}).get("storage", {}).get("value", "")
        check(scope, "verify-update", "수정본" in body2, f"본문 {len(body2)}자")
    finally:
        if page_id:
            try:
                cli.confluence_delete_page(page_id, dry_run=False)
                gone = cli.confluence_page(page_id) is None
                step(scope, "delete-page", gone, f"id={page_id} 잔존={not gone}")
            except AtlassianError as exc:
                step(scope, "delete-page", False, str(exc))


# ──────────────────────────────────────────────────────────────────────
# Jira 왕복
# ──────────────────────────────────────────────────────────────────────
def test_jira(cli: AtlassianClient, project_key: str, *, force_create: bool = False) -> None:
    scope = "jira"
    can_create = cli.jira_can(project_key, "CREATE_ISSUES")
    can_delete = cli.jira_can(project_key, "DELETE_ISSUES")
    step(
        scope,
        "permission-probe",
        can_create,
        f"{project_key} create={can_create} delete={can_delete}",
    )
    if not can_create:
        skip(scope, "roundtrip", f"{project_key} 이슈 생성 권한 없음")
        return
    if not can_delete and not force_create:
        # 삭제 권한이 없으면 잔여물을 남기므로 아예 생성하지 않는다.
        skip(
            scope,
            "roundtrip",
            f"{project_key} DELETE_ISSUES 없음 → 잔여물 방지 위해 생성 생략 "
            f"(강제하려면 --force-create)",
        )
        return

    meta = cli.jira_createmeta(project_keys=[project_key], max_projects=1)
    projects = meta.get("projects") or []
    if not projects:
        step(scope, "createmeta", False, f"{project_key} 생성 불가")
        return
    issue_types = [t.get("name") for t in (projects[0].get("issuetypes") or [])]
    issue_type = "작업" if "작업" in issue_types else (issue_types[0] if issue_types else "작업")
    step(scope, "createmeta", True, f"{project_key} 이슈유형={issue_types}")

    key: str | None = None
    try:
        created = cli.jira_create_issue(
            project_key,
            f"[Hermes 검증] 연동 자체테스트 {STAMP}",
            issue_type=issue_type,
            description=f"자체 검증용 이슈입니다.\n- 시각: {STAMP}\n- 검증 후 자동 삭제됩니다.",
            labels=["hermes-selftest"],
            dry_run=False,
        )
        key = created.get("key")
        check(scope, "create-issue", bool(key), f"key={key}")

        issue = cli.jira_issue(key)
        check(
            scope,
            "read-issue",
            (issue.get("fields") or {}).get("summary", "").startswith("[Hermes 검증]"),
            (issue.get("fields") or {}).get("summary", ""),
        )

        started = (datetime.now(SEOUL) - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%S.000%z"
        )
        wl = cli.jira_add_worklog(
            key, "30m", comment="자체 검증 worklog", started=started, dry_run=False
        )
        check(scope, "add-worklog", bool(wl.get("id")), f"worklogId={wl.get('id')}")

        logs = cli.jira_worklogs(key)
        mine = [x for x in logs if str(x.get("id")) == str(wl.get("id"))]
        check(
            scope,
            "verify-worklog",
            bool(mine) and mine[0].get("timeSpent") == "30m",
            f"worklog 수={len(logs)}",
        )

        cm = cli.jira_add_comment(key, "자체 검증 코멘트", dry_run=False)
        check(scope, "add-comment", bool(cm.get("id")), f"commentId={cm.get('id')}")

        cms = cli.jira_comments(key)
        check(
            scope,
            "verify-comment",
            any(str(c.get("id")) == str(cm.get("id")) for c in cms),
            f"comment 수={len(cms)}",
        )

        transitions = cli.jira_transitions(key)
        print(f"    (참고) {key} 가능한 전이: "
              f"{[t.get('to', {}).get('name') for t in transitions]}")
    finally:
        if key:
            try:
                cli.jira_delete_issue(key, dry_run=False)
                try:
                    cli.jira_issue(key)
                    gone = False
                except AtlassianError as exc:
                    gone = exc.status == 404
                step(scope, "delete-issue", gone, f"key={key} 잔존={not gone}")
            except AtlassianError as exc:
                if exc.status == 403:
                    # 삭제 권한이 없으면 잔여임을 명시하고 종료 상태로 정리한다.
                    try:
                        cli.jira_update_issue(
                            key,
                            summary=f"[자동검증 잔여] {key} 삭제권한 없음",
                            dry_run=False,
                        )
                        moved = cli.jira_transition(
                            key, ["완료", "Done", "Closed", "닫힘"], dry_run=False
                        )
                        skip(
                            scope,
                            "delete-issue",
                            f"삭제 권한 없음 → '{moved.get('to')}' 처리로 정리 (key={key})",
                        )
                    except AtlassianError as exc2:
                        step(scope, "delete-issue", False, f"정리 실패: {exc2}")
                else:
                    step(scope, "delete-issue", False, str(exc))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Atlassian 연동 왕복 자체 검증")
    ap.add_argument("--project", default="VT", help="Jira 테스트 프로젝트 (기본 VT)")
    ap.add_argument("--skip-jira", action="store_true")
    ap.add_argument("--skip-confluence", action="store_true")
    ap.add_argument(
        "--force-create",
        action="store_true",
        help="삭제 권한이 없어도 이슈를 생성 (잔여물이 남을 수 있음)",
    )
    args = ap.parse_args(argv)

    cli = AtlassianClient()
    print(f"site={cli.site}  {cli.credentials.describe()}\n")

    try:
        if not args.skip_confluence:
            print("[Confluence] 개인 스페이스 왕복 검증")
            test_confluence(cli)
        if not args.skip_jira:
            print(f"[Jira] {args.project} 프로젝트 왕복 검증")
            test_jira(cli, args.project, force_create=args.force_create)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        step("global", "uncaught", False, "예외 발생")

    print("\n" + "=" * 62)
    failed = 0
    for status, name, detail in report:
        print(f"{status:4}  {name:34}  {detail}")
        if status == "FAIL":
            failed += 1
    print("=" * 62)
    print(f"총 {len(report)}개 항목, 실패 {failed}개")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
