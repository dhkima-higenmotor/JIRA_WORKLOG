import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

import requests
from requests.auth import HTTPBasicAuth

# Import auth and config from main.py
try:
    from main import get_session, read_text, BASE_URL
except ImportError:
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

def search_user_by_email(sess: requests.Session, email: str):
    """
    이메일로 사용자를 검색하여 (displayName, accountId)를 반환한다.
    검색 실패 시 None 반환.
    """
    url = BASE_URL + "user/search"
    params = {"query": email}
    try:
        r = sess.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        # 첫 번째 검색 결과 반환
        user = data[0]
        return user.get("displayName"), user.get("accountId")
    except Exception as e:
        print(f"Error searching user: {e}")
        return None

def append_member(name: str, account_id: str):
    """
    members.csv 파일에 사용자 정보를 추가한다.
    """
    csv_path = Path("members.csv")
    new_line = f"{name}, {account_id}\n"
    
    # 파일이 없으면 생성 (헤더 포함)
    if not csv_path.exists():
        csv_path.write_text("이름,AccountId\n" + new_line, encoding="utf-8")
        return

    # 중복 체크
    content = csv_path.read_text(encoding="utf-8")
    if account_id in content:
        print(f"이미 존재하는 Account ID 입니다: {account_id}")
        return

    # 마지막 줄이 개행으로 끝나지 않으면 개행 추가
    if content and not content.endswith("\n"):
        new_line = "\n" + new_line

    with open(csv_path, "a", encoding="utf-8") as f:
        f.write(new_line)
    print(f"추가되었습니다: {name}, {account_id}")

class AddMemberGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Jira Member Adder")
        self.geometry("400x250")
        self.resizable(False, False)
        
        self.found_user = None # (name, aid)
        
        try:
            self.api_token = read_text("jira_api_token.txt")
            self.user_email = read_text("user_email.txt")
        except FileNotFoundError:
            messagebox.showerror("Error", "Configuration files (jira_api_token.txt, user_email.txt) not found.")
            self.destroy()
            return

        self._build_ui()

    def _build_ui(self):
        # Email Input
        lbl_instruction = ttk.Label(self, text="Search by Email:")
        lbl_instruction.pack(pady=(20, 5))
        
        frm_search = ttk.Frame(self)
        frm_search.pack(pady=5)
        
        self.entry_email = ttk.Entry(frm_search, width=30)
        self.entry_email.pack(side=tk.LEFT, padx=5)
        self.entry_email.bind("<Return>", self.on_search)
        
        self.btn_search = ttk.Button(frm_search, text="Search", command=self.on_search)
        self.btn_search.pack(side=tk.LEFT)

        # Result Display
        self.lbl_result = ttk.Label(self, text="...", font=("Arial", 10, "bold"))
        self.lbl_result.pack(pady=20)

        # Add Button
        self.btn_add = ttk.Button(self, text="Add to members.csv", state=tk.DISABLED, command=self.on_add)
        self.btn_add.pack(pady=5)
        
        # Status Bar
        self.lbl_status = ttk.Label(self, text="Ready", relief=tk.SUNKEN, anchor=tk.W)
        self.lbl_status.pack(side=tk.BOTTOM, fill=tk.X)

    def on_search(self, event=None):
        email = self.entry_email.get().strip()
        if not email:
            messagebox.showwarning("Warning", "Please enter an email address.")
            return
            
        self.lbl_status.config(text="Searching...")
        self.update_idletasks()
        
        sess = get_session(self.user_email, self.api_token)
        result = search_user_by_email(sess, email)
        
        if result:
            name, aid = result
            self.found_user = (name, aid)
            self.lbl_result.config(text=f"{name}\n({aid})", foreground="blue")
            self.btn_add.config(state=tk.NORMAL)
            self.lbl_status.config(text="User found.")
        else:
            self.found_user = None
            self.lbl_result.config(text="User not found.", foreground="red")
            self.btn_add.config(state=tk.DISABLED)
            self.lbl_status.config(text="User not found.")

    def on_add(self):
        if not self.found_user:
            return
            
        name, aid = self.found_user
        try:
            append_member(name, aid)
            self.lbl_status.config(text=f"Added: {name}")
            messagebox.showinfo("Success", f"Added to members.csv:\n{name}")
            self.entry_email.delete(0, tk.END)
            self.lbl_result.config(text="...")
            self.btn_add.config(state=tk.DISABLED)
            self.found_user = None
        except Exception as e:
            messagebox.showerror("Error", f"Failed to append member: {e}")

def main():
    # GUI Mode -> Always open GUI
    app = AddMemberGUI()
    app.mainloop()

if __name__ == "__main__":
    main()
