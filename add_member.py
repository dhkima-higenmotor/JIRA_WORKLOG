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

def append_member(name: str, account_id: str, email: str):
    """
    members.csv 파일에 사용자 정보를 추가한다.
    형식: 이름,AccountId,Email
    """
    csv_path = Path("members.csv")
    new_line = f"{name},{account_id},{email}\n"
    
    # 파일이 없으면 생성 (헤더 포함)
    if not csv_path.exists():
        csv_path.write_text("이름,AccountId,Email\n" + new_line, encoding="utf-8")
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
    print(f"추가되었습니다: {name}, {account_id}, {email}")

class AddMemberGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Jira Member Adder")
        self.geometry("400x320")
        self.resizable(False, False)
        
        self.found_user = None # (name, aid)
        
        self.api_token = ""
        try:
            self.api_token = read_text("jira_api_token.txt")
        except FileNotFoundError:
            pass # Acceptable, user can input in GUI

        self._build_ui()

    def _build_ui(self):
        ENTRY_WIDTH = 30
        
        # API Token Input
        lbl_token = ttk.Label(self, text="Jira API Token:")
        lbl_token.pack(pady=(15, 5))
        
        self.entry_api_token = ttk.Entry(self, show="*")
        self.entry_api_token.pack(fill=tk.X, padx=20)
        
        if self.api_token:
            self.entry_api_token.insert(0, self.api_token)

        # My Email Input
        lbl_my_email = ttk.Label(self, text="jira_api_email:")
        lbl_my_email.pack(pady=(10, 5))
        
        self.entry_my_email = ttk.Entry(self)
        self.entry_my_email.pack(fill=tk.X, padx=20)
        
        # Load saved email if exists
        try:
            saved_email = Path("jira_api_email.txt").read_text(encoding="utf-8").strip()
            self.entry_my_email.insert(0, saved_email)
        except Exception:
            pass

        # Target Email Input
        lbl_instruction = ttk.Label(self, text="Search User by Email:")
        lbl_instruction.pack(pady=(15, 5))
        
        self.entry_email = ttk.Entry(self)
        self.entry_email.pack(fill=tk.X, padx=20)
        self.entry_email.bind("<Return>", self.on_add_user_click)
        
        # Result Status
        self.lbl_result = ttk.Label(self, text="...", font=("Arial", 10, "bold"))
        self.lbl_result.pack(pady=10)
        
        # Status Bar (Pack First to be at very bottom)
        self.lbl_status = ttk.Label(self, text="Ready", relief=tk.SUNKEN, anchor=tk.W)
        self.lbl_status.pack(side=tk.BOTTOM, fill=tk.X)

        # Buttons Frame (Bottom, above status)
        frm_buttons = ttk.Frame(self)
        frm_buttons.pack(side=tk.BOTTOM, pady=10)

        self.btn_add_user = ttk.Button(frm_buttons, text="Add User", command=self.on_add_user_click)
        self.btn_add_user.pack(side=tk.LEFT, padx=5)
        
        self.btn_exit = ttk.Button(frm_buttons, text="EXIT", command=self.destroy)
        self.btn_exit.pack(side=tk.LEFT, padx=5)

    def on_add_user_click(self, event=None):
        email = self.entry_email.get().strip()
        if not email:
            messagebox.showwarning("Warning", "Please enter an email address.")
            return
            
        # Validate API Token
        api_token_input = self.entry_api_token.get().strip()
        if not api_token_input:
             messagebox.showwarning("Warning", "Please enter Jira API Token.")
             return
        
        # Save API Token
        try:
            Path("jira_api_token.txt").write_text(api_token_input, encoding="utf-8")
            self.api_token = api_token_input
        except Exception as e:
            print(f"Failed to save api token: {e}")

        self.lbl_status.config(text="Searching...")
        self.update_idletasks()
        
        # Validate my email
        my_email = self.entry_my_email.get().strip()
        if not my_email:
            messagebox.showwarning("Warning", "Please enter YOUR email for authentication.")
            return
            
        # Save email for future use
        try:
            Path("jira_api_email.txt").write_text(my_email, encoding="utf-8")
        except Exception as e:
            print(f"Failed to save auth email: {e}")

        self.lbl_status.config(text="Searching...")
        self.update_idletasks()
        
        result = None
        try:
            sess = get_session(my_email, self.api_token)
            result = search_user_by_email(sess, email)
        except Exception as e:
            messagebox.showerror("Search Error", f"{e}")
            self.lbl_status.config(text="Error occurred.")
            return
        
        if result:
            name, aid = result
            # Found user, now add immediately
            try:
                append_member(name, aid, email)
                self.lbl_result.config(text=f"Added: {name}", foreground="blue")
                self.lbl_status.config(text=f"Successfully added {name} to members.csv")
                
                # Clear input for next
                self.entry_email.delete(0, tk.END)
                messagebox.showinfo("Success", f"User added:\nName: {name}\nEmail: {email}")
                
            except Exception as e:
                self.lbl_result.config(text="Error adding member", foreground="red")
                messagebox.showerror("Error", f"Failed to append member: {e}")
        else:
            self.lbl_result.config(text="User not found.", foreground="red")
            self.lbl_status.config(text="User not found.")
            
    # Removed separate on_add method

def main():
    # GUI Mode -> Always open GUI
    app = AddMemberGUI()
    app.mainloop()

if __name__ == "__main__":
    main()
