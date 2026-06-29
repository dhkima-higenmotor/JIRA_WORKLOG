import os
import json
import shutil
import subprocess
import datetime
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk


# Mode (sidebar) definitions: index -> (key, label, icon, position)
# position: "top"  -> packed from the top of the sidebar
#           "bottom" -> packed from the bottom of the sidebar
MODE_DASHBOARD = 0
MODE_SPACE_WORK = 1
MODE_DAILY = 2
MODE_WEEKLY = 3
MODE_CONFIG = 4
MODE_HELP = 5

MODES = [
    (MODE_DASHBOARD,  "Dashboard",  " \U0001F4CA Dashboard",  "top"),
    (MODE_SPACE_WORK, "Space", " \U0001F4CD Space", "top"),
    (MODE_DAILY,      "Daily",      " \U0001F4C5 Daily",      "top"),
    (MODE_WEEKLY,     "Weekly",     " \U0001F5D3 Weekly",     "top"),
    (MODE_CONFIG,     "Config",     " \u2699\ufe0f Config",   "bottom"),
    (MODE_HELP,       "Help",       " \U0001F4A1 Help",       "bottom"),
]


def load_style(style_path):
    """Load the ui_style.json file and return the parsed dict.

    Returns an empty dict on failure so the caller can fall back to defaults.
    """
    if not os.path.exists(style_path):
        print(f"[style] ui_style.json not found at: {style_path}")
        return {}
    try:
        with open(style_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[style] Failed to load ui_style.json: {e}")
        return {}


def _font_tuple(font_cfg):
    """Convert a font dict from ui_style.json into a tk font tuple."""
    family = font_cfg.get("family", "Segoe UI")
    size = font_cfg.get("size", 10)
    weight = font_cfg.get("weight", "normal")
    if weight == "bold":
        return (family, size, "bold")
    return (family, size)


class JiraWorklogApp(tk.Tk):
    """Main application window for JIRA Worklog.

    Layout (mirrors the GIT4SW project style):
        +--------------------------------------------------+
        |  [Sidebar]  |  [Content area - per mode]         |
        |  Dashboard  |                                     |
        |  Space |  (empty for now - filled in later) |
        |  Daily      |                                     |
        |  Weekly     |                                     |
        |             |                                     |
        |  ...        |                                     |
        |  Config     |                                     |
        |  Help       |                                     |
        +--------------------------------------------------+
        |  System Log (header + text body)                 |
        +--------------------------------------------------+
    """

    SIDEBAR_WIDTH = 200

    def __init__(self, style_path="ui_style.json"):
        super().__init__()

        self.style_path = os.path.abspath(style_path)
        self.style = load_style(self.style_path)

        # Cached color / font lookups (with safe fallbacks)
        c = self.style.get("colors", {})
        self.col_window_bg = c.get("window", {}).get("background", "#f3f4f6")
        self.col_window_fg = c.get("window", {}).get("text_default", "#1f2937")

        sb = c.get("sidebar", {})
        self.col_sidebar_bg = sb.get("background", "#ffffff")
        self.col_sb_btn_bg = sb.get("button_background", "#e5e7eb")
        self.col_sb_btn_fg = sb.get("button_foreground", "#374151")
        self.col_sb_btn_active_bg = sb.get("button_active_background", "#d1d5db")
        self.col_sb_btn_active_fg = sb.get("button_active_foreground", "#111827")
        self.col_sb_sel_bg = sb.get("selected_background", "#059669")
        self.col_sb_sel_fg = sb.get("selected_foreground", "#ffffff")
        self.col_sb_sel_active_bg = sb.get("selected_active_background", "#047857")
        self.col_sb_sel_active_fg = sb.get("selected_active_foreground", "#ffffff")

        card = c.get("card", {})
        self.col_card_bg = card.get("background", "#ffffff")
        self.col_card_border = card.get("border_color", "#e5e7eb")
        self.col_card_title_fg = card.get("title_foreground", "#059669")
        self.col_card_text_fg = card.get("text_foreground", "#1f2937")

        w = c.get("widgets", {})
        self.col_border = w.get("border_color", "#e5e7eb")
        self.col_divider = w.get("divider_color", "#e5e7eb")
        self.col_entry_bg = w.get("entry_background", "#ffffff")
        self.col_entry_fg = w.get("entry_foreground", "#1f2937")

        cb = c.get("combobox", {})
        self.col_cb_field_bg = cb.get("field_background", "#ffffff")
        self.col_cb_bg = cb.get("background", "#e5e7eb")
        self.col_cb_fg = cb.get("foreground", "#1f2937")
        self.col_cb_sel_bg = cb.get("select_background", "#d1fae5")
        self.col_cb_sel_fg = cb.get("select_foreground", "#065f46")
        self.col_cb_disabled_field_bg = cb.get("disabled_field_background", "#f3f4f6")
        self.col_cb_disabled_fg = cb.get("disabled_foreground", "#9ca3af")
        self.col_cb_lb_bg = cb.get("listbox_background", "#ffffff")
        self.col_cb_lb_fg = cb.get("listbox_foreground", "#1f2937")
        self.col_cb_lb_sel_bg = cb.get("listbox_select_background", "#d1fae5")
        self.col_cb_lb_sel_fg = cb.get("listbox_select_foreground", "#065f46")

        log_c = c.get("log", {})
        self.col_log_bg = log_c.get("background", "#f9fafb")
        self.col_log_fg = log_c.get("foreground", "#1f2937")
        self.col_log_border = log_c.get("border_color", "#e5e7eb")
        self.col_log_focus = log_c.get("focus_color", "#059669")
        self.col_log_ts = log_c.get("timestamp_foreground", "#6b7280")
        self.col_log_info = log_c.get("info_foreground", "#3b82f6")
        self.col_log_success = log_c.get("success_foreground", "#10b981")
        self.col_log_warning = log_c.get("warning_foreground", "#f59e0b")
        self.col_log_error = log_c.get("error_foreground", "#ef4444")
        self.col_log_idle = log_c.get("idle_foreground", "#10b981")
        self.col_log_busy = log_c.get("busy_foreground", "#ef4444")

        # Fonts
        fcfg = self.style.get("fonts", {})
        self.font_default = _font_tuple(fcfg.get("default", {"family": "Segoe UI", "size": 10}))
        self.font_title = _font_tuple(fcfg.get("title", {"family": "Segoe UI", "size": 14, "weight": "bold"}))
        self.font_card_title = _font_tuple(fcfg.get("card_title", {"family": "Segoe UI", "size": 11, "weight": "bold"}))
        self.font_sidebar = _font_tuple(fcfg.get("sidebar", {"family": "Segoe UI", "size": 12, "weight": "bold"}))
        self.font_log = _font_tuple(fcfg.get("log", {"family": "Segoe UI", "size": 10}))

        # State
        self.current_view_index = 0
        self.is_busy = False

        # Window
        self.title("JIRA Worklog")
        self.geometry("1100x600")
        self.configure(bg=self.col_window_bg)

        script_dir = os.path.dirname(os.path.abspath(__file__))

        # Window icon (optional, ignore if missing)
        icon_path = os.path.join(script_dir, "robot_1211_V01.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(default=icon_path)
            except Exception:
                try:
                    self.iconbitmap(icon_path)
                except Exception:
                    pass

        self.config_path = os.path.join(script_dir, "config.json")
        self.config_data = self._load_config()

        self.setup_styles()
        self.init_ui()

        # Start on Dashboard
        self.switch_view(MODE_DASHBOARD)

        # Welcome log
        self.write_log("JIRA Worklog started.", "info")

    def _load_config(self):
        """Load config.json. If missing, copy from config.json.template."""
        template_path = os.path.join(os.path.dirname(self.config_path), "config.json.template")
        if not os.path.exists(self.config_path):
            if os.path.exists(template_path):
                try:
                    shutil.copy2(template_path, self.config_path)
                    print("[config] config.json created from template.")
                except Exception as e:
                    print(f"[config] Failed to copy config template: {e}")
                    return {}
            else:
                print("[config] config.json and template not found.")
                return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[config] Failed to load config.json: {e}")
            return {}

    # ------------------------------------------------------------------
    # Style setup (ttk styles driven by ui_style.json)
    # ------------------------------------------------------------------
    def setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        # Frames / labels
        style.configure("TFrame", background=self.col_window_bg)
        style.configure(
            "Card.TFrame",
            background=self.col_card_bg,
            relief="solid",
            borderwidth=1,
            bordercolor=self.col_card_border,
        )
        style.configure("TLabel", background=self.col_window_bg, foreground=self.col_window_fg)
        style.configure(
            "Card.TLabel",
            background=self.col_card_bg,
            foreground=self.col_card_text_fg,
        )
        style.configure(
            "Title.TLabel",
            background=self.col_window_bg,
            foreground=self.col_card_title_fg,
            font=self.font_title,
        )
        style.configure(
            "CardTitle.TLabel",
            background=self.col_card_bg,
            foreground=self.col_card_title_fg,
            font=self.font_card_title,
        )

        # Buttons
        btns = self.style.get("colors", {}).get("buttons", {})

        def_cfg = btns.get("default", {})
        def_active = def_cfg.get("active", {})
        def_active_state = def_cfg.get("active_state", def_active)
        def_disabled = def_cfg.get("disabled", {})
        style.configure(
            "TButton",
            padding=6,
            background=def_active.get("background", "#e5e7eb"),
            foreground=def_active.get("foreground", "#1f2937"),
            borderwidth=0,
            font=self.font_default,
        )
        style.map(
            "TButton",
            background=[
                ("active", def_active_state.get("background", "#d1d5db")),
                ("disabled", def_disabled.get("background", "#f3f4f6")),
            ],
            foreground=[
                ("active", def_active_state.get("foreground", "#111827")),
                ("disabled", def_disabled.get("foreground", "#9ca3af")),
            ],
        )

        prim_cfg = btns.get("primary", {})
        prim_active = prim_cfg.get("active", {})
        prim_active_state = prim_cfg.get("active_state", prim_active)
        prim_disabled = prim_cfg.get("disabled", {})
        style.configure(
            "Primary.TButton",
            padding=6,
            background=prim_active.get("background", "#059669"),
            foreground=prim_active.get("foreground", "#ffffff"),
            borderwidth=0,
            font=self.font_default,
        )
        style.map(
            "Primary.TButton",
            background=[
                ("active", prim_active_state.get("background", "#047857")),
                ("disabled", prim_disabled.get("background", "#f3f4f6")),
            ],
            foreground=[
                ("active", prim_active_state.get("foreground", "#ffffff")),
                ("disabled", prim_disabled.get("foreground", "#9ca3af")),
            ],
        )

        dang_cfg = btns.get("danger", {})
        dang_active = dang_cfg.get("active", {})
        dang_active_state = dang_cfg.get("active_state", dang_active)
        dang_disabled = dang_cfg.get("disabled", {})
        style.configure(
            "Danger.TButton",
            padding=6,
            background=dang_active.get("background", "#ef4444"),
            foreground=dang_active.get("foreground", "#ffffff"),
            borderwidth=0,
            font=self.font_default,
        )
        style.map(
            "Danger.TButton",
            background=[
                ("active", dang_active_state.get("background", "#dc2626")),
                ("disabled", dang_disabled.get("background", "#fee2e2")),
            ],
            foreground=[
                ("active", dang_active_state.get("foreground", "#ffffff")),
                ("disabled", dang_disabled.get("foreground", "#f87171")),
            ],
        )

        # Entry
        style.configure(
            "TEntry",
            fieldbackground=self.col_entry_bg,
            background=self.col_entry_bg,
            foreground=self.col_entry_fg,
            insertcolor=self.col_window_fg,
            borderwidth=1,
            relief="solid",
            padding=4,
        )

        # Combobox (all colors from ui_style.json)
        style.configure(
            "TCombobox",
            fieldbackground=self.col_cb_field_bg,
            background=self.col_cb_bg,
            foreground=self.col_cb_fg,
            selectbackground=self.col_cb_sel_bg,
            selectforeground=self.col_cb_sel_fg,
            borderwidth=1,
            relief="solid",
            padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[
                ("readonly", self.col_cb_field_bg),
                ("disabled", self.col_cb_disabled_field_bg),
            ],
            foreground=[("disabled", self.col_cb_disabled_fg)],
            background=[
                ("active", self.col_sb_btn_active_bg),
                ("readonly", self.col_cb_bg),
            ],
        )
        self.option_add("*TCombobox*Listbox.background", self.col_cb_lb_bg)
        self.option_add("*TCombobox*Listbox.foreground", self.col_cb_lb_fg)
        self.option_add("*TCombobox*Listbox.selectBackground", self.col_cb_lb_sel_bg)
        self.option_add("*TCombobox*Listbox.selectForeground", self.col_cb_lb_sel_fg)

        # Scrollbar (flat, modern)
        style.configure(
            "TScrollbar",
            troughcolor=self.col_window_bg,
            background="#d1d5db",
            bordercolor=self.col_window_bg,
            lightcolor="#d1d5db",
            darkcolor="#d1d5db",
            arrowcolor="#4b5563",
            gripcount=0,
            arrowsize=11,
        )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def init_ui(self):
        # Master layout: top = sidebar + content, bottom = system log
        self.main_container = tk.Frame(self, bg=self.col_window_bg)
        self.main_container.pack(fill="both", expand=True, side="top")

        # System log at the bottom (built first so it pins to the bottom)
        self._build_log_area()

        # Sidebar (left)
        self._build_sidebar()

        # Divider between sidebar and content
        divider = tk.Frame(self.main_container, bg=self.col_divider, width=1)
        divider.pack(side="left", fill="y")

        # Content area (right) - stacked views, show/hide via switch_view
        self.content_frame = tk.Frame(self.main_container, bg=self.col_window_bg)
        self.content_frame.pack(side="right", fill="both", expand=True)

        # Build one placeholder view per mode (to be filled in later)
        self.views = []
        self.view_titles = {}
        for idx, key, label, _pos in MODES:
            if idx == MODE_CONFIG:
                view = self.create_config_view()
            elif idx == MODE_SPACE_WORK:
                view = self.create_space_work_view()
            else:
                view = self._create_placeholder_view(label)
            self.views.append(view)
            self.view_titles[idx] = label

    def _build_sidebar(self):
        sidebar = tk.Frame(
            self.main_container,
            bg=self.col_sidebar_bg,
            width=self.SIDEBAR_WIDTH,
            bd=0,
        )
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        self.sidebar_buttons = {}

        # Top modes
        for idx, _key, label, pos in MODES:
            if pos != "top":
                continue
            btn = tk.Button(
                sidebar,
                text=label,
                fg=self.col_sb_btn_fg,
                bg=self.col_sb_btn_bg,
                activebackground=self.col_sb_btn_active_bg,
                activeforeground=self.col_sb_btn_active_fg,
                font=self.font_sidebar,
                bd=0,
                anchor="w",
                padx=20,
                cursor="hand2",
                command=lambda i=idx: self.switch_view(i),
            )
            btn.pack(fill="x", pady=(24 if idx == MODE_DASHBOARD else 4, 4))
            self.sidebar_buttons[idx] = btn

        # Bottom modes (packed from the bottom)
        for idx, _key, label, pos in reversed(MODES):
            if pos != "bottom":
                continue
            btn = tk.Button(
                sidebar,
                text=label,
                fg=self.col_sb_btn_fg,
                bg=self.col_sb_btn_bg,
                activebackground=self.col_sb_btn_active_bg,
                activeforeground=self.col_sb_btn_active_fg,
                font=self.font_sidebar,
                bd=0,
                anchor="w",
                padx=20,
                cursor="hand2",
                command=lambda i=idx: self.switch_view(i),
            )
            btn.pack(fill="x", side="bottom", pady=(4, 24 if idx == MODE_HELP else 4))
            self.sidebar_buttons[idx] = btn

    def _create_placeholder_view(self, title_text):
        """Create an empty content view with just a title.

        The body of each mode will be implemented in subsequent turns.
        """
        view = ttk.Frame(self.content_frame)

        lbl_title = ttk.Label(view, text=title_text, style="Title.TLabel")
        lbl_title.pack(anchor="w", padx=16, pady=(16, 10))

        # Empty card placeholder (visual anchor for future content)
        card = ttk.Frame(view, style="Card.TFrame")
        card.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        placeholder = ttk.Label(
            card,
            text="To be implemented",
            style="Card.TLabel",
            font=self.font_default,
        )
        placeholder.pack(expand=True)

        return view

    # ------------------------------------------------------------------
    # Space view (MODE_SPACE_WORK)
    # ------------------------------------------------------------------
    def create_space_work_view(self):
        view = ttk.Frame(self.content_frame)

        # Title bar: label on left, combobox on the far right
        title_bar = tk.Frame(view, bg=self.col_window_bg)
        title_bar.pack(fill="x", padx=16, pady=(16, 10))

        lbl_title = ttk.Label(title_bar, text=" \U0001F4CD Space", style="Title.TLabel")
        lbl_title.pack(side="left")

        self.btn_refresh_space_work = ttk.Button(
            title_bar,
            text="Refresh",
            command=self._on_refresh_space_work,
        )
        self.btn_refresh_space_work.pack(side="right")

        self.cb_space_work2 = ttk.Combobox(
            title_bar,
            state="readonly",
            width=30,
        )
        self.cb_space_work2.pack(side="right", padx=(0, 8))

        self.cb_space_work = ttk.Combobox(
            title_bar,
            state="readonly",
            width=30,
        )
        self.cb_space_work.pack(side="right", padx=(0, 8))

        # Empty card placeholder (body to be implemented later)
        card = ttk.Frame(view, style="Card.TFrame")
        card.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        placeholder = ttk.Label(
            card,
            text="To be implemented",
            style="Card.TLabel",
            font=self.font_default,
        )
        placeholder.pack(expand=True)

        return view

    def _on_refresh_space_work(self):
        self.write_log("Refresh Space — not yet implemented.", "info")

    # ------------------------------------------------------------------
    # Config view (MODE_CONFIG)
    # ------------------------------------------------------------------
    def create_config_view(self):
        view = ttk.Frame(self.content_frame)

        card = ttk.Frame(view, style="Card.TFrame")
        card.pack(fill="both", expand=True, padx=16, pady=4)

        container = tk.Frame(card, bg=self.col_card_bg)
        container.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self.config_fields_frame = tk.Frame(container, bg=self.col_card_bg)
        self.config_fields_frame.pack(fill="both", expand=True)

        btn_frm = tk.Frame(container, bg=self.col_card_bg)
        btn_frm.pack(fill="x", side="bottom", pady=(16, 0))

        self.btn_save_config = ttk.Button(
            btn_frm, text="Save Configuration",
            style="Primary.TButton", command=self._save_config,
        )
        self.btn_save_config.pack(side="left")

        self.btn_edit_config = ttk.Button(
            btn_frm, text="Edit", command=self._edit_config_file,
        )
        self.btn_edit_config.pack(side="left", padx=(8, 0))

        self.config_entries = {}

        return view

    def refresh_config_view(self):
        for widget in self.config_fields_frame.winfo_children():
            widget.destroy()
        self.config_entries.clear()

        config_data = self.config_data
        if not config_data:
            return

        display_names = {
            "jira_server_url": "JIRA Server URL",
            "jira_user_email": "JIRA User Email",
            "jira_api_token": "JIRA API Token",
            "jira_reporter": "Reporter",
        }

        keys_order = [
            "jira_server_url",
            "jira_user_email",
            "jira_api_token",
            "jira_reporter",
        ]

        self.config_fields_frame.columnconfigure(0, weight=0)
        self.config_fields_frame.columnconfigure(1, weight=1)

        for row_idx, key in enumerate(keys_order):
            val = config_data.get(key, "")
            display_name = display_names.get(key, key.replace("_", " ").title())

            lbl = ttk.Label(
                self.config_fields_frame,
                text=f"{display_name}:",
                font=self.font_card_title,
                anchor="w",
                background=self.col_card_bg,
            )
            lbl.grid(row=row_idx, column=0, padx=(0, 10), pady=6, sticky="w")

            if key == "jira_api_token":
                ent = ttk.Entry(self.config_fields_frame, show="*")
            else:
                ent = ttk.Entry(self.config_fields_frame)
            ent.insert(0, str(val))
            ent.grid(row=row_idx, column=1, padx=0, pady=6, sticky="ew")
            self.config_entries[key] = ent

    def _save_config(self):
        for key, ent in self.config_entries.items():
            val = ent.get().strip()
            self.config_data[key] = val

        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, indent=4, ensure_ascii=False)
            self.write_log("Configuration saved successfully.", "success")
        except Exception as e:
            self.write_log(f"Failed to save configuration: {e}", "error")

    def _edit_config_file(self):
        if not os.path.exists(self.config_path):
            self.write_log("config.json not found.", "warning")
            return
        try:
            abs_path = os.path.abspath(self.config_path)
            subprocess.Popen(["notepad.exe", abs_path])
            self.write_log("Opened config.json in Notepad.", "info")
        except Exception as e:
            self.write_log(f"Failed to open config.json: {e}", "error")

    # ------------------------------------------------------------------
    # System log area (bottom)
    # ------------------------------------------------------------------
    def _build_log_area(self):
        self.log_container = ttk.Frame(self, style="Card.TFrame")
        self.log_container.pack(fill="x", side="bottom", padx=0, pady=0)

        # Log header: title (left) + status indicator + Clear button (right)
        log_header = tk.Frame(self.log_container, bg=self.col_card_bg)
        log_header.pack(fill="x", padx=12, pady=(6, 2))

        lbl_log_title = ttk.Label(log_header, text="System Log", style="CardTitle.TLabel")
        lbl_log_title.pack(side="left")

        btn_clear_log = tk.Button(
            log_header,
            text="Clear",
            command=self.clear_log,
            font=self.font_default,
            bg=self.col_sb_btn_bg,
            fg=self.col_sb_btn_fg,
            activebackground=self.col_sb_btn_active_bg,
            activeforeground=self.col_sb_btn_active_fg,
            bd=0,
            relief="flat",
            padx=12,
            pady=3,
            cursor="hand2",
        )
        btn_clear_log.pack(side="right")

        # Status indicator: green = Idle, red = Busy
        self.lbl_status_indicator = tk.Label(
            log_header,
            text="\u25CF Idle",
            fg=self.col_log_idle,
            bg=self.col_card_bg,
            font=self.font_default,
        )
        self.lbl_status_indicator.pack(side="right", padx=(0, 15))

        # Log body: text widget + scrollbar
        log_body = tk.Frame(self.log_container, bg=self.col_card_bg)
        log_body.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        self.txt_log = tk.Text(
            log_body,
            height=5,
            bg=self.col_log_bg,
            fg=self.col_log_fg,
            font=self.font_log,
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.col_log_border,
            highlightcolor=self.col_log_focus,
        )
        self.txt_log.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(log_body, orient="vertical", command=self.txt_log.yview)
        scrollbar.pack(side="right", fill="y")
        self.txt_log.config(yscrollcommand=scrollbar.set)
        self.txt_log.config(state="disabled")

        # Configure colored tags for log levels
        self.txt_log.tag_config("timestamp", foreground=self.col_log_ts)
        self.txt_log.tag_config("info", foreground=self.col_log_info)
        self.txt_log.tag_config("success", foreground=self.col_log_success)
        self.txt_log.tag_config("warning", foreground=self.col_log_warning)
        self.txt_log.tag_config("error", foreground=self.col_log_error)

    # ------------------------------------------------------------------
    # View switching
    # ------------------------------------------------------------------
    def switch_view(self, index):
        self.current_view_index = index

        # Update sidebar button highlight states
        for idx, btn in self.sidebar_buttons.items():
            if idx == index:
                btn.config(
                    bg=self.col_sb_sel_bg,
                    fg=self.col_sb_sel_fg,
                    activebackground=self.col_sb_sel_active_bg,
                    activeforeground=self.col_sb_sel_active_fg,
                )
            else:
                btn.config(
                    bg=self.col_sb_btn_bg,
                    fg=self.col_sb_btn_fg,
                    activebackground=self.col_sb_btn_active_bg,
                    activeforeground=self.col_sb_btn_active_fg,
                )

        # Show the selected view, hide the rest
        for idx, view in enumerate(self.views):
            if idx == index:
                view.pack(fill="both", expand=True)
                if idx == MODE_CONFIG:
                    self.refresh_config_view()
            else:
                view.pack_forget()

    # ------------------------------------------------------------------
    # Log helpers
    # ------------------------------------------------------------------
    def write_log(self, message, msg_type="info"):
        """Append a timestamped, color-tagged message to the System Log.

        msg_type: 'info' | 'success' | 'warning' | 'error'
        """
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"[{timestamp}] "

        if msg_type == "success":
            tag_text = "\U0001F7E2 SUCCESS: "
            tag_name = "success"
        elif msg_type == "error":
            tag_text = "\U0001F534 ERROR: "
            tag_name = "error"
        elif msg_type == "warning":
            tag_text = "\u26A0\uFE0F WARNING: "
            tag_name = "warning"
        else:
            tag_text = "\u2139\uFE0F INFO: "
            tag_name = "info"

        self.txt_log.config(state="normal")
        self.txt_log.insert("end", prefix, "timestamp")
        self.txt_log.insert("end", tag_text, tag_name)
        self.txt_log.insert("end", f"{message}\n")
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")

    def clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")

    def set_busy(self, busy):
        """Toggle the status indicator between Idle (green) and Busy (red)."""
        self.is_busy = busy
        if busy:
            self.lbl_status_indicator.config(text="\u25CF Busy", fg=self.col_log_busy)
        else:
            self.lbl_status_indicator.config(text="\u25CF Idle", fg=self.col_log_idle)


if __name__ == "__main__":
    app = JiraWorklogApp()
    app.mainloop()
