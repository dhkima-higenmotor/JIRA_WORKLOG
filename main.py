import threading
import calendar
import re
import os

import requests
from requests.auth import HTTPBasicAuth
import pandas as pd
from openpyxl.utils import get_column_letter
from datetime import datetime, date, timedelta
from pathlib import Path
import sys
import subprocess

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font as tkfont

BASE_URL = "https://higen-rnd.atlassian.net/rest/api/3/"
AGILE_BASE = "https://higen-rnd.atlassian.net/rest/agile/1.0/"

def fetch_projects(sess: requests.Session) -> list[dict]:
    r = sess.get(BASE_URL + "project", timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_boards(sess: requests.Session) -> list[dict]:
    boards = []
    start = 0
    limit = 50
    while True:
        r = sess.get(
            AGILE_BASE + "board",
            params={"startAt": start, "maxResults": limit},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("values", [])
        boards.extend(results)
        if data.get("isLast", True):
            break
        start += limit
    return boards

def fetch_board_columns(sess: requests.Session, board_id: int) -> list[dict]:
    r = sess.get(
        AGILE_BASE + f"board/{board_id}/configuration",
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("columnConfig", {}).get("columns", [])

def fetch_board_issues(sess: requests.Session, board_id: int) -> list[dict]:
    issues = []
    start = 0
    limit = 50
    while True:
        r = sess.get(
            AGILE_BASE + f"board/{board_id}/issue",
            params={"startAt": start, "maxResults": limit, "fields": "summary,status"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("issues", []) or []
        issues.extend(results)
        total = data.get("total", len(issues))
        if start + len(results) >= total or not results:
            break
        start += len(results)
    return issues

def fetch_project_issues(sess: requests.Session, project_key: str) -> list[dict]:
    issues = []
    start = 0
    limit = 100
    url = BASE_URL + "search"
    while True:
        try:
            r = sess.get(
                url,
                params={
                    "jql": f"project = '{project_key}' ORDER BY key DESC",
                    "startAt": start,
                    "maxResults": limit,
                    "fields": "summary,status"
                },
                timeout=30
            )
            r.raise_for_status()
            data = r.json()
            results = data.get("issues", []) or []
            issues.extend(results)
            total = data.get("total", len(issues))
            if start + len(results) >= total or not results:
                break
            start += len(results)
        except Exception:
            break
    return issues

def fetch_jira_users(sess: requests.Session) -> list:
    """
    Jira REST API에서 등록된 사용자 목록을 가져와 (displayName, accountId, emailAddress) 튜플 리스트로 반환한다.
    실제 사람 계정만 필터링하여 이름순으로 정렬한다.
    """
    users_url = BASE_URL + "users/search"
    start_at = 0
    max_results = 100
    all_users = []
    
    while True:
        try:
            r = sess.get(users_url, params={"startAt": start_at, "maxResults": max_results}, timeout=30)
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            all_users.extend(data)
            if len(data) < max_results:
                break
            start_at += len(data)
        except Exception:
            break

    members = []
    for u in all_users:
        acct_type = u.get("accountType", "")
        is_active = u.get("active", True)
        display_name = (u.get("displayName") or "").strip()
        account_id = u.get("accountId", "")
        email = u.get("emailAddress", "")

        if acct_type == "atlassian" and is_active and display_name and display_name != "Former user" and account_id:
            members.append((display_name, account_id, email))

    members.sort(key=lambda x: x[0])
    return members

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

def get_myself(sess: requests.Session) -> dict:
    r = sess.get(BASE_URL + "myself", timeout=30)
    r.raise_for_status()
    return r.json()

def get_current_account_id(sess: requests.Session) -> str:
    return get_myself(sess).get("accountId", "")

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

def add_worklog_remote(issue_key, time_spent, comment, started,
                       api_token=None, user_email=None):
    url = f"{BASE_URL}issue/{issue_key}/worklog"
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
    r = requests.post(
        url,
        auth=HTTPBasicAuth(user_email, api_token),
        headers=headers,
        json=data,
        timeout=30
    )
    r.raise_for_status()
    return r.json()

def get_user_groups(sess: requests.Session, account_id: str) -> list[dict]:
    r = sess.get(
        BASE_URL + "user/groups",
        params={"accountId": account_id},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

def create_issue_remote(project_key, summary, issuetype="작업", start_date=None,
                        due_date=None, assignee_account_id=None, reporter_account_id=None,
                        api_token=None, user_email=None):
    url = BASE_URL + "issue"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    fields = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issuetype},
    }
    if start_date:
        fields["customfield_10015"] = start_date
    if due_date:
        fields["duedate"] = due_date
    if assignee_account_id:
        fields["assignee"] = {"accountId": assignee_account_id}
    if reporter_account_id:
        fields["reporter"] = {"accountId": reporter_account_id}
    data = {"fields": fields}
    if api_token is None:
        api_token = read_text("jira_api_token.txt")
    if user_email is None:
        raise ValueError("User email is required.")
    r = requests.post(
        url,
        auth=HTTPBasicAuth(user_email, api_token),
        headers=headers,
        json=data,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

def transition_issue_to_status(sess: requests.Session, issue_key: str, target_names: list) -> bool:
    url = BASE_URL + f"issue/{issue_key}/transitions"
    r = sess.get(url, timeout=30)
    r.raise_for_status()
    transitions = r.json().get("transitions", [])
    for t in transitions:
        to_status = t.get("to", {}).get("name", "")
        if to_status in target_names:
            tid = t["id"]
            sess.post(url, json={"transition": {"id": tid}}, timeout=30)
            return True
    return False

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
        
        self.lbl_month = ttk.Label(header_frm, text="", font=("", 10, "bold"), anchor="center")
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
            lbl = ttk.Label(week_frm, text=d, anchor="center", font=("", 9, "bold"))
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
                        font=("", 9)
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
            font=("", 16, "bold"),
            justify="center",
            wrap=True
        )
        self.sp_hour.pack(side=tk.LEFT, padx=5)
        self.sp_hour.set(f"{self.selected_time.hour:02d}")
        
        lbl_sep = ttk.Label(picker_frm, text=":", font=("", 16, "bold"))
        lbl_sep.pack(side=tk.LEFT, padx=2)
        
        # Spinbox for Minute
        self.sp_min = ttk.Spinbox(
            picker_frm, 
            from_=0, to=59, 
            width=3, 
            format="%02.0f", 
            font=("", 16, "bold"),
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

class LogWorkDialog(tk.Toplevel):
    def __init__(self, parent, issue_key, on_created=None, initial_comment=""):
        super().__init__(parent)
        self.title(f"Log Work - {issue_key}")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        x = parent_x + (parent_w - 430) // 2
        y = parent_y + (parent_h - 330) // 2
        self.geometry(f"430x330+{max(0, x)}+{max(0, y)}")

        self._issue_key = issue_key
        self._on_created = on_created
        self._initial_comment = initial_comment
        self._started_datetime = datetime.now().astimezone()

        self.result = None

        self._build_ui()

        self.bind("<Escape>", lambda event: self.destroy())

    def _build_ui(self):
        frm = ttk.Frame(self, padding=(12, 12, 12, 12))
        frm.pack(fill=tk.BOTH, expand=True)

        issue_lbl = ttk.Label(frm, text=f"Issue: {self._issue_key}", font=("", 10, "bold"))
        issue_lbl.pack(anchor=tk.W, pady=(0, 10))

        row1 = ttk.Frame(frm)
        row1.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(row1, text="Time spent:", width=13, anchor=tk.W).pack(side=tk.LEFT)
        self.entry_time_spent = ttk.Entry(row1, width=15)
        self.entry_time_spent.insert(0, "1h")
        self.entry_time_spent.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Label(row1, text="(예: 2h, 1d 4h, 30m)", foreground="gray").pack(side=tk.LEFT)

        row2 = ttk.Frame(frm)
        row2.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(row2, text="Date started:", width=13, anchor=tk.W).pack(side=tk.LEFT)
        self.lbl_date = ttk.Label(row2, text=self._started_datetime.strftime("%Y-%m-%d %H:%M"), width=18)
        self.lbl_date.pack(side=tk.LEFT, padx=(0, 5))
        self.btn_pick_date = ttk.Button(row2, text="변경", command=self._pick_datetime, width=6)
        self.btn_pick_date.pack(side=tk.LEFT)

        ttk.Label(frm, text="Work description:").pack(anchor=tk.W, pady=(10, 2))
        self.txt_comment = tk.Text(frm, width=48, height=3, font="TkDefaultFont")
        self.txt_comment.pack(fill=tk.X)
        if self._initial_comment:
            self.txt_comment.insert("1.0", self._initial_comment + " ")

        btn_frame = ttk.Frame(frm)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        ttk.Button(btn_frame, text="Create", command=self._confirm).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)

    def _pick_datetime(self):
        dp = DatePickerDialog(self, initial_date=self._started_datetime.date(), title="시작 날짜 선택")
        self.wait_window(dp)
        if dp.result is None:
            return
        tp = TimePickerDialog(self, initial_time=self._started_datetime.time(), title="시작 시간 선택")
        self.wait_window(tp)
        if tp.result is None:
            return
        h, m = tp.result
        new_dt = datetime(dp.result.year, dp.result.month, dp.result.day, h, m)
        self._started_datetime = new_dt.astimezone()
        self.lbl_date.config(text=self._started_datetime.strftime("%Y-%m-%d %H:%M"))

    def _confirm(self):
        time_spent = self.entry_time_spent.get().strip()
        if not time_spent:
            messagebox.showerror("오류", "Time spent를 입력해주세요.", parent=self)
            return
        comment = self.txt_comment.get("1.0", tk.END).strip()
        started_str = self._started_datetime.strftime("%Y-%m-%dT%H:%M:%S.000%z")
        self.result = {
            "time_spent": time_spent,
            "comment": comment,
            "started": started_str,
        }
        self.destroy()

class AddWorkItemDialog(tk.Toplevel):
    def __init__(self, parent, project_key, members=None, initial_name=""):
        super().__init__(parent)
        self.title(f"Add Work Item - {project_key}")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        x = parent_x + (parent_w - 430) // 2
        y = parent_y + (parent_h - 350) // 2
        self.geometry(f"430x350+{max(0, x)}+{max(0, y)}")

        self._project_key = project_key
        self._members = members or []
        self._initial_name = initial_name
        self.result = None

        self._build_ui()

        self.bind("<Escape>", lambda event: self.destroy())

    def _build_ui(self):
        frm = ttk.Frame(self, padding=(12, 12, 12, 12))
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text=f"Project: {self._project_key}", font=("", 10, "bold")).pack(anchor=tk.W, pady=(0, 10))

        row1 = ttk.Frame(frm)
        row1.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(row1, text="Name:", width=13, anchor=tk.W).pack(side=tk.LEFT)
        self.entry_name = ttk.Entry(row1, width=40)
        self.entry_name.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if self._initial_name:
            self.entry_name.insert(0, self._initial_name)

        row2 = ttk.Frame(frm)
        row2.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(row2, text="Reporter:", width=13, anchor=tk.W).pack(side=tk.LEFT)
        self.cbo_reporter = ttk.Combobox(row2, width=37, state="readonly")
        member_names = [name for name, aid, email in self._members]
        self.cbo_reporter["values"] = member_names
        default_idx = 0
        for i, name in enumerate(member_names):
            if "이정규" in name:
                default_idx = i
                break
        if member_names:
            self.cbo_reporter.current(default_idx)
        self.cbo_reporter.pack(side=tk.LEFT, fill=tk.X, expand=True)

        row3 = ttk.Frame(frm)
        row3.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(row3, text="Start date:", width=13, anchor=tk.W).pack(side=tk.LEFT)
        self.lbl_start = ttk.Label(row3, text=date.today().isoformat(), width=15)
        self.lbl_start.pack(side=tk.LEFT, padx=(0, 5))
        self.btn_pick_start = ttk.Button(row3, text="변경", command=self._pick_start_date, width=6)
        self.btn_pick_start.pack(side=tk.LEFT)

        row4 = ttk.Frame(frm)
        row4.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(row4, text="Due date:", width=13, anchor=tk.W).pack(side=tk.LEFT)
        self.lbl_due = ttk.Label(row4, text="", width=15)
        self.lbl_due.pack(side=tk.LEFT, padx=(0, 5))
        self.btn_pick_due = ttk.Button(row4, text="변경", command=self._pick_due_date, width=6)
        self.btn_pick_due.pack(side=tk.LEFT)
        self.btn_clear_due = ttk.Button(row4, text="지움", command=self._clear_due_date, width=6)
        self.btn_clear_due.pack(side=tk.LEFT, padx=(5, 0))

        info_lbl = ttk.Label(frm, text="Assignee와 Team은 현재 로그인 사용자 기준으로 자동 할당됩니다.",
                             foreground="gray", font=("", 8))
        info_lbl.pack(anchor=tk.W, pady=(5, 10))

        btn_frame = ttk.Frame(frm)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))
        ttk.Button(btn_frame, text="OK", command=self._confirm).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)

    def _pick_start_date(self):
        dp = DatePickerDialog(self, initial_date=date.today(), title="Start date 선택")
        self.wait_window(dp)
        if dp.result is not None:
            self.lbl_start.config(text=dp.result.isoformat())

    def _pick_due_date(self):
        dp = DatePickerDialog(self, initial_date=date.today(), title="Due date 선택")
        self.wait_window(dp)
        if dp.result is not None:
            self.lbl_due.config(text=dp.result.isoformat())

    def _clear_due_date(self):
        self.lbl_due.config(text="")

    def _confirm(self):
        name = self.entry_name.get().strip()
        if not name:
            messagebox.showerror("오류", "Name을 입력해주세요.", parent=self)
            return
        reporter_name = self.cbo_reporter.get()
        reporter_account_id = ""
        for n, aid, _ in self._members:
            if n == reporter_name:
                reporter_account_id = aid
                break
        self.result = {
            "name": name,
            "reporter": reporter_name,
            "reporter_account_id": reporter_account_id,
            "start_date": self.lbl_start.cget("text") or None,
            "due_date": self.lbl_due.cget("text") or None,
        }
        self.destroy()

