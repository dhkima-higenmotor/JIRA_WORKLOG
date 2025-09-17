import threading
import requests
from requests.auth import HTTPBasicAuth
import pandas as pd
from datetime import datetime, date
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# 고정 BASE_URL (지시사항)
BASE_URL = "https://higen-rnd.atlassian.net/rest/api/3/"

# =========================
# 기존 핵심 로직 함수들
# =========================

def read_text(path: str) -> str:
    """
    텍스트 파일 읽기. 없으면 예외 발생(콘솔 종료 대신 GUI에서 처리하기 위해 예외로 전달).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    return p.read_text(encoding="utf-8").strip()


def get_session(user_email: str, api_token: str) -> requests.Session:
    s = requests.Session()
    s.auth = HTTPBasicAuth(user_email, api_token)
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    return s


def get_current_account_id(sess: requests.Session) -> str:
    # GET /rest/api/3/myself
    r = sess.get(BASE_URL + "myself", timeout=30)
    r.raise_for_status()
    data = r.json()
    return data["accountId"]


def validate_date_str(date_str: str) -> str:
    """
    YYYY-MM-DD 형식 검증. 잘못되면 ValueError 발생.
    """
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        raise ValueError("날짜 형식이 올바르지 않습니다. 예: 2025-09-17")


def enhanced_search_issue_keys(sess: requests.Session, jql: str, fields=None, page_size=100) -> list:
    """
    Enhanced JQL Service API: POST /rest/api/3/search/jql
    응답의 nextPageToken 기반 스크롤링 페이지네이션 처리
    """
    if fields is None:
        fields = ["key"]

    all_keys = []
    next_token = None
    url = BASE_URL + "search/jql"

    while True:
        payload = {
            "jql": jql,
            "fields": fields,
            "maxResults": page_size
        }
        if next_token:
            payload["nextPageToken"] = next_token

        r = sess.post(url, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()

        issues = data.get("issues", []) or []
        all_keys.extend([it.get("key") for it in issues if it.get("key")])

        next_token = data.get("nextPageToken")
        if not next_token:
            break

    return sorted(set(all_keys))


def iter_issue_worklogs(sess: requests.Session, issue_key: str, page_size=100):
    """
    표준 Worklog API: GET /rest/api/3/issue/{issueIdOrKey}/worklog
    startAt/total 기반 페이지네이션 처리
    """
    url = f"{BASE_URL}issue/{issue_key}/worklog"
    start_at = 0

    while True:
        r = sess.get(url, params={"startAt": start_at, "maxResults": page_size}, timeout=60)
        r.raise_for_status()
        data = r.json()

        worklogs = data.get("worklogs", []) or []
        total = data.get("total", len(worklogs))

        for wl in worklogs:
            yield wl

        start_at += len(worklogs)
        if start_at >= total:
            break


def parse_started_date(started_str: str) -> str:
    # 예: "2021-01-17T12:34:00.000+0000"
    dt = datetime.strptime(started_str, "%Y-%m-%dT%H:%M:%S.%f%z")
    return dt.date().isoformat()


def extract_comment_text(adf) -> str:
    """
    Jira Cloud v3 Worklog의 comment는 ADF(Document)이다.
    - 문자열이면 그대로 반환
    - dict/list면 재귀적으로 모든 text 노드 수집
    - mention/emoji 등 특수 노드도 간단 표시
    파싱 실패 시 빈 문자열
    """
    try:
        # 문자열 코멘트 대비
        if isinstance(adf, str):
            return adf.strip()

        texts = []

        def walk(node):
            if isinstance(node, dict):
                ntype = node.get("type")
                if ntype == "text" and "text" in node:
                    texts.append(node["text"])
                elif ntype == "emoji":
                    short = (node.get("attrs") or {}).get("shortName")
                    if short:
                        texts.append(short)
                elif ntype == "mention":
                    m = node.get("attrs") or {}
                    label = m.get("text") or m.get("displayName") or m.get("id")
                    if label:
                        texts.append(str(label))
                # 자식 순회
                for key in ("content", "children"):
                    if key in node and isinstance(node[key], list):
                        for child in node[key]:
                            walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        # 루트가 doc가 아니어도 content만 있으면 순회
        walk(adf)
        return " ".join(texts).strip()
    except Exception:
        return ""


def format_started_kor(started_str: str) -> str:
    """
    Jira started ISO 문자열을 "YYYY-MM-DD(요일) HH:MM" 형식으로 변환.
    요일: 월(0)~일(6) 수동 매핑. 파싱 실패 시 원문 반환.
    """
    try:
        dt = datetime.strptime(started_str, "%Y-%m-%dT%H:%M:%S.%f%z")
        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        w = weekdays[dt.weekday()]
        return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}({w}) {dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return started_str

# =========================
# GUI 애플리케이션
# =========================

class JiraWorklogGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Jira Worklog 조회 (currentUser, worklogDate)")
        self.geometry("1000x620")
        self.minsize(900, 520)

        # 상태
        self._worker = None
        self._df = pd.DataFrame()
        self._df_display = pd.DataFrame()

        # UI 구성
        self._build_top()
        self._build_table()
        self._build_bottom()

        # 기본 날짜 = 오늘
        self.entry_date.insert(0, date.today().isoformat())

    def _build_top(self):
        frm = ttk.Frame(self, padding=(10, 10, 10, 5))
        frm.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(frm, text="조회 날짜 (YYYY-MM-DD):").pack(side=tk.LEFT)
        self.entry_date = ttk.Entry(frm, width=16)
        self.entry_date.pack(side=tk.LEFT, padx=(6, 10))

        self.btn_query = ttk.Button(frm, text="조회", command=self.on_query)
        self.btn_query.pack(side=tk.LEFT)

        self.btn_save_csv = ttk.Button(frm, text="CSV 저장", command=self.on_save_csv, state=tk.DISABLED)
        self.btn_save_csv.pack(side=tk.LEFT, padx=(10, 0))

        self.progress = ttk.Progressbar(frm, mode="indeterminate", length=180)
        self.progress.pack(side=tk.RIGHT)

    def _build_table(self):
        frm = ttk.Frame(self, padding=(10, 5, 10, 5))
        frm.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        cols = ("issueKey", "worklogId", "started", "timeSpent", "authorDisplayName", "commentText")
        self.tree = ttk.Treeview(frm, columns=cols, show="headings", height=18)

        # 헤더
        self.tree.heading("issueKey", text="Issue Key")
        self.tree.heading("worklogId", text="Worklog ID")
        self.tree.heading("started", text="Started")
        self.tree.heading("timeSpent", text="Time Spent")
        self.tree.heading("authorDisplayName", text="Author")
        self.tree.heading("commentText", text="Comment")

        # 컬럼 폭
        self.tree.column("issueKey", width=110, anchor=tk.CENTER)
        self.tree.column("worklogId", width=110, anchor=tk.CENTER)
        self.tree.column("started", width=180, anchor=tk.W)
        self.tree.column("timeSpent", width=100, anchor=tk.CENTER)
        self.tree.column("authorDisplayName", width=160, anchor=tk.W)
        self.tree.column("commentText", width=360, anchor=tk.W)

        vsb = ttk.Scrollbar(frm, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frm, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscroll=vsb.set, xscroll=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)

    def _build_bottom(self):
        frm = ttk.Frame(self, padding=(10, 5, 10, 10))
        frm.pack(side=tk.BOTTOM, fill=tk.X)

        self.lbl_status = ttk.Label(frm, text="합계(시간): 0.00 h")
        self.lbl_status.pack(side=tk.LEFT)

        self.lbl_hint = ttk.Label(frm, text="jira_api_token.txt / user_email.txt 파일이 같은 폴더에 있어야 합니다.")
        self.lbl_hint.pack(side=tk.RIGHT)

    # =========================
    # 이벤트 핸들러
    # =========================

    def on_query(self):
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("안내", "이미 조회 중입니다. 잠시만 기다려주세요.")
            return

        date_str = self.entry_date.get().strip()
        try:
            validate_date_str(date_str)
        except Exception as e:
            messagebox.showerror("날짜 오류", str(e))
            return

        # UI 잠금
        self._lock_ui(True)
        self._clear_table()
        self.lbl_status.config(text="합계(시간): 0.00 h")

        # 비동기 실행
        self._worker = threading.Thread(target=self._run_query_worker, args=(date_str,), daemon=True)
        self._worker.start()
        self.progress.start(10)

    def on_save_csv(self):
        if self._df_display is None or self._df_display.empty:
            messagebox.showinfo("안내", "저장할 데이터가 없습니다.")
            return

        path = filedialog.asksaveasfilename(
            title="CSV로 저장",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return

        try:
            self._df_display.to_csv(path, index=False, encoding="utf-8-sig")
            messagebox.showinfo("완료", "CSV 저장이 완료되었습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"CSV 저장 중 오류가 발생했습니다:\n{e}")

    # =========================
    # 워커 로직
    # =========================

    def _run_query_worker(self, date_str: str):
        """
        원래 main()에서 수행하던 흐름을 GUI용으로 재구성.
        """
        try:
            # 1) 파일에서 자격 정보 읽기
            api_token = read_text("jira_api_token.txt")
            user_email = read_text("user_email.txt")

            # 2) 세션 생성
            sess = get_session(user_email, api_token)

            # 3) 로그인 사용자 accountId 획득 및 파일 저장 (지시된 철자 유지: acountID.txt)
            account_id = get_current_account_id(sess)
            Path("acountID.txt").write_text(account_id, encoding="utf-8")

            # 5) Enhanced JQL로 대상 이슈 검색
            jql = f"worklogAuthor = currentUser() AND worklogDate = '{date_str}'"
            issue_keys = enhanced_search_issue_keys(sess, jql=jql, fields=["key"], page_size=100)

            # 결과 없을 때
            if not issue_keys:
                df_display = pd.DataFrame(columns=[
                    "issueKey", "worklogId", "started", "timeSpent", "authorDisplayName", "commentText"
                ])
                total_hours = 0.0
                self.after(0, self._update_result, df_display, total_hours)
                return

            # 6) 각 이슈별 worklog 수집(로그인 사용자 + 해당 날짜만 필터)
            rows = []
            for key in issue_keys:
                for wl in iter_issue_worklogs(sess, key, page_size=100):
                    wl_author = wl.get("author", {}) or {}
                    wl_account = wl_author.get("accountId", "")
                    if wl_account != account_id:
                        continue

                    started_raw = wl.get("started", "")
                    if not started_raw:
                        continue

                    # 날짜 필터
                    if parse_started_date(started_raw) != date_str:
                        continue

                    row = {
                        "issueKey": key,
                        "worklogId": wl.get("id"),
                        # started 표시 형식 변경: "YYYY-MM-DD(요일) HH:MM"
                        "started": format_started_kor(started_raw),
                        "timeSpent": wl.get("timeSpent"),
                        "timeSpentSeconds": wl.get("timeSpentSeconds", 0) or 0,
                        "authorDisplayName": wl_author.get("displayName", ""),
                        "authorAccountId": wl_account,
                        "updated": wl.get("updated", ""),
                        # ADF 코멘트 안전 파싱
                        "commentText": extract_comment_text(wl.get("comment")),
                    }
                    rows.append(row)

            df = pd.DataFrame(rows, columns=[
                "issueKey", "worklogId", "started", "timeSpent", "timeSpentSeconds",
                "authorDisplayName", "authorAccountId", "updated", "commentText"
            ])

            df_display = df.drop(columns=["timeSpentSeconds", "authorAccountId", "updated"], errors="ignore")

            if df.empty:
                total_hours = 0.0
            else:
                total_seconds = int(df["timeSpentSeconds"].sum())
                total_hours = total_seconds / 3600.0

            # UI 갱신 예약
            self.after(0, self._update_result, df_display, total_hours)

        except Exception as e:
            self.after(0, self._handle_error, e)

    # =========================
    # UI 유틸
    # =========================

    def _update_result(self, df_display: pd.DataFrame, total_hours: float):
        self._df_display = df_display
        self._fill_table_from_df(df_display)
        self.lbl_status.config(text=f"합계(시간): {total_hours:.2f} h")
        self._lock_ui(False)

    def _handle_error(self, e: Exception):
        self._lock_ui(False)
        messagebox.showerror("오류", f"처리 중 오류가 발생했습니다:\n{e}")

    def _lock_ui(self, lock: bool):
        state = tk.DISABLED if lock else tk.NORMAL
        self.btn_query.config(state=state)
        self.btn_save_csv.config(
            state=state if (self._df_display is not None and not self._df_display.empty and not lock) else tk.DISABLED
        )
        if lock:
            self.progress.start(10)
        else:
            self.progress.stop()

    def _clear_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _fill_table_from_df(self, df_display: pd.DataFrame):
        self._clear_table()
        if df_display is None or df_display.empty:
            self.btn_save_csv.config(state=tk.DISABLED)
            return

        # 행 삽입
        for _, row in df_display.iterrows():
            values = (
                row.get("issueKey", ""),
                row.get("worklogId", ""),
                row.get("started", ""),
                row.get("timeSpent", ""),
                row.get("authorDisplayName", ""),
                row.get("commentText", ""),
            )
            self.tree.insert("", tk.END, values=values)

        self.btn_save_csv.config(state=tk.NORMAL)


# 진입점
if __name__ == "__main__":
    app = JiraWorklogGUI()
    app.mainloop()
