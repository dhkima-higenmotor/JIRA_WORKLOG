import threading
import calendar

import requests
from requests.auth import HTTPBasicAuth
import pandas as pd
from datetime import datetime, date
from pathlib import Path
import sys
import subprocess

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

BASE_URL = "https://higen-rnd.atlassian.net/rest/api/3/"

def read_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    return p.read_text(encoding="utf-8").strip()

def load_members(path: str) -> list:
    """
    members.csv 파일을 읽어서 (이름, accountId, email) 튜플 리스트를 반환한다.
    파일 형식: 이름,accountId,email
    """
    p = Path(path)
    members = []
    if not p.exists():
        return members
    
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        # 헤더(첫번째 행) 건너뛰기
        if lines:
            lines = lines[1:]
        for line in lines:
            if not line.strip() or line.strip().startswith("#"):
                continue
            parts = line.split(",")
            if len(parts) >= 3:
                name = parts[0].strip()
                aid = parts[1].strip()
                email = parts[2].strip()
                if name and aid and email:
                    members.append((name, aid, email))
            elif len(parts) >= 2:
                # Fallback for old CSV format (no email) - user must fix
                pass
    except Exception:
        pass
    return members

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
        raise ValueError("User email is required.")
    r = requests.put(
        url,
        auth=HTTPBasicAuth(user_email, api_token),
        headers=headers,
        json=data,
        timeout=30
    )
    r.raise_for_status()
    return r.json()

def fetch_issue_info_enhanced(sess: requests.Session, issue_key: str) -> dict:
    """
    Enhanced JQL 기반으로 특정 이슈의 주요 정보를 반환 (summary, status, assignee, updated 등)
    """
    url = BASE_URL + "search/jql"
    jql = f'key = "{issue_key}"'
    fields = ["project", "summary", "status", "assignee", "updated", "creator", "reporter", "startdate", "duedate", "description"]
    payload = {
        "jql": jql,
        "fields": fields,
        "maxResults": 1
    }
    r = sess.post(url, json=payload, timeout=45)
    r.raise_for_status()
    data = r.json()
    issues = data.get("issues", [])
    if not issues:
        return {}
    return issues[0].get("fields", {})