class LogWorkPopup(tk.Toplevel):
    _last_space = None
    _last_column = None
    _last_issue = None

    def __init__(self, parent, session, api_token, user_email):
        super().__init__(parent)
        self.title("Log Work")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        x = parent_x + (parent_w - 450) // 2
        y = parent_y + (parent_h - 280) // 2
        self.geometry(f"450x280+{max(0, x)}+{max(0, y)}")

        self._session = session
        self._api_token = api_token
        self._user_email = user_email
        self._projects = []
        self._columns = []
        self._issues = []
        self._board_id = None

        self._build_ui()
        self._load_projects()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        frm = ttk.Frame(self, padding=(10, 10, 10, 10))
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Spaces:").pack(anchor=tk.W)
        self.cbo_spaces = ttk.Combobox(frm, width=50, state="readonly")
        self.cbo_spaces.pack(fill=tk.X, pady=(2, 10))
        self.cbo_spaces.bind("<<ComboboxSelected>>", self._on_space_select)

        ttk.Label(frm, text="Column:").pack(anchor=tk.W)
        self.cbo_columns = ttk.Combobox(frm, width=50, state="readonly")
        self.cbo_columns.pack(fill=tk.X, pady=(2, 10))
        self.cbo_columns.bind("<<ComboboxSelected>>", self._on_column_select)

        ttk.Label(frm, text="Work Item:").pack(anchor=tk.W)
        self.cbo_issues = ttk.Combobox(frm, width=50, state="readonly")
        self.cbo_issues.pack(fill=tk.X, pady=(2, 10))
        self.cbo_issues.bind("<<ComboboxSelected>>", self._on_issue_select)

        self.lbl_status = ttk.Label(frm, text="", foreground="gray", font=("", 8))
        self.lbl_status.pack(anchor=tk.W, pady=(0, 5))

        btn_frame = ttk.Frame(frm)
        btn_frame.pack(fill=tk.X, pady=(5, 0))
        self.btn_add_work_item = ttk.Button(btn_frame, text="Add Work Item", command=self._on_add_work_item)
        self.btn_add_work_item.pack(side=tk.LEFT)
        self.btn_log = ttk.Button(btn_frame, text="Log Work", command=self._on_log_work, state=tk.DISABLED)
        self.btn_log.pack(side=tk.RIGHT)

    def _load_projects(self):
        try:
            self._projects = fetch_projects(self._session)
            labels = [f"[{p['key']}] {p.get('name', '')}" for p in self._projects]
            self.cbo_spaces["values"] = labels

            target_idx = -1
            if LogWorkPopup._last_space is not None:
                for i, label in enumerate(labels):
                    if label == LogWorkPopup._last_space:
                        target_idx = i
                        break

            if target_idx < 0:
                for i, label in enumerate(labels):
                    if label == "[A10ETC] [기구팀] [ETC]":
                        target_idx = i
                        break
                    elif target_idx < 0 and label.startswith("[A10ETC]"):
                        target_idx = i

            if target_idx < 0 and labels:
                target_idx = 0

            if target_idx >= 0:
                self.cbo_spaces.current(target_idx)
                self._on_space_select()
        except Exception as e:
            messagebox.showerror("오류", f"Project 목록을 불러오지 못했습니다:\n{e}", parent=self)
            self.destroy()

    def _on_space_select(self, event=None):
        idx = self.cbo_spaces.current()
        if idx < 0:
            return
        project = self._projects[idx]
        project_key = project["key"]
        LogWorkPopup._last_space = self.cbo_spaces.get()

        self.cbo_columns["values"] = []
        self.cbo_columns.set("")
        self.cbo_issues["values"] = []
        self.cbo_issues.set("")
        self.btn_log.config(state=tk.DISABLED)

        try:
            all_boards = fetch_boards(self._session)
            matching = [b for b in all_boards
                         if b.get("location", {}).get("projectKey", "").upper() == project_key.upper()]
            if not matching:
                matching = all_boards

            if not matching:
                messagebox.showinfo("안내", "접근 가능한 Board가 없습니다.", parent=self)
                return

            self._board_id = matching[0]["id"]
            columns = fetch_board_columns(self._session, self._board_id)
            self._columns = columns
            col_names = [c.get("name", "") for c in columns]
            self.cbo_columns["values"] = col_names

            target_col_idx = -1
            if LogWorkPopup._last_column is not None:
                for i, name in enumerate(col_names):
                    if name == LogWorkPopup._last_column:
                        target_col_idx = i
                        break

            if target_col_idx < 0:
                for i, name in enumerate(col_names):
                    name_clean = name.replace(" ", "").lower()
                    if name == "In Progress" or name_clean == "inprogress":
                        target_col_idx = i
                        break

            if target_col_idx < 0 and col_names:
                target_col_idx = 0

            if target_col_idx >= 0:
                self.cbo_columns.current(target_col_idx)
                self._on_column_select()
        except Exception as e:
            messagebox.showerror("오류", f"Board/Column 정보를 불러오지 못했습니다:\n{e}", parent=self)

    def _on_column_select(self, event=None):
        col_idx = self.cbo_columns.current()
        if col_idx < 0:
            return

        LogWorkPopup._last_column = self.cbo_columns.get()

        col = self._columns[col_idx]
        col_name = col.get("name", "").strip()
        raw_statuses = col.get("statuses", [])
        col_status_ids = set()
        col_status_names = set()
        for s in raw_statuses:
            if isinstance(s, dict):
                sid = s.get("id")
                sn = s.get("name")
                if sid is not None:
                    col_status_ids.add(str(sid).strip())
                if sn:
                    col_status_names.add(str(sn).strip().lower())

        self.cbo_issues["values"] = []
        self.cbo_issues.set("")
        self.btn_log.config(state=tk.DISABLED)
        if hasattr(self, 'lbl_status'):
            self.lbl_status.config(text="")

        try:
            space_idx = self.cbo_spaces.current()
            project_key = self._projects[space_idx]["key"] if space_idx >= 0 and self._projects else ""

            # 1. Fetch board issues if board_id exists
            all_issues = []
            if self._board_id is not None:
                all_issues = fetch_board_issues(self._session, self._board_id)

            # 2. Fetch project issues directly via JQL and merge to ensure complete coverage
            if project_key:
                proj_issues = fetch_project_issues(self._session, project_key)
                existing_keys = {iss.get("key") for iss in all_issues if iss.get("key")}
                for p_iss in proj_issues:
                    if p_iss.get("key") and p_iss.get("key") not in existing_keys:
                        all_issues.append(p_iss)
                        existing_keys.add(p_iss.get("key"))

            filtered = []
            for iss in all_issues:
                iss_status_obj = iss.get("fields", {}).get("status", {}) or {}
                iss_status_id = str(iss_status_obj.get("id", "")).strip()
                iss_status_name = str(iss_status_obj.get("name", "")).strip()
                iss_status_name_lower = iss_status_name.lower()

                matched = False
                if col_status_ids and iss_status_id in col_status_ids:
                    matched = True
                elif col_status_names and iss_status_name_lower in col_status_names:
                    matched = True
                elif col_name and iss_status_name_lower == col_name.lower():
                    matched = True

                if matched:
                    filtered.append(iss)

            # Fallback 1: If exact match yielded no issues, check partial match with col_name
            if not filtered and col_name:
                for iss in all_issues:
                    iss_status_name = str(iss.get("fields", {}).get("status", {}).get("name", "")).strip().lower()
                    if col_name.lower() in iss_status_name or iss_status_name in col_name.lower():
                        filtered.append(iss)

            # Fallback 2: If still no issues matched status filter for this column, show all issues of the project/board
            if not filtered:
                filtered = all_issues

            self._issues = filtered
            labels = [f"{iss['key']}: {iss.get('fields', {}).get('summary', '')}" for iss in filtered]
            self.cbo_issues["values"] = labels
            if hasattr(self, 'lbl_status'):
                total = len(all_issues)
                matched_cnt = len(filtered)
                status_info = ", ".join(col_status_names) if col_status_names else col_name
                self.lbl_status.config(text=f"컬럼: {col_name} (상태: {status_info}) | 전체: {total}개 | 표시: {matched_cnt}개")
        except Exception as e:
            messagebox.showerror("오류", f"Work Item 목록을 불러오지 못했습니다:\n{e}", parent=self)

        if self.cbo_issues["values"]:
            restored = False
            if LogWorkPopup._last_issue is not None:
                for i, label in enumerate(self.cbo_issues["values"]):
                    if label == LogWorkPopup._last_issue:
                        self.cbo_issues.current(i)
                        restored = True
                        break
            if not restored:
                self.cbo_issues.current(0)
            self.btn_log.config(state=tk.NORMAL)

    def _on_issue_select(self, event=None):
        LogWorkPopup._last_issue = self.cbo_issues.get()

    def _append_issue(self, issue_key, summary):
        self._issues.append({"key": issue_key, "fields": {"summary": summary}})
        labels = [f"{iss['key']}: {iss.get('fields', {}).get('summary', '')}" for iss in self._issues]
        self.cbo_issues["values"] = labels
        for i, label in enumerate(labels):
            if label.startswith(issue_key):
                self.cbo_issues.current(i)
                self.btn_log.config(state=tk.NORMAL)
                break
        if hasattr(self, 'lbl_status'):
            self.lbl_status.config(text=f"표시: {len(labels)}개 (+ 새 항목: {issue_key})")

    def _on_close(self):
        LogWorkPopup._last_issue = self.cbo_issues.get()
        LogWorkPopup._last_column = self.cbo_columns.get()
        LogWorkPopup._last_space = self.cbo_spaces.get()
        self.destroy()

    def _on_add_work_item(self):
        idx = self.cbo_spaces.current()
        if idx < 0:
            messagebox.showinfo("안내", "Project(Spaces)를 먼저 선택해주세요.", parent=self)
            return
        project_key = self._projects[idx]["key"]

        space_label = self.cbo_spaces.get()
        members = load_members("members.csv")
        dlg = AddWorkItemDialog(self, project_key, members=members, initial_name=space_label)
        self.wait_window(dlg)
        if dlg.result is None:
            return

        name = dlg.result["name"]
        start_date = dlg.result["start_date"]
        due_date = dlg.result["due_date"]
        reporter_account_id = dlg.result.get("reporter_account_id", "")

        def do_create():
            try:
                account_id = get_current_account_id(self._session)

                result = create_issue_remote(
                    project_key,
                    summary=name,
                    start_date=start_date,
                    due_date=due_date,
                    assignee_account_id=account_id,
                    reporter_account_id=reporter_account_id,
                    api_token=self._api_token,
                    user_email=self._user_email,
                )
                issue_key = result.get("key", "")

                col_idx = self.cbo_columns.current()
                target_names = []
                if col_idx >= 0 and self._board_id is not None:
                    col = self._columns[col_idx]
                    col_name = col.get("name", "")
                    statuses = col.get("statuses", [])
                    target_names = [col_name] + [s.get("name", "") for s in statuses if s.get("name")]
                    target_names = list(dict.fromkeys(target_names))
                    if target_names:
                        ok = transition_issue_to_status(self._session, issue_key, target_names)
                        if not ok:
                            self.after(0, lambda: messagebox.showwarning("알림",
                                f"'{col_name}' 컬럼으로 이동할 수 없습니다.\n사용 가능한 상태: {target_names}", parent=self))

                self.after(0, lambda: self._append_issue(issue_key, name))
                self.after(0, lambda: messagebox.showinfo("완료", f"Work Item이 생성되었습니다.\n{issue_key}", parent=self))
            except requests.exceptions.HTTPError as e:
                body = e.response.text if e.response is not None else ""
                self.after(0, lambda body=body, e=e: messagebox.showerror("오류", f"Work Item 생성 실패:\n{type(e).__name__}: {e}\n\n{body}", parent=self))
            except Exception as e:
                self.after(0, lambda e=e: messagebox.showerror("오류", f"Work Item 생성 실패:\n{type(e).__name__}: {e}", parent=self))
        threading.Thread(target=do_create, daemon=True).start()

    def _on_log_work(self):
        LogWorkPopup._last_issue = self.cbo_issues.get()
        idx = self.cbo_issues.current()
        if idx < 0:
            return
        issue_key = self._issues[idx]["key"]

        issue_label = self.cbo_issues.get()
        dlg = LogWorkDialog(self, issue_key, initial_comment=issue_label)
        self.wait_window(dlg)
        if dlg.result is None:
            return

        time_spent = dlg.result["time_spent"]
        comment = dlg.result["comment"]
        started = dlg.result["started"]

        def do_create():
            try:
                add_worklog_remote(
                    issue_key,
                    time_spent=time_spent,
                    comment=comment,
                    started=started,
                    api_token=self._api_token,
                    user_email=self._user_email,
                )
                self.after(0, lambda: messagebox.showinfo("완료", f"Worklog가 생성되었습니다.\n{issue_key}: {time_spent}", parent=self))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("오류", f"Worklog 생성 실패:\n{e}", parent=self))
        threading.Thread(target=do_create, daemon=True).start()

class JiraMemberAdderDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Jira Member Adder")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        x = parent_x + (parent_w - 450) // 2
        y = parent_y + (parent_h - 260) // 2
        self.geometry(f"450x260+{max(0, x)}+{max(0, y)}")

        self.parent = parent
        self.success = False

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        frm = ttk.Frame(self, padding=(20, 15, 20, 15))
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Jira Member Adder - 최초 인증 설정", font=("TkDefaultFont", 11, "bold")).pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(frm, text="Jira API Email:").pack(anchor=tk.W)
        self.entry_email = ttk.Entry(frm, width=50)
        self.entry_email.pack(fill=tk.X, pady=(2, 10))
        if getattr(self.parent, "_auth_email", ""):
            self.entry_email.insert(0, self.parent._auth_email)

        ttk.Label(frm, text="Jira API Token:").pack(anchor=tk.W)
        self.entry_token = ttk.Entry(frm, width=50, show="*")
        self.entry_token.pack(fill=tk.X, pady=(2, 10))
        if getattr(self.parent, "_api_token", ""):
            self.entry_token.insert(0, self.parent._api_token)

        self.lbl_status = ttk.Label(frm, text="", foreground="red")
        self.lbl_status.pack(anchor=tk.W, pady=(0, 5))

        btn_frame = ttk.Frame(frm)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(btn_frame, text="저장 및 연결", command=self._on_save).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="취소", command=self._on_close).pack(side=tk.RIGHT)

    def _on_save(self):
        email = self.entry_email.get().strip()
        token = self.entry_token.get().strip()

        if not email:
            self.lbl_status.config(text="Jira API Email을 입력해주세요.")
            return
        if not token:
            self.lbl_status.config(text="Jira API Token을 입력해주세요.")
            return

        self.lbl_status.config(text="Jira 연결 확인 중...", foreground="blue")
        self.update_idletasks()

        try:
            sess = get_session(email, token)
            my_info = get_myself(sess)
            name = my_info.get("displayName", email)

            Path("jira_api_email.txt").write_text(email, encoding="utf-8")
            Path("jira_api_token.txt").write_text(token, encoding="utf-8")

            self.parent._auth_email = email
            self.parent._api_token = token
            self.parent._session = sess
            self.success = True

            messagebox.showinfo("연결 성공", f"Jira 인증 성공!\n안녕하세요, {name}님.", parent=self)
            self.destroy()
        except Exception as e:
            self.lbl_status.config(text="인증 실패: 정보를 확인하세요.", foreground="red")
            messagebox.showerror("인증 오류", f"Jira 연동 실패:\n{e}", parent=self)

    def _on_close(self):
        self.destroy()

class JiraWorklogGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("JIRA_WORKLOG 조회 프로그램")
        self.geometry("1000x300")
        self.minsize(600, 300)
        self._worker = None
        self._df_display = pd.DataFrame()
        self._entry_popup = None
        self._session = None
        self._user_email = ""

        try:
            self._api_token = read_text("jira_api_token.txt")
        except FileNotFoundError:
            self._api_token = ""

        try:
            self._auth_email = read_text("jira_api_email.txt")
        except FileNotFoundError:
            self._auth_email = ""

        if self._auth_email and self._api_token:
            try:
                sess = get_session(self._auth_email, self._api_token)
                get_myself(sess)
                self._session = sess
            except Exception:
                self._session = None

        if not self._session:
            dlg = JiraMemberAdderDialog(self)
            self.wait_window(dlg)

        if not self._session:
            messagebox.showwarning("경고", "인증 정보가 설정되지 않아 프로그램 기능을 사용할 수 없습니다.")

        # Load members from Jira REST API
        self._check_and_load_members()

        self._build_top()
        self._build_table()
        self._build_bottom()
        
        # Select currently logged-in account as default if available
        my_idx = -1
        if self._session:
            try:
                my_info = get_myself(self._session)
                my_aid = my_info.get("accountId", "")
                my_email = (my_info.get("emailAddress") or self._auth_email or "").strip().lower()
                for i, (name, aid, email) in enumerate(self._members):
                    if (my_aid and aid == my_aid) or (my_email and email.strip().lower() == my_email):
                        my_idx = i
                        break
            except Exception:
                pass

        if my_idx < 0 and self._auth_email:
            auth_email_clean = self._auth_email.strip().lower()
            for i, (name, aid, email) in enumerate(self._members):
                if email.strip().lower() == auth_email_clean:
                    my_idx = i
                    break

        if my_idx < 0 and self._members:
            my_idx = 0

        if my_idx >= 0:
            self.cbo_users.current(my_idx)
            self._on_user_select(None)
        
        self.entry_date.config(state="normal")
        self.entry_date.insert(0, date.today().isoformat())
        self.entry_date.config(state="readonly")

    def _check_and_load_members(self):
        self._members = []
        if self._session:
            try:
                self._members = fetch_jira_users(self._session)
            except Exception:
                self._members = []

        if not self._members:
            self._members = load_members("members.csv")

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
        self.btn_log_work = ttk.Button(frm, text="워크로그추가", command=self.on_log_work)
        self.btn_log_work.pack(side=tk.LEFT, padx=(10, 0))
        self.btn_weekly_draft = ttk.Button(frm, text="주간업무초안", command=self.on_weekly_draft)
        self.btn_weekly_draft.pack(side=tk.LEFT, padx=(10, 0))
        self.btn_weekly = ttk.Button(frm, text="Weekly", command=self.on_weekly)
        self.btn_weekly.pack(side=tk.LEFT, padx=(10, 0))

        self.progress = ttk.Label(frm, text="●", font=("", 16), foreground="green")
        self.progress.pack(side=tk.RIGHT, padx=(10, 0))

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
        self.lbl_status = ttk.Label(frm, text="전체합계시간: 0.00 h", font=("TkDefaultFont", 16, "bold"), foreground="red")
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



    def on_log_work(self):
        if not self._session:
            messagebox.showwarning("경고", "인증 정보가 설정되지 않아 Log Work 기능을 사용할 수 없습니다.")
            return

        self._lock_ui(True)
        self.update_idletasks()

        def do_open():
            try:
                popup = LogWorkPopup(self, self._session, self._api_token, self._auth_email)
                self.wait_window(popup)
            finally:
                self._lock_ui(False)

        self.after(10, do_open)

    def on_weekly_draft(self):
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("안내", "이미 작업 중입니다. 잠시만 기다려주세요.")
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

        self._lock_ui(True)
        self._worker = threading.Thread(
            target=self._run_weekly_draft_worker,
            args=(date_str, target_account_id),
            daemon=True
        )
        self._worker.start()

    def _run_weekly_draft_worker(self, date_str: str, target_account_id: str):
        try:
            ref_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            start_week1 = ref_date - timedelta(days=ref_date.weekday())
            end_week1 = start_week1 + timedelta(days=6)
            start_week2 = start_week1 + timedelta(days=7)
            end_week2 = start_week2 + timedelta(days=6)

            api_token = read_text("jira_api_token.txt")
            auth_email = self._auth_email or read_text("jira_api_email.txt")
            sess = get_session(auth_email, api_token)

            jql = f"worklogAuthor = '{target_account_id}' AND worklogDate >= '{start_week1.isoformat()}' AND worklogDate <= '{end_week2.isoformat()}'"
            issue_keys = enhanced_search_issue_keys(sess, jql=jql, fields=["key"], page_size=100)

            rows = []
            for key in issue_keys:
                for wl in iter_issue_worklogs(sess, key, page_size=100):
                    wl_author = wl.get("author", {}) or {}
                    wl_account = wl_author.get("accountId", "")
                    if wl_account != target_account_id:
                        continue
                    started_raw = wl.get("started", "")
                    if not started_raw:
                        continue

                    dt = datetime.strptime(started_raw, "%Y-%m-%dT%H:%M:%S.%f%z")
                    wl_date = dt.date()
                    if not (start_week1 <= wl_date <= end_week2):
                        continue

                    week_label = "금주" if start_week1 <= wl_date <= end_week1 else "다음주"
                    suffix = " 완료" if week_label == "금주" else " 목표"
                    date_mmdd = dt.strftime("%m/%d") + suffix

                    rows.append({
                        "주차": week_label,
                        "업무내용": extract_comment_text(wl.get("comment")),
                        "작업날짜": date_mmdd,
                        "작성자": wl_author.get("displayName", ""),
                        "date_sort": dt,
                    })

            rows.sort(key=lambda x: str(x["업무내용"]))

            cols_output = ["주차", "업무내용", "작업날짜", "작성자"]
            if rows:
                df = pd.DataFrame(rows)[cols_output]
            else:
                df = pd.DataFrame(columns=cols_output)

            output_dir = Path("output")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / f"{date_str}.xlsx"

            with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
                df.to_excel(writer, index=False)
                ws = writer.sheets["Sheet1"]
                for col in ws.columns:
                    max_len = 0
                    col_letter = get_column_letter(col[0].column)
                    for cell in col:
                        if cell.value is not None:
                            lines = str(cell.value).split("\n")
                            for line in lines:
                                line_len = sum(2 if ord(c) > 127 else 1 for c in line)
                                if line_len > max_len:
                                    max_len = line_len
                    ws.column_dimensions[col_letter].width = max(max_len + 4, 10)

            self.after(0, self._on_weekly_draft_success, str(output_file))
        except Exception as e:
            self.after(0, self._on_weekly_draft_error, str(e))

    def _on_weekly_draft_success(self, output_filepath: str):
        self._lock_ui(False)
        try:
            if sys.platform == "win32":
                os.startfile(output_filepath)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", output_filepath])
            else:
                subprocess.Popen(["xdg-open", output_filepath])
        except Exception as e:
            messagebox.showwarning("완료 (열기 실패)", f"엑셀 파일이 생성되었으나 열지 못했습니다:\n{e}\n\n파일 경로: {output_filepath}")

    def _on_weekly_draft_error(self, err_msg: str):
        self._lock_ui(False)
        messagebox.showerror("오류", f"주간업무초안 작성 중 오류가 발생했습니다:\n{err_msg}")

    def on_weekly(self):
        try:
            weekly_script = Path("weekly/weekly.py")
            if weekly_script.exists():
                subprocess.Popen([sys.executable, str(weekly_script)])
            else:
                messagebox.showerror("오류", "weekly/weekly.py 파일을 찾을 수 없습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"Weekly 실행 실패:\n{e}")

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
        self.btn_log_work.config(state=state)
        if lock:
            self.progress.config(foreground="red")
        else:
            self.progress.config(foreground="green")

    def _clear_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _fill_table_from_df(self, df_display: pd.DataFrame):
        self._clear_table()
        if df_display is None or df_display.empty:
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
        text = tk.Text(win, wrap="word", height=14, width=46, font="TkDefaultFont")
        text.insert(1.0, msg)
        text.configure(state=tk.DISABLED)
        text.pack(fill=tk.BOTH, expand=True, padx=12, pady=9)
        btn = ttk.Button(win, text="닫기", command=win.destroy)
        btn.pack(pady=(0, 10))

if __name__ == "__main__":
    app = JiraWorklogGUI()
    app.iconbitmap('robot_1211_V01.ico')
    app.mainloop()
