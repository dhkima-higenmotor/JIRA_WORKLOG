import threading
import requests
from requests.auth import HTTPBasicAuth
import pandas as pd
from datetime import datetime, date
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

BASE_URL = "https://higen-rnd.atlassian.net/rest/api/3/"

def read_text(path: str) -> str:
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
    r = sess.get(BASE_URL + "myself", timeout=30)
    r.raise_for_status()
    data = r.json()
    return data["accountId"]

def validate_date_str(date_str: str) -> str:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        raise ValueError("날짜 형식이 올바르지 않습니다. 예: 2025-09-17")

def enhanced_search_issue_keys(sess: requests.Session, jql: str, fields=None, page_size=100) -> list:
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

def extract_comment_text(adf) -> str:
    try:
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
                for key in ("content", "children"):
                    if key in node and isinstance(node[key], list):
                        for child in node[key]:
                            walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)
        walk(adf)
        return " ".join(texts).strip()
    except Exception:
        return ""

def to_adf_comment(text: str) -> dict:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": text or ""}
                ]
            }
        ]
    }

def format_started_kor(started_str: str) -> str:
    try:
        dt = datetime.strptime(started_str, "%Y-%m-%dT%H:%M:%S.%f%z")
        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        w = weekdays[dt.weekday()]
        return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}({w}) {dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return started_str

def update_worklog_remote(issue_key, worklog_id, time_spent, comment, started,
                          api_token=None, user_email=None):
    url = f"{BASE_URL}issue/{issue_key}/worklog/{worklog_id}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    data = {}
    if time_spent is not None:
        data["timeSpent"] = time_spent
    if comment is not None:
        data["comment"] = to_adf_comment(comment)
    if started:
        data["started"] = started
    if api_token is None:
        api_token = read_text("jira_api_token.txt")
    if user_email is None:
        user_email = read_text("user_email.txt")
    r = requests.put(
        url,
        auth=HTTPBasicAuth(user_email, api_token),
        headers=headers,
        json=data,
        timeout=30
    )
    r.raise_for_status()
    return r.json()

class EntryPopup(ttk.Entry):
    def __init__(self, parent, tree, iid, col_index, text, finish_edit_callback, **kw):
        super().__init__(parent, **kw)
        self.tree = tree
        self.iid = iid
        self.col_index = col_index
        self.finish_edit_callback = finish_edit_callback
        self.insert(0, text)
        self['exportselection'] = False
        self.focus_force()
        self.select_range(0, 'end')
        self.bind("<Return>", self.on_return)
        self.bind("<Escape>", self.on_esc)
        self.bind("<FocusOut>", self.on_focus_out)

    def on_return(self, event=None):
        self.finish_edit_callback(self.get(), self.iid, self.col_index)
        self.destroy()
    def on_esc(self, event=None):
        self.destroy()
    def on_focus_out(self, event=None):
        self.destroy()

class JiraWorklogGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("JIRA_WORKLOG 조회 프로그램")
        self.geometry("1000x400")
        self.minsize(1000, 200)
        self._worker = None
        self._df = pd.DataFrame()
        self._df_display = pd.DataFrame()
        self._entry_popup = None
        self._api_token = read_text("jira_api_token.txt")
        self._user_email = read_text("user_email.txt")
        self._build_top()
        self._build_table()
        self._build_bottom()
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
        self.cols = cols
        self.tree = ttk.Treeview(frm, columns=cols, show="headings", height=10, selectmode="extended")
        self.tree.heading("issueKey", text="Issue Key")
        self.tree.heading("worklogId", text="Worklog ID")
        self.tree.heading("started", text="Started")
        self.tree.heading("timeSpent", text="TimeSpent")
        self.tree.heading("authorDisplayName", text="Author")
        self.tree.heading("commentText", text="Comment")
        self.tree.column("issueKey", width=80, anchor=tk.CENTER)
        self.tree.column("worklogId", width=70, anchor=tk.CENTER)
        self.tree.column("started", width=120, anchor=tk.CENTER)
        self.tree.column("timeSpent", width=70, anchor=tk.CENTER)
        self.tree.column("authorDisplayName", width=50, anchor=tk.CENTER)
        self.tree.column("commentText", width=360, anchor=tk.W)
        vsb = ttk.Scrollbar(frm, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frm, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscroll=vsb.set, xscroll=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

    def _build_bottom(self):
        frm = ttk.Frame(self, padding=(10, 5, 10, 10))
        frm.pack(side=tk.BOTTOM, fill=tk.X)
        self.lbl_status = ttk.Label(frm, text="합계(시간): 0.00 h")
        self.lbl_status.pack(side=tk.LEFT)
        self.lbl_hint = ttk.Label(
            frm,
            text="* TimeSpent, Comment 셀을 더블클릭해 수정하면 Jira에 바로 반영됩니다.\n* jira_api_token.txt 및 user_email.txt 파일이 같은 폴더에 필요."
        )
        self.lbl_hint.pack(side=tk.RIGHT)

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
        self._lock_ui(True)
        self._clear_table()
        self.lbl_status.config(text="합계(시간): 0.00 h")
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

    def _run_query_worker(self, date_str: str):
        try:
            api_token = read_text("jira_api_token.txt")
            user_email = read_text("user_email.txt")
            sess = get_session(user_email, api_token)
            account_id = get_current_account_id(sess)
            Path("acountID.txt").write_text(account_id, encoding="utf-8")
            jql = f"worklogAuthor = currentUser() AND worklogDate = '{date_str}'"
            issue_keys = enhanced_search_issue_keys(sess, jql=jql, fields=["key"], page_size=100)
            if not issue_keys:
                df_display = pd.DataFrame(columns=[
                    "issueKey", "worklogId", "started", "timeSpent", "authorDisplayName", "commentText"
                ])
                total_hours = 0.0
                self.after(0, self._update_result, df_display, total_hours)
                return

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
                    def parse_started_date(started_str):
                        dt = datetime.strptime(started_str, "%Y-%m-%dT%H:%M:%S.%f%z")
                        return dt.date().isoformat()
                    if parse_started_date(started_raw) != date_str:
                        continue
                    row = {
                        "issueKey": key,
                        "worklogId": wl.get("id"),
                        "started": format_started_kor(started_raw),
                        "timeSpent": wl.get("timeSpent"),
                        "timeSpentSeconds": wl.get("timeSpentSeconds", 0) or 0,
                        "authorDisplayName": wl_author.get("displayName", ""),
                        "authorAccountId": wl_account,
                        "updated": wl.get("updated", ""),
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
            self.after(0, self._update_result, df_display, total_hours)
        except Exception as e:
            self.after(0, self._handle_error, e)

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

    def _on_tree_double_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        rowid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not rowid or not col or col == "#0":
            return
        col_index = int(col[1:]) - 1
        if self.cols[col_index] not in ("timeSpent", "commentText"):
            return
        if self._entry_popup:
            self._entry_popup.destroy()
        x, y, width, height = self.tree.bbox(rowid, col)
        original_text = self.tree.item(rowid, "values")[col_index]
        self._entry_popup = EntryPopup(
            self.tree, self.tree, rowid, col_index, original_text, self._on_edit_finish
        )
        self._entry_popup.place(x=x, y=y, width=width, height=height)

    def _on_edit_finish(self, new_value, rowid, col_index):
        item_values = list(self.tree.item(rowid, "values"))
        colname = self.cols[col_index]
        old_value = item_values[col_index]
        item_values[col_index] = new_value
        self.tree.item(rowid, values=item_values)

        idx = list(self.tree.get_children()).index(rowid)
        if self._df_display is not None and not self._df_display.empty:
            try:
                self._df_display.at[self._df_display.index[idx], colname] = new_value
            except Exception:
                pass

        issue_key = item_values[self.cols.index("issueKey")]
        worklog_id = item_values[self.cols.index("worklogId")]

        def do_update():
            try:
                if colname == "timeSpent":
                    update_worklog_remote(
                        issue_key, worklog_id,
                        time_spent=new_value,
                        comment=None,
                        started=None,
                        api_token=self._api_token,
                        user_email=self._user_email
                    )
                elif colname == "commentText":
                    update_worklog_remote(
                        issue_key, worklog_id,
                        time_spent=None,
                        comment=new_value,
                        started=None,
                        api_token=self._api_token,
                        user_email=self._user_email
                    )
            except Exception as e:
                # 오류 시 롤백
                self.after(0, lambda: messagebox.showerror("Jira 업데이트 실패", f"Jira Worklog 반영 오류: {e}"))
                item_values[col_index] = old_value
                self.after(0, lambda: self.tree.item(rowid, values=item_values))
                if self._df_display is not None and not self._df_display.empty:
                    try:
                        self._df_display.at[self._df_display.index[idx], colname] = old_value
                    except Exception:
                        pass
        threading.Thread(target=do_update, daemon=True).start()
        self._entry_popup = None

if __name__ == "__main__":
    app = JiraWorklogGUI()
    app.mainloop()