class DatePickerDialog(tk.Toplevel):
    def __init__(self, parent, initial_date=None, title="날짜 선택"):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        
        # Center the dialog on parent
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        x = parent_x + (parent_w - 320) // 2
        y = parent_y + (parent_h - 290) // 2
        self.geometry(f"320x290+{max(0, x)}+{max(0, y)}")
        
        if initial_date is None:
            initial_date = datetime.today().date()
        self.selected_date = initial_date
        self.current_year = initial_date.year
        self.current_month = initial_date.month
        
        self.result = None
        
        self._build_ui()
        
        self.bind("<Return>", lambda event: self._confirm())
        self.bind("<Escape>", lambda event: self.destroy())

    def _build_ui(self):
        # Header: Prev Month, Year/Month Label, Next Month
        header_frm = ttk.Frame(self, padding=5)
        header_frm.pack(fill=tk.X)
        
        self.btn_prev = ttk.Button(header_frm, text="<", width=3, command=self._prev_month)
        self.btn_prev.pack(side=tk.LEFT)
        
        self.lbl_month = ttk.Label(header_frm, text="", font=("Malgun Gothic", 10, "bold"), anchor="center")
        self.lbl_month.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        self.btn_next = ttk.Button(header_frm, text=">", width=3, command=self._next_month)
        self.btn_next.pack(side=tk.RIGHT)
        
        # Days of the week headers
        week_frm = ttk.Frame(self, padding=2)
        week_frm.pack(fill=tk.X)
        
        for col in range(7):
            week_frm.columnconfigure(col, weight=1)
            
        days_headers = ["월", "화", "수", "목", "금", "토", "일"]
        for i, d in enumerate(days_headers):
            lbl = ttk.Label(week_frm, text=d, anchor="center", font=("Malgun Gothic", 9, "bold"))
            lbl.grid(row=0, column=i, padx=1, pady=1, sticky="nsew")
            if d == "토":
                lbl.configure(foreground="blue")
            elif d == "일":
                lbl.configure(foreground="red")
                
        # Calendar grid frame
        self.grid_frm = ttk.Frame(self, padding=2)
        self.grid_frm.pack(fill=tk.BOTH, expand=True)
        
        for col in range(7):
            self.grid_frm.columnconfigure(col, weight=1)
            
        self._draw_calendar()
        
        # Bottom buttons
        btn_frm = ttk.Frame(self, padding=5)
        btn_frm.pack(fill=tk.X)
        ttk.Button(btn_frm, text="선택", command=self._confirm).pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        ttk.Button(btn_frm, text="취소", command=self.destroy).pack(side=tk.RIGHT, padx=5, expand=True, fill=tk.X)
        
    def _draw_calendar(self):
        for widget in self.grid_frm.winfo_children():
            widget.destroy()
            
        self.lbl_month.config(text=f"{self.current_year}년 {self.current_month}월")
        
        cal = calendar.monthcalendar(self.current_year, self.current_month)
        
        # Configure row weights dynamically based on number of weeks in month
        for r_idx in range(len(cal)):
            self.grid_frm.rowconfigure(r_idx, weight=1)
            
        for r_idx, week in enumerate(cal):
            for c_idx, day in enumerate(week):
                if day == 0:
                    lbl = ttk.Label(self.grid_frm, text="")
                    lbl.grid(row=r_idx, column=c_idx, padx=1, pady=1, sticky="nsew")
                else:
                    btn = tk.Button(
                        self.grid_frm, 
                        text=str(day), 
                        relief="flat", 
                        bg="#fcfcfc",
                        activebackground="#dcdcdc",
                        font=("Malgun Gothic", 9)
                    )
                    
                    is_selected = (
                        self.selected_date.year == self.current_year and
                        self.selected_date.month == self.current_month and
                        self.selected_date.day == day
                    )
                    if is_selected:
                        btn.configure(bg="#0078d7", fg="white", activebackground="#005a9e", activeforeground="white")
                    else:
                        if c_idx == 5: # Saturday
                            btn.configure(fg="blue")
                        elif c_idx == 6: # Sunday
                            btn.configure(fg="red")
                            
                    btn.configure(command=lambda d=day: self._select_day(d))
                    btn.bind("<Double-Button-1>", lambda event, d=day: self._on_day_double_click(d))
                    btn.grid(row=r_idx, column=c_idx, padx=1, pady=1, sticky="nsew")
                    
    def _select_day(self, day):
        self.selected_date = date(self.current_year, self.current_month, day)
        self._draw_calendar()
        
    def _on_day_double_click(self, day):
        self.selected_date = date(self.current_year, self.current_month, day)
        self._confirm()
        
    def _prev_month(self):
        if self.current_month == 1:
            self.current_month = 12
            self.current_year -= 1
        else:
            self.current_month -= 1
        self._draw_calendar()
        
    def _next_month(self):
        if self.current_month == 12:
            self.current_month = 1
            self.current_year += 1
        else:
            self.current_month += 1
        self._draw_calendar()
        
    def _confirm(self):
        self.result = self.selected_date
        self.destroy()


