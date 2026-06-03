"""
weekly/weekly.py
Confluence Space의 블로그 게시물을 조회하고 Markdown 파일로 저장하는 GUI 도구.
main.py와 동일한 인증 방식(jira_api_token.txt / jira_api_email.txt) 사용.
"""

import re
import subprocess
import threading
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth
import tkinter as tk
from tkinter import ttk, messagebox

# ─── 경로 설정 ────────────────────────────────────────────────────────────────
# 이 파일은 weekly/ 하위에 있으므로 프로젝트 루트는 한 단계 상위
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
OUTPUT_DIR = _HERE / "output"

CONFLUENCE_BASE = "https://higen-rnd.atlassian.net/wiki/rest/api/"

# ─── 공통 유틸 ────────────────────────────────────────────────────────────────

def read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    return path.read_text(encoding="utf-8").strip()


def get_session(user_email: str, api_token: str) -> requests.Session:
    s = requests.Session()
    s.auth = HTTPBasicAuth(user_email, api_token)
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    return s


# ─── Confluence API 함수 ──────────────────────────────────────────────────────

def fetch_spaces(sess: requests.Session) -> list[dict]:
    """사용 가능한 모든 Confluence Space 목록을 반환한다."""
    spaces = []
    start = 0
    limit = 50
    while True:
        r = sess.get(
            CONFLUENCE_BASE + "space",
            params={"start": start, "limit": limit, "type": "global"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        spaces.extend(results)
        size = data.get("size", 0)
        start += size
        if start >= data.get("totalSize", start):
            break
        if size == 0:
            break
    return spaces


def fetch_blogposts(sess: requests.Session, space_key: str) -> list[dict]:
    """주어진 Space의 블로그 게시물 목록을 반환한다."""
    posts = []
    start = 0
    limit = 50
    while True:
        r = sess.get(
            CONFLUENCE_BASE + "content",
            params={
                "type": "blogpost",
                "spaceKey": space_key,
                "start": start,
                "limit": limit,
                "orderby": "history.createdDate desc",
                "expand": "history.createdBy,history.createdDate,version",
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        posts.extend(results)
        size = data.get("size", 0)
        start += size
        if start >= data.get("size", 0) + (start - size) or size < limit:
            break
    return posts


def fetch_blogpost_content(sess: requests.Session, post_id: str) -> str:
    """블로그 게시물의 본문(storage 형식 HTML)을 반환한다."""
    r = sess.get(
        CONFLUENCE_BASE + f"content/{post_id}",
        params={"expand": "body.storage,history.createdBy,history.createdDate,version"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("body", {}).get("storage", {}).get("value", "")


# ─── HTML → Markdown 변환 ─────────────────────────────────────────────────────

def _decode_entities(text: str) -> str:
    """HTML 엔티티를 디코딩한다."""
    entities = {
        "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&#39;": "'", "&nbsp;": " ",
        "&apos;": "'",
    }
    for ent, char in entities.items():
        text = text.replace(ent, char)
    return text


def _strip_tags(html: str) -> str:
    """HTML 태그를 모두 제거하고 순수 텍스트만 남긴다."""
    return re.sub(r"<[^>]+>", "", html)


def _fix_bold_markers(text: str) -> str:
    """
    Markdown 굵은글씨 마커 **...** 내부의 앞뒤 공백/ZWS를 제거한다.
    예: '** text **' → '**text**',  '**text **' → '**text**'
    내용이 공백뿐이면 마커 자체를 제거한다.
    """
    def _trim(m):
        content = m.group(1).strip(' \t\u200b')
        if not content:
            return ''  # 빈 굵은글씨 마커 제거
        return '**' + content + '**'
    return re.sub(r'\*\*(.+?)\*\*', _trim, text)


def _convert_time_tags(html: str) -> str:
    """
    Confluence의 <time datetime="YYYY-MM-DD" ... /> 태그를
    날짜 텍스트로 변환한다.
    예: <time datetime="2026-05-04" local-id="xxx" /> → 2026-05-04

    날짜 태그 앞뒤의 </strong>...<strong> 도 같이 소비하여
    변환 후 빈 **DATE** 패턴이 생기지 않도록 한다.
    """
    # 자체 닫힘: (</strong>)? <time datetime="..." ... /> (<strong>)?
    html = re.sub(
        r'(?:</strong>\s*)?<time[^>]+datetime=["\']([^"\']+)["\'][^>]*/?>(?:\s*<strong>)?',
        r' \1 ',
        html, flags=re.IGNORECASE,
    )
    # 열림+닫힘 쌍: (</strong>)? <time datetime="...">...</time> (<strong>)?
    html = re.sub(
        r'(?:</strong>\s*)?<time[^>]+datetime=["\']([^"\']+)["\'][^>]*>.*?</time>(?:\s*<strong>)?',
        r' \1 ',
        html, flags=re.DOTALL | re.IGNORECASE,
    )
    return html


def _inline_html_to_md(html: str) -> str:
    """셀 내부의 인라인 HTML(굵게, 기울임, 링크, 코드 등)을 Markdown으로 변환한다."""
    text = html

    # Confluence 날짜 태그를 텍스트로 변환 (매크로 제거 전에 수행)
    text = _convert_time_tags(text)

    # Confluence 매크로 태그 제거
    text = re.sub(r"<ac:[^>]*/?>", "", text, flags=re.DOTALL)
    text = re.sub(r"</ac:[^>]+>", "", text)
    text = re.sub(r"<ri:[^>]*/?>", "", text, flags=re.DOTALL)
    text = re.sub(r"</ri:[^>]+>", "", text)

    # 굵게 / 기울임
    text = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", text, flags=re.DOTALL | re.IGNORECASE)

    # 링크
    text = re.sub(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL | re.IGNORECASE)

    # 인라인 코드
    text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", text, flags=re.DOTALL | re.IGNORECASE)

    # 줄바꿈 → 공백 (테이블 셀 안에서는 줄바꿈 불가)
    text = re.sub(r"<br[^>]*/?>", " ", text, flags=re.IGNORECASE)

    # 단락 태그 → 공백
    text = re.sub(r"<p[^>]*>(.*?)</p>", r" \1 ", text, flags=re.DOTALL | re.IGNORECASE)

    # 나머지 태그 제거
    text = _strip_tags(text)

    # 엔티티 디코딩
    text = _decode_entities(text)

    # 줄바꿈/탭 → 공백, 연속 공백 정리
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r" {2,}", " ", text)

    # ** 마커 내부 공백 정리
    text = _fix_bold_markers(text)

    return text.strip()


def _apply_inline_formatting(html: str) -> str:
    """HTML 조각에 인라인 Markdown 변환(굵게, 기울임, 링크, 코드 등)을 적용한다."""
    text = html

    # 굵게 / 기울임
    text = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", text, flags=re.DOTALL | re.IGNORECASE)

    # 링크
    text = re.sub(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL | re.IGNORECASE)

    # 인라인 코드
    text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", text, flags=re.DOTALL | re.IGNORECASE)

    # 나머지 태그 제거
    text = _strip_tags(text)

    # 엔티티 디코딩
    text = _decode_entities(text)

    # 줄바꿈/탭 → 공백, 연속 공백 정리
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r" {2,}", " ", text)

    # ** 마커 내부 공백 정리
    text = _fix_bold_markers(text)

    return text.strip()


def _cell_to_lines(html: str) -> list[str]:
    """
    셀 HTML을 <p> 및 <br> 경계로 분리하여
    각 줄을 Markdown으로 변환한 리스트를 반환한다.
    """
    text = html

    # Confluence 날짜 태그 변환
    text = _convert_time_tags(text)

    # Confluence 매크로 태그 제거
    text = re.sub(r"<ac:[^>]*/?>", "", text, flags=re.DOTALL)
    text = re.sub(r"</ac:[^>]+>", "", text)
    text = re.sub(r"<ri:[^>]*/?>", "", text, flags=re.DOTALL)
    text = re.sub(r"</ri:[^>]+>", "", text)

    # <p> 블록 추출
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", text, flags=re.DOTALL | re.IGNORECASE)

    if paragraphs:
        raw_parts: list[str] = []
        for p in paragraphs:
            # 각 단락 내에서 <br>로 추가 분리
            br_parts = re.split(r"<br[^>]*/?>", p, flags=re.IGNORECASE)
            raw_parts.extend(br_parts)
    else:
        # <p> 없으면 <br>로만 분리
        raw_parts = re.split(r"<br[^>]*/?>", text, flags=re.IGNORECASE)

    # 각 조각에 인라인 Markdown 적용
    result = [_apply_inline_formatting(part) for part in raw_parts]

    # 빈 줄만 있으면 빈 문자열 하나 반환
    if not any(result):
        return [""]

    return result


def _convert_table(table_html: str) -> str:
    """
    하나의 <table>…</table> HTML 블록을 Markdown 테이블 문자열로 변환한다.
    - thead/th 가 있으면 첫 행을 헤더로 사용
    - thead가 없으면 첫 번째 행을 헤더로 간주
    - 셀 안에 여러 줄(<p>, <br>)이 있으면 테이블 행을 분리하여 확장
    - 각 열 너비를 정렬하여 보기 좋게 출력
    """
    # 행 추출
    rows_html = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.DOTALL | re.IGNORECASE)
    if not rows_html:
        return ""

    header_row_count = 0  # thead 안에 있는 행 개수

    # thead 영역 판별용
    thead_match = re.search(r"<thead[^>]*>(.*?)</thead>", table_html, flags=re.DOTALL | re.IGNORECASE)
    thead_content = thead_match.group(1) if thead_match else ""
    thead_rows_html = re.findall(r"<tr[^>]*>(.*?)</tr>", thead_content, flags=re.DOTALL | re.IGNORECASE) if thead_content else []
    header_row_count = len(thead_rows_html)

    # thead가 없으면 첫 행에 th가 있는지 확인하여 헤더 여부 결정
    if header_row_count == 0:
        first_row_html = rows_html[0]
        th_count = len(re.findall(r"<th[^>]*>", first_row_html, flags=re.IGNORECASE))
        if th_count > 0:
            header_row_count = 1

    # ── 1단계: 각 행의 셀을 줄 리스트로 수집 ──────────────────────────────
    raw_rows: list[list[list[str]]] = []  # rows → cells → lines

    for row_html in rows_html:
        cell_matches = re.findall(
            r"(<t[hd][^>]*>)(.*?)</t[hd]>",
            row_html, flags=re.DOTALL | re.IGNORECASE,
        )
        row_cells: list[list[str]] = []
        for open_tag, content in cell_matches:
            cell_lines = _cell_to_lines(content)
            # colspan 처리
            cs_match = re.search(r'colspan=["\']?(\d+)', open_tag, flags=re.IGNORECASE)
            colspan = int(cs_match.group(1)) if cs_match else 1
            row_cells.append(cell_lines)
            for _ in range(colspan - 1):
                row_cells.append([""])
        if row_cells:
            raw_rows.append(row_cells)

    if not raw_rows:
        return ""

    # ── 2단계: 멀티라인 셀을 여러 행으로 확장 ─────────────────────────────
    parsed_rows: list[list[str]] = []
    expanded_header_count = 0

    for ri, raw_row in enumerate(raw_rows):
        max_lines = max(len(cell_lines) for cell_lines in raw_row)
        for li in range(max_lines):
            expanded = []
            for cell_lines in raw_row:
                expanded.append(cell_lines[li] if li < len(cell_lines) else "")
            parsed_rows.append(expanded)
        # 헤더 행이 확장된 경우 추적
        if ri < header_row_count:
            expanded_header_count += max_lines

    header_row_count = expanded_header_count

    # ── 2.5단계: 프로젝트 식별명칭 자동 추가 ──────────────────────────────
    # 굵은글씨 + 0열/3열 동일 → 프로젝트 행, 첫 [..] 가 식별명칭
    # 이후 비굵은글씨 행의 주요항목 열에 식별명칭이 없으면 접두어로 추가
    current_project_id: str | None = None

    for ri in range(header_row_count, len(parsed_rows)):
        row = parsed_rows[ri]
        if len(row) < 4:
            continue

        col0 = row[0].strip()
        col3 = row[3].strip()

        # 프로젝트 행 판별: 굵은글씨 + 0열과 3열 내용 동일
        is_bold_0 = col0.startswith("**") and col0.endswith("**")
        is_bold_3 = col3.startswith("**") and col3.endswith("**")

        if is_bold_0 and is_bold_3:
            # ** 제거 후 내용 비교
            content_0 = col0[2:-2].strip()
            content_3 = col3[2:-2].strip()
            if content_0 == content_3:
                # 프로젝트 식별명칭 추출 (첫 번째 대괄호)
                bracket_match = re.search(r"\[([^\]]+)\]", content_0)
                if bracket_match:
                    current_project_id = f"[{bracket_match.group(1)}]"
                continue

        # 수행내용 행: 프로젝트 식별명칭 접두어 추가 + 현황을 주요항목에 병합
        if current_project_id:
            for ci in (0, 3):  # 주요항목 열 (1열, 4열)
                if ci >= len(row):
                    continue
                cell = row[ci].strip()
                if cell and not cell.startswith("**") and not cell.startswith(current_project_id):
                    row[ci] = current_project_id + " " + cell

            # 현황 열(col1,col4) + 담당자 열(col2,col5)을 주요항목 끝에 소괄호로 추가
            # 형식: 주요항목 (현황, 담당자)
            for item_ci, status_ci, person_ci in ((0, 1, 2), (3, 4, 5)):
                if item_ci >= len(row):
                    continue
                item = row[item_ci].strip()
                status = row[status_ci].strip() if status_ci < len(row) else ""
                person = row[person_ci].strip() if person_ci < len(row) else ""
                if item and not item.startswith("**"):
                    parts = [p for p in (status, person) if p]
                    if parts:
                        row[item_ci] = item + " (" + ", ".join(parts) + ")"
                    if status_ci < len(row):
                        row[status_ci] = ""
                    if person_ci < len(row):
                        row[person_ci] = ""

    # ── 2.6단계: 현황 열(col1,col4) + 담당자 열(col2,col5) 삭제 ────────
    # 역순으로 삭제하여 인덱스 밀림 방지
    for row in parsed_rows:
        for ci in sorted([5, 4, 2, 1], reverse=True):
            if ci < len(row):
                del row[ci]

    # ── 2.7단계: '주요항목'만 있는 불필요한 행 제거 ─────────────────────
    parsed_rows = [
        row for row in parsed_rows
        if not all(c.strip() in ("", "**주요항목**") for c in row)
    ]

    # ── 3단계: 열 개수 통일 및 포매팅 ─────────────────────────────────────
    max_cols = max(len(r) for r in parsed_rows)
    for row in parsed_rows:
        while len(row) < max_cols:
            row.append("")

    # 열별 최대 너비 계산 (최소 3)
    col_widths = [3] * max_cols
    for row in parsed_rows:
        for ci, cell in enumerate(row):
            col_widths[ci] = max(col_widths[ci], len(cell))

    def format_row(cells: list[str]) -> str:
        parts = []
        for ci, cell in enumerate(cells):
            parts.append(" " + cell.ljust(col_widths[ci]) + " ")
        return "|" + "|".join(parts) + "|"

    def separator_line() -> str:
        return "|" + "|".join("-" * (w + 2) for w in col_widths) + "|"

    lines: list[str] = []

    if header_row_count > 0:
        # 헤더 행 출력
        for hi in range(header_row_count):
            lines.append(format_row(parsed_rows[hi]))
        lines.append(separator_line())
        # 데이터 행 출력
        for row in parsed_rows[header_row_count:]:
            lines.append(format_row(row))
    else:
        # 헤더 없음: 빈 헤더를 만들고 전체를 데이터로 취급
        empty_header = [""] * max_cols
        lines.append(format_row(empty_header))
        lines.append(separator_line())
        for row in parsed_rows:
            lines.append(format_row(row))

    return "\n".join(lines)


def confluence_storage_to_md(html: str) -> str:
    """
    Confluence storage format(HTML 유사)을 간단한 Markdown으로 변환한다.
    완벽한 변환보다는 가독성 있는 Markdown 산출에 초점.
    """
    text = html

    # CDATA / XML 선언 제거
    text = re.sub(r"<!\[CDATA\[.*?\]\]>", "", text, flags=re.DOTALL)

    # Confluence 날짜 태그를 텍스트로 변환 (매크로 제거 전에 수행)
    text = _convert_time_tags(text)

    # Confluence 매크로 및 특수 태그 제거
    text = re.sub(r"<ac:[^>]*/?>", "", text, flags=re.DOTALL)
    text = re.sub(r"</ac:[^>]+>", "", text)
    text = re.sub(r"<ri:[^>]*/?>", "", text, flags=re.DOTALL)
    text = re.sub(r"</ri:[^>]+>", "", text)

    # ── 테이블을 먼저 구조적으로 변환 ──────────────────────────────────────
    # <table> 블록을 찾아 Markdown 테이블로 교체한 뒤 나머지 인라인 변환 수행
    def _table_replacer(m):
        return "\n\n" + _convert_table(m.group(0)) + "\n\n"

    text = re.sub(
        r"<table[^>]*>.*?</table>",
        _table_replacer,
        text, flags=re.DOTALL | re.IGNORECASE,
    )

    # ── 나머지 인라인/블록 변환 ────────────────────────────────────────────

    # 제목
    for level in range(1, 7):
        text = re.sub(
            rf"<h{level}[^>]*>(.*?)</h{level}>",
            lambda m, l=level: "\n" + "#" * l + " " + _strip_tags(m.group(1)).strip() + "\n",
            text, flags=re.DOTALL | re.IGNORECASE,
        )

    # 굵게 / 기울임
    text = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", text, flags=re.DOTALL | re.IGNORECASE)

    # 링크
    text = re.sub(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL | re.IGNORECASE)

    # 코드 블록
    text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<pre[^>]*>(.*?)</pre>", r"\n```\n\1\n```\n", text, flags=re.DOTALL | re.IGNORECASE)

    # 수평선
    text = re.sub(r"<hr[^>]*/?>", "\n---\n", text, flags=re.IGNORECASE)

    # 줄바꿈
    text = re.sub(r"<br[^>]*/?>", "\n", text, flags=re.IGNORECASE)

    # 리스트 항목
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[uo]l[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</[uo]l>", "\n", text, flags=re.IGNORECASE)

    # 단락
    text = re.sub(r"<p[^>]*>(.*?)</p>", r"\n\1\n", text, flags=re.DOTALL | re.IGNORECASE)

    # 나머지 HTML 태그 제거
    text = _strip_tags(text)

    # HTML 엔티티 디코딩
    text = _decode_entities(text)

    # ** 마커 내부 공백 정리
    text = _fix_bold_markers(text)

    # 연속 빈 줄 정리
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def sanitize_filename(name: str) -> str:
    """파일 이름으로 사용할 수 없는 문자를 제거/교체한다."""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = name.strip(". ")
    return name or "untitled"


# ─── GUI ─────────────────────────────────────────────────────────────────────

class ConfluenceBlogApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Confluence 블로그 조회기")
        self.geometry("900x350")
        self.minsize(800, 300)
        self.resizable(True, True)

        # 인증 정보 로드
        try:
            self._api_token = read_text(_ROOT / "jira_api_token.txt")
        except FileNotFoundError:
            messagebox.showerror("오류", "jira_api_token.txt 파일을 찾을 수 없습니다.\n프로젝트 루트에 파일을 생성해 주세요.")
            self.destroy()
            return

        try:
            self._auth_email = read_text(_ROOT / "jira_api_email.txt")
        except FileNotFoundError:
            messagebox.showerror("오류", "jira_api_email.txt 파일을 찾을 수 없습니다.\n프로젝트 루트에 파일을 생성해 주세요.")
            self.destroy()
            return

        self._session: requests.Session = get_session(self._auth_email, self._api_token)
        self._spaces: list[dict] = []
        self._posts: list[dict] = []
        self._worker: threading.Thread | None = None

        self._build_ui()
        self._load_spaces()

    # ── UI 구성 ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── 상단 컨트롤 프레임 ──────────────────────────────────────────────
        top_frame = ttk.Frame(self, padding=(10, 10, 10, 5))
        top_frame.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top_frame, text="Space:").pack(side=tk.LEFT)
        self.cbo_space = ttk.Combobox(top_frame, width=35, state="readonly")
        self.cbo_space.pack(side=tk.LEFT, padx=(6, 10))
        self.cbo_space.bind("<<ComboboxSelected>>", self._on_space_select)

        self.btn_refresh_spaces = ttk.Button(top_frame, text="Space 새로고침", command=self._load_spaces)
        self.btn_refresh_spaces.pack(side=tk.LEFT, padx=(0, 10))

        self.progress = ttk.Progressbar(top_frame, mode="indeterminate", length=160)
        self.progress.pack(side=tk.RIGHT)

        # ── 상태바 ────────────────────────────────────────────────────────
        self.lbl_status = ttk.Label(self, text="Space를 선택하면 블로그 목록을 불러옵니다.", anchor=tk.W)
        self.lbl_status.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 4))

        # ── 하단 버튼 프레임 ────────────────────────────────────────────────
        btn_frame = ttk.Frame(self, padding=(10, 0, 10, 6))
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.btn_export = ttk.Button(
            btn_frame, text="선택 블로그 MD로 저장", command=self._on_export, state=tk.DISABLED
        )
        self.btn_export.pack(side=tk.RIGHT)

        self.btn_export_all = ttk.Button(
            btn_frame, text="전체 블로그 MD로 저장", command=self._on_export_all, state=tk.DISABLED
        )
        self.btn_export_all.pack(side=tk.RIGHT, padx=(0, 10))

        self.lbl_output = ttk.Label(btn_frame, text=f"출력 폴더: {OUTPUT_DIR}", foreground="gray")
        self.lbl_output.pack(side=tk.LEFT)

        # ── 블로그 목록 테이블 ──────────────────────────────────────────────
        tbl_frame = ttk.Frame(self, padding=(10, 5, 10, 5))
        tbl_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        cols = ("title", "author", "created", "version")
        self.tree = ttk.Treeview(tbl_frame, columns=cols, show="headings", selectmode="extended", height=5)

        self.tree.heading("title", text="제목")
        self.tree.heading("author", text="작성자")
        self.tree.heading("created", text="작성일")
        self.tree.heading("version", text="버전")

        self.tree.column("title", width=460, anchor=tk.W)
        self.tree.column("author", width=140, anchor=tk.CENTER)
        self.tree.column("created", width=130, anchor=tk.CENTER)
        self.tree.column("version", width=60, anchor=tk.CENTER)

        vsb = ttk.Scrollbar(tbl_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tbl_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscroll=vsb.set, xscroll=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        tbl_frame.rowconfigure(0, weight=1)
        tbl_frame.columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._on_post_select)

    # ── 이벤트 핸들러 ─────────────────────────────────────────────────────────

    def _on_space_select(self, event=None):
        idx = self.cbo_space.current()
        if idx < 0 or idx >= len(self._spaces):
            return
        space = self._spaces[idx]
        self._load_blogposts(space["key"])

    def _on_post_select(self, event=None):
        selected = self.tree.selection()
        self.btn_export.config(state=tk.NORMAL if selected else tk.DISABLED)

    def _on_export(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("안내", "내보낼 블로그를 선택해 주세요.")
            return
        indices = [self.tree.index(iid) for iid in selected]
        posts = [self._posts[i] for i in indices if i < len(self._posts)]
        if posts:
            self._export_posts(posts)

    def _on_export_all(self):
        if not self._posts:
            messagebox.showinfo("안내", "블로그 목록이 없습니다.")
            return
        self._export_posts(self._posts)

    # ── 데이터 로드 (비동기) ──────────────────────────────────────────────────

    def _load_spaces(self):
        if self._is_busy():
            return
        self._set_status("Space 목록 불러오는 중…")
        self._lock_ui(True)
        t = threading.Thread(target=self._worker_load_spaces, daemon=True)
        t.start()
        self._worker = t

    def _worker_load_spaces(self):
        try:
            spaces = fetch_spaces(self._session)
            self.after(0, self._on_spaces_loaded, spaces)
        except Exception as e:
            self.after(0, self._handle_error, e)

    def _on_spaces_loaded(self, spaces: list[dict]):
        self._spaces = spaces
        labels = [f"[{s['key']}] {s.get('name', '')}" for s in spaces]
        self.cbo_space["values"] = labels
        
        if labels:
            # 기본값 설정: '[mech] 기구팀' 찾기
            default_idx = 0
            for i, label in enumerate(labels):
                if "[mech]" in label.lower():
                    default_idx = i
                    break
            self.cbo_space.current(default_idx)
            self._set_status(f"Space {len(spaces)}개 로드 완료.")
            self._lock_ui(False)
            # 기본 선택된 Space의 블로그 목록 자동 로드
            self._on_space_select()
        else:
            self._set_status("로드된 Space가 없습니다.")
            self._lock_ui(False)

    def _load_blogposts(self, space_key: str):
        if self._is_busy():
            return
        self._set_status(f"'{space_key}' Space의 블로그 목록 불러오는 중…")
        self._lock_ui(True)
        self._clear_table()
        self.btn_export.config(state=tk.DISABLED)
        self.btn_export_all.config(state=tk.DISABLED)
        t = threading.Thread(target=self._worker_load_posts, args=(space_key,), daemon=True)
        t.start()
        self._worker = t

    def _worker_load_posts(self, space_key: str):
        try:
            posts = fetch_blogposts(self._session, space_key)
            self.after(0, self._on_posts_loaded, posts)
        except Exception as e:
            self.after(0, self._handle_error, e)

    def _on_posts_loaded(self, posts: list[dict]):
        self._posts = posts
        self._fill_table(posts)
        count = len(posts)
        self._set_status(f"블로그 {count}개 로드 완료. 저장할 항목을 선택하세요.")
        self._lock_ui(False)
        self.btn_export_all.config(state=tk.NORMAL if posts else tk.DISABLED)

    # ── 내보내기 (비동기) ─────────────────────────────────────────────────────

    def _export_posts(self, posts: list[dict]):
        if self._is_busy():
            return
        self._set_status(f"총 {len(posts)}개 블로그 내보내는 중…")
        self._lock_ui(True)
        t = threading.Thread(target=self._worker_export, args=(posts,), daemon=True)
        t.start()
        self._worker = t

    def _worker_export(self, posts: list[dict]):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        success, failed = [], []
        total = len(posts)
        for i, post in enumerate(posts, 1):
            try:
                post_id = post["id"]
                title = post.get("title", f"untitled_{post_id}")
                self.after(0, self._set_status, f"({i}/{total}) '{title}' 내보내는 중…")

                html = fetch_blogpost_content(self._session, post_id)
                md_body = confluence_storage_to_md(html)

                # 메타 정보 헤더
                history = post.get("history", {})
                created_by = (history.get("createdBy") or {}).get("displayName", "")
                created_date = history.get("createdDate", "")
                if created_date:
                    created_date = created_date[:10]  # YYYY-MM-DD

                md_content = (
                    f"# {title}\n\n"
                    f"{md_body}\n"
                )

                filename = sanitize_filename(title) + ".md"
                out_path = OUTPUT_DIR / filename
                out_path.write_text(md_content, encoding="utf-8")
                success.append((title, out_path))
            except Exception as e:
                failed.append(f"{post.get('title', '?')}: {e}")

        self.after(0, self._on_export_done, success, failed)

    def _on_export_done(self, success: list[tuple[str, Path]], failed: list[str]):
        self._lock_ui(False)
        if failed:
            msg = (
                f"완료: {len(success)}개 저장됨\n"
                f"실패: {len(failed)}개\n\n"
                + "\n".join(failed[:10])
            )
            messagebox.showwarning("내보내기 결과", msg)
        else:
            messagebox.showinfo(
                "내보내기 완료",
                f"{len(success)}개의 블로그를 Markdown으로 저장했습니다.\n\n폴더: {OUTPUT_DIR}"
            )
        self._set_status(f"내보내기 완료 – {len(success)}개 저장됨. 폴더: {OUTPUT_DIR}")

        # 에디터로 생성된 파일 열기
        for _title, fpath in success:
            try:
                subprocess.Popen(["marktext", str(fpath)], shell=True) # code
            except Exception:
                pass

    # ── UI 헬퍼 ───────────────────────────────────────────────────────────────

    def _fill_table(self, posts: list[dict]):
        self._clear_table()
        for post in posts:
            history = post.get("history", {})
            author = (history.get("createdBy") or {}).get("displayName", "")
            created = history.get("createdDate", "")
            if created:
                created = created[:10]
            version = (post.get("version") or {}).get("number", "")
            self.tree.insert(
                "",
                tk.END,
                values=(post.get("title", ""), author, created, version),
            )

    def _clear_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _set_status(self, msg: str):
        self.lbl_status.config(text=msg)

    def _lock_ui(self, lock: bool):
        state = tk.DISABLED if lock else tk.NORMAL
        self.btn_refresh_spaces.config(state=state)
        self.cbo_space.config(state="disabled" if lock else "readonly")
        if lock:
            self.progress.start(10)
        else:
            self.progress.stop()

    def _is_busy(self) -> bool:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("안내", "이미 작업 중입니다. 잠시만 기다려주세요.")
            return True
        return False

    def _handle_error(self, e: Exception):
        self._lock_ui(False)
        messagebox.showerror("오류", f"처리 중 오류가 발생했습니다:\n{e}")
        self._set_status("오류 발생. 인증 정보 또는 네트워크 상태를 확인하세요.")


# ─── 진입점 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = ConfluenceBlogApp()
    app.mainloop()