class TimePickerDialog(tk.Toplevel):
    def __init__(self, parent, initial_time=None, title="시간 선택"):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        x = parent_x + (parent_w - 220) // 2
        y = parent_y + (parent_h - 180) // 2
        self.geometry(f"220x180+{max(0, x)}+{max(0, y)}")
        
        if initial_time is None:
            initial_time = datetime.now().time()
        self.selected_time = initial_time
        self.result = None
        
        self._build_ui()
        
        self.bind("<Return>", lambda event: self._confirm())
        self.bind("<Escape>", lambda event: self.destroy())
        
    def _build_ui(self):
        main_frm = ttk.Frame(self, padding=10)
        main_frm.pack(fill=tk.BOTH, expand=True)
        
        picker_frm = ttk.Frame(main_frm)
        picker_frm.pack(pady=5)
        
        # Spinbox for Hour
        self.sp_hour = ttk.Spinbox(
            picker_frm, 
            from_=0, to=23, 
            width=3, 
            format="%02.0f", 
            font=("Malgun Gothic", 16, "bold"),
            justify="center",
            wrap=True
        )
        self.sp_hour.pack(side=tk.LEFT, padx=5)
        self.sp_hour.set(f"{self.selected_time.hour:02d}")
        
        lbl_sep = ttk.Label(picker_frm, text=":", font=("Malgun Gothic", 16, "bold"))
        lbl_sep.pack(side=tk.LEFT, padx=2)
        
        # Spinbox for Minute
        self.sp_min = ttk.Spinbox(
            picker_frm, 
            from_=0, to=59, 
            width=3, 
            format="%02.0f", 
            font=("Malgun Gothic", 16, "bold"),
            justify="center",
            wrap=True
        )
        self.sp_min.pack(side=tk.LEFT, padx=5)
        self.sp_min.set(f"{self.selected_time.minute:02d}")
        
        # Quick offset buttons
        quick_frm = ttk.Frame(main_frm)
        quick_frm.pack(pady=5)
        
        for offset in [-30, -10, 10, 30]:
            lbl_sign = f"+{offset}" if offset > 0 else f"{offset}"
            btn = ttk.Button(quick_frm, text=lbl_sign, width=5, command=lambda o=offset: self._adjust_minutes(o))
            btn.pack(side=tk.LEFT, padx=2)
            
        # Bottom buttons
        btn_frm = ttk.Frame(main_frm)
        btn_frm.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btn_frm, text="선택", command=self._confirm).pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        ttk.Button(btn_frm, text="취소", command=self.destroy).pack(side=tk.RIGHT, padx=5, expand=True, fill=tk.X)
        
    def _adjust_minutes(self, offset):
        try:
            curr_h = int(self.sp_hour.get())
            curr_m = int(self.sp_min.get())
        except ValueError:
            curr_h = self.selected_time.hour
            curr_m = self.selected_time.minute
            
        total_m = curr_h * 60 + curr_m + offset
        total_m %= 1440
        
        new_h = total_m // 60
        new_m = total_m % 60
        
        self.sp_hour.set(f"{new_h:02d}")
        self.sp_min.set(f"{new_m:02d}")
        
    def _confirm(self):
        try:
            h = int(self.sp_hour.get())
            m = int(self.sp_min.get())
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError()
        except ValueError:
            messagebox.showerror("오류", "시간 값이 올바르지 않습니다.")
            return
            
        self.result = (h, m)
        self.destroy()


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
        self.geometry("1000x300")
        self.minsize(1000, 300)
        self._worker = None
        self._df_display = pd.DataFrame()
        self._entry_popup = None
        self._api_token = read_text("jira_api_token.txt")
        self._user_email = "" 
        
        # Load auth email
        try:
             self._auth_email = read_text("jira_api_email.txt")
        except FileNotFoundError:
             self._auth_email = ""

        # Load members, if empty launch add_member.py
        self._check_and_load_members()
        
        # Check auth email again (user might have entered it in add_member.py)
        if not self._auth_email:
             try:
                 self._auth_email = read_text("jira_api_email.txt")
             except FileNotFoundError:
                 pass
                 
        if not self._auth_email:
             # Launch add_member.py
             messagebox.showinfo("안내", "인증용 이메일(jira_api_email.txt)이 없어 사용자 추가 프로그램을 실행합니다.\n'Your Email'을 입력하고 사용자를 추가/검색하면 생성됩니다.")
             try:
                 subprocess.run([sys.executable, "add_member.py"], check=True)
             except Exception as e:
                 messagebox.showerror("오류", f"사용자 추가 프로그램 실행 실패: {e}")

             # Reload
             try:
                 self._auth_email = read_text("jira_api_email.txt")
             except FileNotFoundError:
                 pass

             if not self._auth_email:
                 messagebox.showwarning("경고", "인증용 이메일 파일이 생성되지 않았습니다. 프로그램 기능을 사용할 수 없습니다.")

        self._build_top()
        self._build_table()
        self._build_bottom()
        
        # Select first user by default if available
        if self._members:
             self.cbo_users.current(0)
             self._on_user_select(None)
        
        self.entry_date.config(state="normal")
        self.entry_date.insert(0, date.today().isoformat())
        self.entry_date.config(state="readonly")

    def _check_and_load_members(self):
        self._members = load_members("members.csv")
        if not self._members:
            # Launch add_member.py
            messagebox.showinfo("안내", "등록된 사용자가 없어 사용자 추가 프로그램을 실행합니다.\n추가 후 프로그램을 종료하면 다시 로드합니다.")
            try:
                subprocess.run([sys.executable, "add_member.py"], check=True)
            except Exception as e:
                messagebox.showerror("오류", f"사용자 추가 프로그램 실행 실패: {e}")
                
            # Reload
            self._members = load_members("members.csv")
            if not self._members:
                messagebox.showwarning("경고", "사용자가 등록되지 않았습니다. 프로그램 기능을 사용할 수 없습니다.")

    def _on_user_select(self, event):
        idx = self.cbo_users.current()
        if idx >= 0 and idx < len(self._members):
            _, _, email = self._members[idx]
            self._user_email = email

    def _build_top(self):
        frm = ttk.Frame(self, padding=(10, 10, 10, 5))
        frm.pack(side=tk.TOP, fill=tk.X)
        self.btn_select_date = ttk.Button(frm, text="조회날짜", command=self.on_select_query_date)
        self.btn_select_date.pack(side=tk.LEFT)
        self.entry_date = ttk.Entry(frm, width=12, state="readonly")
        self.entry_date.pack(side=tk.LEFT, padx=(6, 10))

        # Email Input Removed
        
        ttk.Label(frm, text="대상자:").pack(side=tk.LEFT)
        self.cbo_users = ttk.Combobox(frm, width=15, state="readonly")
        user_values = [name for name, aid, email in self._members]
        self.cbo_users['values'] = user_values
        if user_values:
            self.cbo_users.current(0)
        self.cbo_users.pack(side=tk.LEFT, padx=(6, 10))
        self.cbo_users.bind("<<ComboboxSelected>>", self._on_user_select)

        self.btn_query = ttk.Button(frm, text="조회", command=self.on_query)
        self.btn_query.pack(side=tk.LEFT)
        self.btn_save_csv = ttk.Button(frm, text="CSV 저장", command=self.on_save_csv, state=tk.DISABLED)
        self.btn_save_csv.pack(side=tk.LEFT, padx=(10, 0))
        
        self.btn_add_member = ttk.Button(frm, text="Add Member", command=self.on_add_member)
        self.btn_add_member.pack(side=tk.LEFT, padx=(10, 0))

        self.progress = ttk.Progressbar(frm, mode="indeterminate", length=180)
        self.progress.pack(side=tk.RIGHT)

    def _build_table(self):
        frm = ttk.Frame(self, padding=(10, 5, 10, 5))
        frm.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        cols = ("issueKey", "worklogId", "started", "timeSpent", "authorDisplayName", "commentText")
        self.cols = cols
        self.tree = ttk.Treeview(frm, columns=cols, show="headings", height=6, selectmode="extended")
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
        self.tree.tag_configure("duplicate", foreground="red")

    def _build_bottom(self):
        frm = ttk.Frame(self, padding=(10, 5, 10, 10))
        frm.pack(side=tk.BOTTOM, fill=tk.X)
        self.lbl_status = ttk.Label(frm, text="전체합계시간: 0.00 h", font=("Malgun Gothic", 16, "bold"), foreground="red")
        self.lbl_status.pack(side=tk.LEFT)
        self.lbl_hint = ttk.Label(
            frm,
            text="* Issue Key 셀을 더블클릭하면 해당 이슈의 정보를 확인할 수 있습니다.\n* Started, TimeSpent, Comment 셀을 더블클릭후 수정하면 Jira에 바로 반영됩니다."
        )
        self.lbl_hint.pack(side=tk.RIGHT)

    def on_select_query_date(self):
        current_date_str = self.entry_date.get().strip()
        try:
            initial_d = datetime.strptime(current_date_str, "%Y-%m-%d").date()
        except Exception:
            initial_d = date.today()
            
        dp = DatePickerDialog(self, initial_date=initial_d, title="조회 날짜 선택")
        self.wait_window(dp)
        if dp.result is not None:
            selected_str = dp.result.isoformat()
            self.entry_date.config(state="normal")
            self.entry_date.delete(0, tk.END)
            self.entry_date.insert(0, selected_str)
            self.entry_date.config(state="readonly")

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
            
        selected_idx = self.cbo_users.current()
        if selected_idx < 0:
             messagebox.showwarning("입력 확인", "대상자를 선택해주세요.")
             return
             
        name, target_account_id, target_user_email = self._members[selected_idx]
        self._user_email = target_user_email

        self._lock_ui(True)
        self._clear_table()
        self.lbl_status.config(text="전체합계시간: 0.00 h", foreground="red")
        
        self._worker = threading.Thread(target=self._run_query_worker, args=(date_str, self._auth_email, target_account_id), daemon=True)
        self._worker.start()
        self.progress.start(10)

    def on_add_member(self):
        try:
            # Launch add_member.py and wait for it to finish
            subprocess.run([sys.executable, "add_member.py"], check=True)
            
            # Refresh member list
            self._members = load_members("members.csv")
            
            # Update Combobox
            user_values = [name for name, aid, email in self._members]
            self.cbo_users['values'] = user_values
            
            # Retain selection if possible, or select first
            current_idx = self.cbo_users.current()
            if self._members:
                if current_idx >= 0 and current_idx < len(self._members):
                    # re-select current
                    self.cbo_users.current(current_idx)
                else:
                    self.cbo_users.current(0)
                self._on_user_select(None)
            else:
                 self.cbo_users.set("")
                 self._user_email = ""
            
        except Exception as e:
            messagebox.showerror("오류", f"사용자 추가 프로그램 실행 중 오류: {e}")

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

    def _run_query_worker(self, date_str: str, auth_email: str, target_account_id: str = None):
        try:
            api_token = read_text("jira_api_token.txt")
            # auth_email passed as arg (used for login)
            if not auth_email:
                 raise ValueError("인증용 이메일이 설정되지 않았습니다. (jira_api_email.txt)")
                 
            sess = get_session(auth_email, api_token)
            
            my_account_id = get_current_account_id(sess)
            # acountID.txt creation removed as per user request

            
            if not target_account_id:
                # Should not happen with new logic, but fallback
                target_account_id = my_account_id
                jql_author = "currentUser()"
            else:
                jql_author = f"'{target_account_id}'"

            jql = f"worklogAuthor = {jql_author} AND worklogDate = '{date_str}'"
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
                    # 정확한 필터링: JQL로 1차 거르지만, worklogAuthor가 여러명일 수 있는 이슈 내에서
                    # 해당 날짜/해당 작성자의 worklog만 추려야 함.
                    if wl_account != target_account_id:
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
        in_range = 8.0 - 0.001 <= total_hours <= 9.0 + 0.001
        color = "black" if in_range else "red"
        self.lbl_status.config(text=f"전체합계시간: {total_hours:.2f} h", foreground=color)
        self._lock_ui(False)

    def _update_total_hours(self):
        total_hours = 0.0
        import re
        all_items = self.tree.get_children()
        for item in all_items:
            vals = self.tree.item(item, "values")
            if vals and len(vals) >= 4:
                time_spent_str = vals[3]
                tokens = re.findall(r'(\d+(?:\.\d+)?)\s*([wdhm])', time_spent_str.lower())
                if tokens:
                    for val_str, unit in tokens:
                        val = float(val_str)
                        if unit == 'w':
                            total_hours += val * 40
                        elif unit == 'd':
                            total_hours += val * 8
                        elif unit == 'h':
                            total_hours += val
                        elif unit == 'm':
                            total_hours += val / 60.0
                else:
                    try:
                        total_hours += float(time_spent_str)
                    except ValueError:
                        pass
        in_range = 8.0 - 0.001 <= total_hours <= 9.0 + 0.001
        color = "black" if in_range else "red"
        self.lbl_status.config(text=f"전체합계시간: {total_hours:.2f} h", foreground=color)

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
        self._update_duplicate_tags()

    def _update_duplicate_tags(self):
        all_items = self.tree.get_children()
        started_values = []
        item_started_map = {}
        for item in all_items:
            vals = self.tree.item(item, "values")
            if vals and len(vals) >= 3:
                started_val = vals[2]
                started_values.append(started_val)
                item_started_map[item] = started_val
        
        counts = {}
        for val in started_values:
            counts[val] = counts.get(val, 0) + 1
        duplicate_started = {val for val, count in counts.items() if count > 1}
        
        for item, started_val in item_started_map.items():
            if started_val in duplicate_started:
                self.tree.item(item, tags=("duplicate",))
            else:
                self.tree.item(item, tags=())

    def _on_tree_double_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        rowid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not rowid or not col or col == "#0":
            return
        col_index = int(col[1:]) - 1
        col_name = self.cols[col_index]
        values = self.tree.item(rowid, "values")
        if col_name == "issueKey":  # 이슈 상세 팝업
            issue_key = values[col_index]
            self.show_issue_info_popup(issue_key)
            return
        if col_name == "started":
            # Parse existing cell value for initial value
            initial_dt = None
            original_text = values[col_index]
            try:
                # Format: 2026-06-04(목) 08:32
                parts = original_text.split(")")
                date_part = parts[0].split("(")[0].strip()
                time_part = parts[1].strip()
                initial_dt = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M")
            except Exception:
                initial_dt = datetime.now()

            # 1. Date Picker
            dp = DatePickerDialog(self, initial_date=initial_dt.date())
            self.wait_window(dp)
            if dp.result is None:
                return # Cancelled

            # 2. Time Picker
            tp = TimePickerDialog(self, initial_time=initial_dt.time())
            self.wait_window(tp)
            if tp.result is None:
                return # Cancelled

            # Combine and format
            sel_date = dp.result
            sel_hour, sel_min = tp.result
            new_dt = datetime(sel_date.year, sel_date.month, sel_date.day, sel_hour, sel_min)
            new_dt_tz = new_dt.astimezone()
            
            display_val = format_started_kor(new_dt_tz.strftime("%Y-%m-%dT%H:%M:%S.000%z"))
            raw_val = new_dt_tz.strftime("%Y-%m-%dT%H:%M:%S.000%z")
            
            self._on_edit_finish(display_val, rowid, col_index, raw_value=raw_val)
            return

        if col_name not in ("timeSpent", "commentText"):
            return
        if self._entry_popup:
            self._entry_popup.destroy()
        x, y, width, height = self.tree.bbox(rowid, col)
        original_text = values[col_index]
        self._entry_popup = EntryPopup(
            self.tree, self.tree, rowid, col_index, original_text, self._on_edit_finish
        )
        self._entry_popup.place(x=x, y=y, width=width, height=height)
 
    def _on_edit_finish(self, new_value, rowid, col_index, raw_value=None):
        item_values = list(self.tree.item(rowid, "values"))
        colname = self.cols[col_index]
        old_value = item_values[col_index]
        item_values[col_index] = new_value
        self.tree.item(rowid, values=item_values)
        self._update_duplicate_tags()
        if colname == "timeSpent":
            self._update_total_hours()
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
                        user_email=self._auth_email
                    )
                elif colname == "commentText":
                    update_worklog_remote(
                        issue_key, worklog_id,
                        time_spent=None,
                        comment=new_value,
                        started=None,
                        api_token=self._api_token,
                        user_email=self._auth_email
                    )
                elif colname == "started":
                    started_val = raw_value
                    if not started_val:
                        try:
                            # 2026-06-04(목) 08:32 -> ISO
                            parts = new_value.split(")")
                            date_part = parts[0].split("(")[0].strip()
                            time_part = parts[1].strip()
                            dt = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M")
                            started_val = dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S.000%z")
                        except Exception:
                            started_val = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S.000%z")
                    update_worklog_remote(
                        issue_key, worklog_id,
                        time_spent=None,
                        comment=None,
                        started=started_val,
                        api_token=self._api_token,
                        user_email=self._auth_email
                    )
            except Exception as e:
                # 오류 시 롤백
                self.after(0, lambda: messagebox.showerror("Jira 업데이트 실패", f"Jira Worklog 반영 오류: {e}"))
                item_values[col_index] = old_value
                self.after(0, lambda: self.tree.item(rowid, values=item_values))
                self.after(0, self._update_duplicate_tags)
                if colname == "timeSpent":
                    self.after(0, self._update_total_hours)
                if self._df_display is not None and not self._df_display.empty:
                    try:
                        self._df_display.at[self._df_display.index[idx], colname] = old_value
                    except Exception:
                        pass
        threading.Thread(target=do_update, daemon=True).start()
        self._entry_popup = None

    def show_issue_info_popup(self, issue_key):
        email_for_popup = self._user_email


        def extract_adf_text_with_newline(adf) -> str:
            """
            Atlassian Document Format dict에서 순수 텍스트만 추출하고,
            단락(문단, 헤딩, 리스트) 별로 줄바꿈을 삽입한다.
            """
            texts = []
            def walk(node):
                if isinstance(node, dict):
                    ntype = node.get("type")
                    # 텍스트 노드
                    if ntype == "text" and "text" in node:
                        texts.append(node["text"])
                    # 줄바꿈이 들어가는 주요 블록
                    elif ntype in ("paragraph", "heading", "listItem"):
                        for key in ("content", "children"):
                            if key in node and isinstance(node[key], list):
                                for child in node[key]:
                                    walk(child)
                        texts.append("\n")
                    # 일반 블록(리스트, 도큐먼트, etc)
                    else:
                        for key in ("content", "children"):
                            if key in node and isinstance(node[key], list):
                                for child in node[key]:
                                    walk(child)
                elif isinstance(node, list):
                    for child in node:
                        walk(child)
            walk(adf)
            # 줄바꿈 연속, 앞/뒤 공백 제거
            return "\n".join(line.strip() for line in "".join(texts).splitlines() if line.strip())

        def worker():
            try:
                # Ensure we have the email from entry if distinct from last query, 
                # but accessing entry from thread is bad practice. 
                # However, this worker is short lived.
                # Better to use self._user_email assuming query was run or user entered it.
                # For safety, let's capture it in show_issue_info_popup scope
                sess = get_session(self._auth_email, self._api_token)
                info = fetch_issue_info_enhanced(sess, issue_key)
                fields = []
                if info:
                    project = (info.get("project", "")).get("name", "")
                    summary = info.get("summary", "")
                    status = (info.get("status") or {}).get("name", "")
                    assignee = ((info.get("assignee") or {}).get("displayName", "")
                                if info.get("assignee") else "")
                    updated = info.get("updated", "")
                    creator = ((info.get("creator") or {}).get("displayName", "")
                                if info.get("creator") else "")
                    reporter = ((info.get("reporter") or {}).get("displayName", "")
                                if info.get("reporter") else "")
                    startdate = "Not Known" #info[0]["fields"].get("customfield_10429") if issues else None
                    duedate = info.get("duedate", "")
                    description = extract_adf_text_with_newline(info.get("description", ""))
                    fields = [
                        f"# Project: {project}",
                        f"",
                        f"Issue Key: {issue_key}",
                        f"Summary: {summary}",
                        f"Status: {status}",
                        f"Assignee: {assignee}",
                        f"Reporter: {reporter}",
                        f"Creator: {creator}",
                        f"Updated: {updated}",
                        f"Start Date: {startdate}",
                        f"Due Date: {duedate}",
                        f"",
                        f"",
                        f"# Description:",
                        f"",
                        f"{description}"
                    ]
                else:
                    fields = [f"Issue Key: {issue_key}", "이슈 상세 정보를 찾을 수 없습니다."]
                msg = "\n".join(fields)
                self.after(0, lambda: self._show_info_text_popup(f"Issue Info: {issue_key}", msg))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("이슈 정보 오류", str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def _show_info_text_popup(self, title, msg):
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("400x350")
        text = tk.Text(win, wrap="word", height=14, width=46, font=("Malgun Gothic", 11))
        text.insert(1.0, msg)
        text.configure(state=tk.DISABLED)
        text.pack(fill=tk.BOTH, expand=True, padx=12, pady=9)
        btn = ttk.Button(win, text="닫기", command=win.destroy)
        btn.pack(pady=(0, 10))

if __name__ == "__main__":
    app = JiraWorklogGUI()
    app.iconbitmap('robot_1211_V01.ico')
    app.mainloop()
