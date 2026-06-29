import json
import os
import sys
import time
import tkinter as tk
import ctypes
from datetime import date, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

try:
    import winsound
except ImportError:
    winsound = None

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageTk
except ImportError:
    Image = None
    ImageChops = None
    ImageDraw = None
    ImageFont = None
    ImageTk = None


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "water_reminder_config.json"
APP_USER_MODEL_ID = "SQTQS.DesktopWaterReminder"
BALL_WIDTH = 146
BALL_HEIGHT = 46
BALL_RENDER_SCALE = 4
BALL_STYLE_LABELS = {
    "minimal": "极简版",
    "cute": "可爱版",
    "tech": "科技感版",
}
BALL_LABEL_TO_STYLE = {label: key for key, label in BALL_STYLE_LABELS.items()}
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
SW_HIDE = 0

DEFAULT_CONFIG = {
    "interval_minutes": 45,
    "snooze_minutes": 10,
    "daily_goal_ml": 2000,
    "drink_amount_ml": 250,
    "consumed_ml": 0,
    "history": {},
    "drink_log": [],
    "last_drink_at": 0,
    "next_reminder_at": 0,
    "floating_ball_enabled": True,
    "ball_style": "minimal",
    "autostart_enabled": False,
    "ball_x": 60,
    "ball_y": 120,
    "last_date": "",
    "window_x": 40,
    "window_y": 80,
}

COLORS = {
    "bg": "#f4f7fb",
    "card": "#ffffff",
    "line": "#dbe5ee",
    "text": "#102a3a",
    "muted": "#61717f",
    "soft": "#edf4f8",
    "primary": "#176b87",
    "primary_dark": "#0f536b",
    "green": "#2e8b57",
    "amber": "#b7791f",
    "bar": "#78b7c8",
    "bar_bg": "#e8eef3",
}


class WaterReminder:
    def __init__(self):
        self.set_windows_app_id()
        self.config = self.load_config()
        self.reset_daily_total_if_needed()
        self.initialize_reminder_state()
        self.sync_autostart()
        self.alert_window = None
        self.ball_window = None
        self.last_beep = 0
        self.ball_drag_offset = (0, 0)
        self.ball_flash_on = False
        self.ball_hover = False
        self.ball_image = None

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("喝水提醒")
        self.root.geometry(f"390x560+{self.config['window_x']}+{self.config['window_y']}")
        self.root.minsize(390, 560)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        self.build_ui()
        self.refresh_all()
        self.tick()
        self.root.update_idletasks()
        self.hide_window_from_taskbar(self.root)
        self.root.deiconify()
        self.root.update_idletasks()
        self.hide_window_from_taskbar(self.root)
        self.root.lift()
        self.keep_window_off_taskbar(self.root)

    def default_config(self):
        config = DEFAULT_CONFIG.copy()
        config["history"] = {}
        config["drink_log"] = []
        return config

    def load_config(self):
        if not CONFIG_PATH.exists():
            return self.default_config()

        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self.default_config()

        config = self.default_config()
        config.update({key: data.get(key, value) for key, value in DEFAULT_CONFIG.items()})
        config["interval_minutes"] = self.clamp_int(config["interval_minutes"], 5, 240, 45)
        config["snooze_minutes"] = self.clamp_int(config["snooze_minutes"], 1, 60, 10)
        config["daily_goal_ml"] = self.clamp_int(config["daily_goal_ml"], 300, 8000, 2000)
        config["drink_amount_ml"] = self.clamp_int(config["drink_amount_ml"], 50, 2000, 250)
        config["consumed_ml"] = self.clamp_int(config["consumed_ml"], 0, 20000, 0)
        config["last_drink_at"] = self.normalize_timestamp(config["last_drink_at"])
        config["next_reminder_at"] = self.normalize_timestamp(config["next_reminder_at"])
        config["floating_ball_enabled"] = bool(config["floating_ball_enabled"])
        config["ball_style"] = self.normalize_ball_style(config.get("ball_style", "minimal"))
        config["autostart_enabled"] = bool(config["autostart_enabled"])
        config["ball_x"] = self.clamp_int(config["ball_x"], 0, 8000, 60)
        config["ball_y"] = self.clamp_int(config["ball_y"], 0, 8000, 120)
        config["window_x"] = self.clamp_int(config["window_x"], 0, 8000, 40)
        config["window_y"] = self.clamp_int(config["window_y"], 0, 8000, 80)
        config["last_date"] = config["last_date"] if isinstance(config["last_date"], str) else ""
        config["history"] = self.normalize_history(config.get("history", {}))
        config["drink_log"] = self.normalize_drink_log(config.get("drink_log", []))
        return config

    @staticmethod
    def normalize_ball_style(value):
        if value in BALL_STYLE_LABELS:
            return value
        if value in BALL_LABEL_TO_STYLE:
            return BALL_LABEL_TO_STYLE[value]
        return "minimal"

    @staticmethod
    def clamp_int(value, low, high, fallback):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return fallback
        return max(low, min(high, value))

    @staticmethod
    def normalize_timestamp(value):
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return 0
        if timestamp < 0 or timestamp > 4102444800:
            return 0
        return timestamp

    def normalize_history(self, raw_history):
        history = {}
        if not isinstance(raw_history, dict):
            return history

        for day, amount in raw_history.items():
            if isinstance(day, str) and len(day) == 10:
                history[day] = self.clamp_int(amount, 0, 20000, 0)
        return history

    def normalize_drink_log(self, raw_log):
        if not isinstance(raw_log, list):
            return []

        normalized = []
        for entry in raw_log[-500:]:
            if not isinstance(entry, dict):
                continue

            day = entry.get("date", "")
            timestamp = self.normalize_timestamp(entry.get("time", 0))
            amount = self.clamp_int(entry.get("amount_ml", 0), 1, 5000, 0)
            if isinstance(day, str) and len(day) == 10 and timestamp > 0 and amount > 0:
                normalized.append(
                    {
                        "date": day,
                        "time": timestamp,
                        "amount_ml": amount,
                        "prev_last_drink_at": self.normalize_timestamp(
                            entry.get("prev_last_drink_at", 0)
                        ),
                        "prev_next_reminder_at": self.normalize_timestamp(
                            entry.get("prev_next_reminder_at", 0)
                        ),
                    }
                )
        return normalized

    def save_config(self):
        try:
            CONFIG_PATH.write_text(
                json.dumps(self.config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def startup_file_path(self):
        appdata = os.getenv("APPDATA")
        if not appdata:
            return None

        return (
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
            / "Water Reminder.bat"
        )

    def sync_autostart(self, show_errors=False):
        startup_file = self.startup_file_path()
        if startup_file is None:
            if show_errors:
                messagebox.showwarning("开机自启动", "没有找到当前用户的启动目录。")
            return False

        try:
            if self.config["autostart_enabled"]:
                startup_file.parent.mkdir(parents=True, exist_ok=True)
                launcher = APP_DIR / "start_water_reminder.bat"
                startup_file.write_text(
                    f'@echo off\nstart "" "{launcher}"\n',
                    encoding="utf-8",
                )
            elif startup_file.exists():
                startup_file.unlink()
        except OSError as exc:
            if show_errors:
                messagebox.showwarning("开机自启动", f"无法更新开机自启动设置：{exc}")
            return False

        return True

    def initialize_reminder_state(self):
        now = time.time()
        if self.config["last_drink_at"] <= 0:
            self.config["last_drink_at"] = now

        if self.config["next_reminder_at"] <= 0:
            self.config["next_reminder_at"] = (
                self.config["last_drink_at"] + self.config["interval_minutes"] * 60
            )

        self.save_config()

    def reset_daily_total_if_needed(self):
        today = date.today().isoformat()
        history = self.config["history"]
        last_date = self.config["last_date"]

        if not last_date:
            self.config["last_date"] = today
            self.config["consumed_ml"] = history.get(today, self.config["consumed_ml"])
            history[today] = self.config["consumed_ml"]
            self.save_config()
            return True

        if last_date != today:
            history[last_date] = self.config["consumed_ml"]
            self.config["last_date"] = today
            self.config["consumed_ml"] = history.get(today, 0)
            history[today] = self.config["consumed_ml"]
            self.save_config()
            return True

        history[today] = self.config["consumed_ml"]
        return False

    def build_ui(self):
        self.root.configure(bg=COLORS["bg"])
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=COLORS["bar_bg"],
            background=COLORS["primary"],
            bordercolor=COLORS["bar_bg"],
            lightcolor=COLORS["primary"],
            darkcolor=COLORS["primary"],
        )
        style.configure("TSpinbox", arrowsize=12)

        self.container = tk.Frame(self.root, bg=COLORS["bg"], padx=14, pady=12)
        self.container.pack(fill="both", expand=True)

        self.interval_var = tk.StringVar(value=str(self.config["interval_minutes"]))
        self.goal_var = tk.StringVar(value=str(self.config["daily_goal_ml"]))
        self.amount_var = tk.StringVar(value=str(self.config["drink_amount_ml"]))
        self.floating_ball_var = tk.BooleanVar(value=self.config["floating_ball_enabled"])
        self.ball_style_var = tk.StringVar(value=BALL_STYLE_LABELS[self.config["ball_style"]])
        self.autostart_var = tk.BooleanVar(value=self.config["autostart_enabled"])

        self.build_header()
        self.build_today_card()
        self.build_timer_card()
        self.build_actions()
        self.build_history_card()

    def build_header(self):
        header = tk.Frame(self.container, bg=COLORS["bg"])
        header.pack(fill="x", pady=(0, 10))

        left = tk.Frame(header, bg=COLORS["bg"])
        left.pack(side="left", fill="x", expand=True)

        tk.Label(
            left,
            text="喝水提醒",
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(anchor="w")

        tk.Label(
            left,
            text=date.today().strftime("%Y-%m-%d"),
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(header, bg=COLORS["bg"])
        right.pack(side="right")

        self.status_badge = tk.Label(
            right,
            text="0%",
            bg=COLORS["soft"],
            fg=COLORS["primary"],
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=5,
        )
        self.status_badge.pack(side="left", anchor="e")

        self.small_button(right, "收起", self.minimize_window).pack(side="left", anchor="e", padx=(8, 0))
        self.small_button(right, "设置", self.open_settings_dialog).pack(side="left", anchor="e", padx=(8, 0))

    def build_today_card(self):
        card = self.card(self.container)
        card.pack(fill="x", pady=(0, 10))

        top = tk.Frame(card, bg=COLORS["card"])
        top.pack(fill="x")

        tk.Label(
            top,
            text="今日饮水",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")

        self.goal_label = tk.Label(
            top,
            text="目标 2000 ml",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        self.goal_label.pack(side="right")

        amount_row = tk.Frame(card, bg=COLORS["card"])
        amount_row.pack(fill="x", pady=(4, 6))

        self.consumed_label = tk.Label(
            amount_row,
            text="0",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=("Segoe UI", 26, "bold"),
        )
        self.consumed_label.pack(side="left")

        tk.Label(
            amount_row,
            text="ml",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left", padx=(6, 0), pady=(14, 0))

        self.remaining_label = tk.Label(
            amount_row,
            text="还差 2000 ml",
            bg=COLORS["card"],
            fg=COLORS["green"],
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.remaining_label.pack(side="right", pady=(16, 0))

        self.daily_progress = ttk.Progressbar(card, maximum=100, value=0)
        self.daily_progress.pack(fill="x")

    def build_timer_card(self):
        card = self.card(self.container)
        card.pack(fill="x", pady=(0, 10))

        row = tk.Frame(card, bg=COLORS["card"])
        row.pack(fill="x")

        left = tk.Frame(row, bg=COLORS["card"])
        left.pack(side="left", fill="x", expand=True)

        tk.Label(
            left,
            text="下次提醒",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")

        self.time_label = tk.Label(
            left,
            text="--:--",
            bg=COLORS["card"],
            fg=COLORS["primary_dark"],
            font=("Segoe UI", 24, "bold"),
        )
        self.time_label.pack(anchor="w", pady=(2, 0))

        self.next_time_label = tk.Label(
            row,
            text="--:--",
            bg=COLORS["soft"],
            fg=COLORS["primary_dark"],
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=5,
        )
        self.next_time_label.pack(side="right", anchor="n")

        self.timer_progress = ttk.Progressbar(card, maximum=100, value=100)
        self.timer_progress.pack(fill="x", pady=(8, 0))

    def build_actions(self):
        row = tk.Frame(self.container, bg=COLORS["bg"])
        row.pack(fill="x", pady=(0, 10))

        self.drink_button = self.action_button(
            row,
            "记录饮水",
            COLORS["primary"],
            "#ffffff",
            self.mark_drunk,
        )
        self.drink_button.pack(side="left", fill="x", expand=True)

        self.snooze_button = self.action_button(
            row,
            "稍后提醒",
            "#ffffff",
            COLORS["primary_dark"],
            self.snooze,
            border=COLORS["line"],
        )
        self.snooze_button.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.undo_button = self.action_button(
            row,
            "撤销",
            "#ffffff",
            COLORS["muted"],
            self.undo_last_drink,
            border=COLORS["line"],
        )
        self.undo_button.pack(side="left", fill="x", expand=True, padx=(8, 0))

    def build_history_card(self):
        card = self.card(self.container)
        card.pack(fill="x", pady=(0, 10))

        top = tk.Frame(card, bg=COLORS["card"])
        top.pack(fill="x")

        tk.Label(
            top,
            text="最近 7 天",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")

        self.week_average_label = tk.Label(
            top,
            text="日均 0 ml",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        self.week_average_label.pack(side="right")

        self.history_canvas = tk.Canvas(
            card,
            width=1,
            height=82,
            bg=COLORS["card"],
            highlightthickness=0,
            bd=0,
        )
        self.history_canvas.pack(fill="x", pady=(6, 0))
        self.history_canvas.bind("<Configure>", lambda _event: self.draw_history_chart())

    def open_settings_dialog(self):
        if hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return

        self.interval_var.set(str(self.config["interval_minutes"]))
        self.goal_var.set(str(self.config["daily_goal_ml"]))
        self.amount_var.set(str(self.config["drink_amount_ml"]))
        self.floating_ball_var.set(self.config["floating_ball_enabled"])
        self.ball_style_var.set(BALL_STYLE_LABELS[self.config["ball_style"]])
        self.autostart_var.set(self.config["autostart_enabled"])

        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.title("设置")
        self.settings_window.geometry(self.settings_geometry())
        self.settings_window.resizable(False, False)
        self.settings_window.attributes("-topmost", True)
        self.settings_window.configure(bg=COLORS["bg"])
        self.settings_window.transient(self.root)
        self.hide_window_from_taskbar(self.settings_window)

        panel = self.card(self.settings_window)
        panel.pack(fill="both", expand=True, padx=14, pady=14)

        tk.Label(
            panel,
            text="饮水设置",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(anchor="w")

        tk.Label(
            panel,
            text="设置会保存到本地，下次打开自动沿用。",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(2, 12))

        grid = tk.Frame(panel, bg=COLORS["card"])
        grid.pack(fill="x")

        self.add_setting(grid, 0, "间隔", self.interval_var, "分钟", 5, 240, 5)
        self.add_setting(grid, 1, "目标", self.goal_var, "ml", 300, 8000, 100)
        self.add_setting(grid, 2, "每次", self.amount_var, "ml", 50, 2000, 50)

        style_row = tk.Frame(panel, bg=COLORS["card"])
        style_row.pack(fill="x", pady=(12, 0))

        tk.Label(
            style_row,
            text="悬浮窗样式",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w")

        style_combo = ttk.Combobox(
            style_row,
            textvariable=self.ball_style_var,
            values=list(BALL_STYLE_LABELS.values()),
            state="readonly",
            font=("Microsoft YaHei UI", 9),
        )
        style_combo.pack(fill="x", pady=(3, 0))
        style_combo.bind("<<ComboboxSelected>>", lambda _event: self.update_settings())

        tk.Checkbutton(
            panel,
            text="最小化到悬浮球",
            variable=self.floating_ball_var,
            bg=COLORS["card"],
            fg=COLORS["text"],
            activebackground=COLORS["card"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["card"],
            font=("Microsoft YaHei UI", 9),
            command=self.update_settings,
        ).pack(anchor="w", pady=(12, 0))

        tk.Checkbutton(
            panel,
            text="开机自启动",
            variable=self.autostart_var,
            bg=COLORS["card"],
            fg=COLORS["text"],
            activebackground=COLORS["card"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["card"],
            font=("Microsoft YaHei UI", 9),
            command=self.update_settings,
        ).pack(anchor="w", pady=(6, 0))

        buttons = tk.Frame(panel, bg=COLORS["card"])
        buttons.pack(fill="x", pady=(18, 0))

        self.action_button(
            buttons,
            "保存",
            COLORS["primary"],
            "#ffffff",
            self.update_settings,
        ).pack(side="left", fill="x", expand=True)

        self.small_button(buttons, "今日清零", self.reset_today).pack(side="left", padx=(8, 0))
        self.small_button(buttons, "关闭", self.settings_window.destroy).pack(side="left", padx=(8, 0))

    def settings_geometry(self):
        self.root.update_idletasks()
        x = self.root.winfo_x() + 20
        y = self.root.winfo_y() + 90
        return f"360x368+{x}+{y}"

    def build_settings_card(self):
        card = self.card(self.container)
        card.pack(fill="x")

        tk.Label(
            card,
            text="设置",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")

        self.interval_var = tk.StringVar(value=str(self.config["interval_minutes"]))
        self.goal_var = tk.StringVar(value=str(self.config["daily_goal_ml"]))
        self.amount_var = tk.StringVar(value=str(self.config["drink_amount_ml"]))

        settings_grid = tk.Frame(card, bg=COLORS["card"])
        settings_grid.pack(fill="x", pady=(8, 0))

        self.add_setting(settings_grid, 0, "间隔", self.interval_var, "分钟", 5, 240, 5)
        self.add_setting(settings_grid, 1, "目标", self.goal_var, "ml", 300, 8000, 100)
        self.add_setting(settings_grid, 2, "每次", self.amount_var, "ml", 50, 2000, 50)

        bottom = tk.Frame(card, bg=COLORS["card"])
        bottom.pack(fill="x", pady=(10, 0))

        self.save_button = self.small_button(bottom, "保存设置", self.update_settings)
        self.save_button.pack(side="left")

        self.reset_button = self.small_button(bottom, "今日清零", self.reset_today)
        self.reset_button.pack(side="right")

    def card(self, parent):
        frame = tk.Frame(
            parent,
            bg=COLORS["card"],
            padx=12,
            pady=8,
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            highlightcolor=COLORS["line"],
        )
        return frame

    def action_button(self, parent, text, bg, fg, command, border=None):
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            relief="flat" if border is None else "solid",
            bd=0 if border is None else 1,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=10,
            pady=9,
            cursor="hand2",
        )
        return button

    def small_button(self, parent, text, command):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=COLORS["soft"],
            fg=COLORS["primary_dark"],
            activebackground=COLORS["soft"],
            activeforeground=COLORS["primary_dark"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=10,
            pady=5,
            cursor="hand2",
        )

    def add_setting(self, parent, column, label, variable, unit, from_, to, increment):
        item = tk.Frame(parent, bg=COLORS["card"])
        item.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        parent.columnconfigure(column, weight=1, uniform="settings")

        tk.Label(
            item,
            text=label,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w")

        input_row = tk.Frame(item, bg=COLORS["card"])
        input_row.pack(fill="x", pady=(3, 0))

        spin = ttk.Spinbox(
            input_row,
            from_=from_,
            to=to,
            increment=increment,
            width=5,
            textvariable=variable,
            command=self.update_settings,
            font=("Segoe UI", 9),
        )
        spin.pack(side="left", fill="x", expand=True)
        spin.bind("<Return>", lambda _event: self.update_settings())
        spin.bind("<FocusOut>", lambda _event: self.update_settings())

        tk.Label(
            input_row,
            text=unit,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(4, 0))

    def minimize_window(self):
        if self.config["floating_ball_enabled"]:
            self.remember_window_position()
            self.hide_main_window_for_floating()
            self.show_floating_ball()
            return

        self.hide_floating_ball()
        self.root.iconify()

    def hide_main_window_for_floating(self):
        self.root.withdraw()
        self.root.update_idletasks()
        self.force_hide_window_from_taskbar(self.root)
        for delay in (50, 250, 1000):
            try:
                self.root.after(delay, lambda: self.force_hide_window_from_taskbar(self.root))
            except tk.TclError:
                return

    def show_floating_ball(self):
        if self.ball_window and self.ball_window.winfo_exists():
            x, y = self.clamp_ball_position(self.config["ball_x"], self.config["ball_y"])
            self.config["ball_x"] = x
            self.config["ball_y"] = y
            self.ball_window.withdraw()
            self.ball_window.geometry(f"{BALL_WIDTH}x{BALL_HEIGHT}+{x}+{y}")
            self.ball_window.update_idletasks()
            self.keep_window_off_taskbar(self.ball_window)
            self.ball_window.deiconify()
            self.ball_window.lift()
            self.ball_window.attributes("-topmost", True)
            self.update_floating_ball()
            self.ball_window.update_idletasks()
            self.keep_window_off_taskbar(self.ball_window)
            self.apply_capsule_window_shape(self.ball_window, BALL_WIDTH, BALL_HEIGHT)
            return

        ball_bg = COLORS["card"]
        x, y = self.clamp_ball_position(self.config["ball_x"], self.config["ball_y"])
        self.config["ball_x"] = x
        self.config["ball_y"] = y

        self.ball_window = tk.Toplevel(self.root)
        self.ball_window.withdraw()
        self.ball_window.overrideredirect(True)
        self.ball_window.attributes("-topmost", True)
        self.ball_window.configure(bg=ball_bg)
        self.ball_window.geometry(f"{BALL_WIDTH}x{BALL_HEIGHT}+{x}+{y}")
        self.ball_window.update_idletasks()
        self.keep_window_off_taskbar(self.ball_window)
        self.ball_window.deiconify()
        self.ball_window.lift()
        self.apply_capsule_window_shape(self.ball_window, BALL_WIDTH, BALL_HEIGHT)

        try:
            self.ball_window.attributes("-alpha", 0.98)
        except tk.TclError:
            pass

        self.ball_canvas = tk.Canvas(
            self.ball_window,
            width=BALL_WIDTH,
            height=BALL_HEIGHT,
            bg=COLORS["card"],
            highlightthickness=0,
            bd=0,
        )
        self.ball_canvas.pack(fill="both", expand=True)
        self.ball_canvas.bind("<ButtonPress-1>", self.start_ball_drag)
        self.ball_canvas.bind("<B1-Motion>", self.drag_floating_ball)
        self.ball_canvas.bind("<ButtonRelease-1>", self.save_ball_position)
        self.ball_canvas.bind("<Double-Button-1>", lambda _event: self.restore_main_window())
        self.ball_canvas.bind("<Button-3>", lambda _event: self.mark_drunk())
        self.ball_canvas.bind("<Enter>", self.on_ball_enter)
        self.ball_canvas.bind("<Leave>", self.on_ball_leave)
        self.update_floating_ball()

    def update_floating_ball(self):
        if not self.ball_window or not self.ball_window.winfo_exists():
            return

        consumed = self.config["consumed_ml"]
        goal = max(1, self.config["daily_goal_ml"])
        percent = min(100, round(consumed / goal * 100))
        daily_ratio = percent / 100
        remaining = max(0, int(self.config["next_reminder_at"] - time.time()))
        total = max(1, self.config["interval_minutes"] * 60)
        elapsed_ratio = max(0, min(1, 1 - remaining / total))
        ball_style = self.normalize_ball_style(self.config.get("ball_style", "minimal"))

        if remaining <= 0:
            self.ball_flash_on = not self.ball_flash_on
            fill = "#d93636" if self.ball_flash_on else "#fff4f4"
            outline = "#b91c1c"
            accent = "#ffffff" if self.ball_flash_on else "#e03f55"
            main_text = "喝水"
            sub_text = f"今日 {percent}%"
            main_fill = "#ffffff" if self.ball_flash_on else "#9b1c1c"
            sub_fill = "#ffe2e2" if self.ball_flash_on else "#9b1c1c"
            state = "due"
        elif remaining <= 300:
            self.ball_flash_on = False
            fill = "#fffaf0"
            outline = "#f0d899"
            accent = "#f6b84b"
            main_text = self.format_ball_countdown(remaining)
            sub_text = f"今日 {percent}%"
            main_fill = "#7a4a06"
            sub_fill = "#9b640d"
            state = "soon"
        else:
            self.ball_flash_on = False
            fill = "#fbfdff"
            outline = "#d7e8f0"
            accent = "#39aee2"
            main_text = self.format_ball_countdown(remaining)
            sub_text = f"今日 {percent}%"
            main_fill = COLORS["text"]
            sub_fill = "#6a7f8d"
            state = "normal"

        if ball_style == "cute":
            if remaining > 0:
                minutes = max(1, (remaining + 59) // 60)
                main_text = f"{minutes} min"
            sub_text = f"下次喝水 · 今日{percent}%"

        canvas = self.ball_canvas
        canvas.delete("all")
        if Image is not None:
            self.render_smooth_floating_ball(
                ball_style,
                fill,
                outline,
                accent,
                main_text,
                sub_text,
                main_fill,
                sub_fill,
                elapsed_ratio,
                daily_ratio,
                state,
            )
            return

        canvas.configure(bg=fill)
        radius = BALL_HEIGHT // 2
        self.rounded_rect(canvas, 0, 0, BALL_WIDTH, BALL_HEIGHT, radius, fill=fill, outline=outline)
        canvas.create_oval(9, 9, 31, 33, fill="#e9f8ff", outline="#bde8f7")
        progress_width = int((BALL_WIDTH - 46) * daily_ratio)
        canvas.create_rectangle(35, BALL_HEIGHT - 7, BALL_WIDTH - 14, BALL_HEIGHT - 5, fill="#e8f4f9", outline="#e8f4f9")
        canvas.create_rectangle(35, BALL_HEIGHT - 7, 35 + progress_width, BALL_HEIGHT - 5, fill=accent, outline=accent)
        canvas.create_text(38, 18, text=main_text, fill=main_fill, font=("Segoe UI", 13, "bold"), anchor="w")
        canvas.create_text(38, 33, text=sub_text, fill=sub_fill, font=("Microsoft YaHei UI", 7, "bold"), anchor="w")

    def render_smooth_floating_ball(
        self,
        ball_style,
        fill,
        outline,
        accent,
        main_text,
        sub_text,
        main_fill,
        sub_fill,
        elapsed_ratio,
        daily_ratio,
        state,
    ):
        scale = BALL_RENDER_SCALE
        image = Image.new("RGB", (BALL_WIDTH * scale, BALL_HEIGHT * scale), fill)
        draw = ImageDraw.Draw(image)

        if ball_style == "cute":
            self.draw_cute_ball(image, draw, fill, outline, accent, main_text, sub_text, main_fill, sub_fill, daily_ratio, state)
        elif ball_style == "tech":
            self.draw_tech_ball(image, draw, fill, outline, accent, main_text, sub_text, main_fill, sub_fill, daily_ratio, elapsed_ratio, state)
        else:
            self.draw_minimal_ball(image, draw, fill, outline, accent, main_text, sub_text, main_fill, sub_fill, daily_ratio, state)

        resample = getattr(Image, "Resampling", Image).LANCZOS
        image = image.resize((BALL_WIDTH, BALL_HEIGHT), resample)
        self.ball_image = ImageTk.PhotoImage(image)
        self.ball_canvas.create_image(0, 0, image=self.ball_image, anchor="nw")

    @staticmethod
    def ball_box(*values):
        scale = BALL_RENDER_SCALE
        return tuple(int(round(value * scale)) for value in values)

    def draw_capsule_shell(self, draw, fill, outline, shadow, highlight):
        draw.rounded_rectangle(
            self.ball_box(2, 3, BALL_WIDTH - 2, BALL_HEIGHT - 1),
            radius=(BALL_HEIGHT // 2 - 1) * BALL_RENDER_SCALE,
            fill=shadow,
        )
        draw.rounded_rectangle(
            self.ball_box(1, 1, BALL_WIDTH - 3, BALL_HEIGHT - 4),
            radius=(BALL_HEIGHT // 2 - 2) * BALL_RENDER_SCALE,
            fill=fill,
            outline=outline,
            width=BALL_RENDER_SCALE,
        )
        draw.rounded_rectangle(
            self.ball_box(6, 5, BALL_WIDTH - 10, 18),
            radius=9 * BALL_RENDER_SCALE,
            fill=highlight,
        )

    def draw_minimal_ball(self, image, draw, fill, outline, accent, main_text, sub_text, main_fill, sub_fill, daily_ratio, state):
        shadow = "#edf5f8" if state != "due" else "#f7d4d6"
        highlight = "#ffffff" if state != "due" else "#fffafa"
        track = "#e7f3f8" if state != "due" else "#ffe1e1"
        water = "#58c7f0" if state != "due" else accent
        self.draw_capsule_shell(draw, fill, outline, shadow, highlight)

        x1, y1, x2, y2 = 13, 10, 22, 34
        draw.rounded_rectangle(self.ball_box(x1, y1, x2, y2), radius=4 * BALL_RENDER_SCALE, fill=track)
        fill_top = y2 - max(2, (y2 - y1) * daily_ratio)
        draw.rounded_rectangle(self.ball_box(x1, fill_top, x2, y2), radius=4 * BALL_RENDER_SCALE, fill=water)
        draw.ellipse(self.ball_box(16, 8, 25, 17), fill="#f5fbff", outline="#bfe7f7", width=BALL_RENDER_SCALE)

        self.draw_ball_text(draw, main_text, 33, 18, main_fill, 15, 80)
        self.draw_ball_text(draw, sub_text, 33, 33, sub_fill, 8, 92)
        self.draw_bottom_progress(draw, daily_ratio, 34, 40, BALL_WIDTH - 15, water, track)

    def draw_cute_ball(self, image, draw, fill, outline, accent, main_text, sub_text, main_fill, sub_fill, daily_ratio, state):
        shell_fill = "#fcfeff" if state != "due" else fill
        shadow = "#e8f6fb" if state != "due" else "#f7d4d6"
        highlight = "#ffffff" if state != "due" else "#fffafa"
        water = "#52c5ed" if state != "due" else accent
        track = "#e8f5fb" if state != "due" else "#ffe1e1"
        self.draw_capsule_shell(draw, shell_fill, outline, shadow, highlight)

        draw.ellipse(self.ball_box(9, 8, 37, 36), fill="#eaf9ff" if state != "due" else "#fff0f0", outline="#c2edf9" if state != "due" else outline, width=BALL_RENDER_SCALE)
        self.draw_drop_icon(image, draw, 23, 23, 16, 23, "#d9f4ff", water, "#7bd3f1", daily_ratio)
        draw.ellipse(self.ball_box(31, 12, 35, 16), fill="#ffffff")

        self.draw_ball_text(draw, main_text, 44, 18, main_fill, 14, 60)
        self.draw_ball_text(draw, sub_text, 44, 33, sub_fill, 7, 91)
        self.draw_bottom_progress(draw, daily_ratio, 44, 40, BALL_WIDTH - 14, water, track)

    def draw_tech_ball(self, image, draw, fill, outline, accent, main_text, sub_text, main_fill, sub_fill, daily_ratio, elapsed_ratio, state):
        shell_fill = "#f8fdff" if state != "due" else fill
        shadow = "#e7f3f6" if state != "due" else "#f7d4d6"
        highlight = "#ffffff" if state != "due" else "#fffafa"
        water = "#24b7e8" if state != "due" else accent
        track = "#e3f3f8" if state != "due" else "#ffe1e1"
        self.draw_capsule_shell(draw, shell_fill, outline, shadow, highlight)

        draw.rounded_rectangle(self.ball_box(12, 9, 20, 35), radius=4 * BALL_RENDER_SCALE, fill=track, outline="#bfe7f4", width=BALL_RENDER_SCALE)
        fill_top = 35 - max(2, 26 * daily_ratio)
        draw.rounded_rectangle(self.ball_box(12, fill_top, 20, 35), radius=4 * BALL_RENDER_SCALE, fill=water)
        for y in (14, 20, 26, 32):
            draw.line(self.ball_box(22, y, 25, y), fill="#abddeb", width=BALL_RENDER_SCALE)
        pulse_x = 112 + 16 * elapsed_ratio
        draw.ellipse(self.ball_box(pulse_x, 12, pulse_x + 3, 15), fill=water)
        draw.line(self.ball_box(109, 14, 135, 14), fill="#d5edf5", width=BALL_RENDER_SCALE)

        self.draw_ball_text(draw, main_text, 31, 18, main_fill, 14, 76, "seguisb.ttf")
        self.draw_ball_text(draw, sub_text, 31, 33, sub_fill, 8, 76)
        self.draw_ball_text(draw, "H2O", 111, 31, "#52b8d7" if state != "due" else sub_fill, 6, 25, "seguisb.ttf")
        self.draw_bottom_progress(draw, daily_ratio, 31, 40, BALL_WIDTH - 16, water, track)

    def draw_drop_icon(self, image, draw, cx, cy, width, height, fill, water_fill, outline, daily_ratio):
        mask = Image.new("L", image.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        points = [
            (cx, cy - height * 0.48),
            (cx + width * 0.44, cy - height * 0.06),
            (cx + width * 0.34, cy + height * 0.33),
            (cx, cy + height * 0.50),
            (cx - width * 0.34, cy + height * 0.33),
            (cx - width * 0.44, cy - height * 0.06),
        ]
        scaled_points = [(int(round(x * BALL_RENDER_SCALE)), int(round(y * BALL_RENDER_SCALE))) for x, y in points]
        mask_draw.polygon(scaled_points, fill=255)
        mask_draw.ellipse(self.ball_box(cx - width * 0.36, cy - height * 0.02, cx + width * 0.36, cy + height * 0.52), fill=255)
        image.paste(fill, mask=mask)

        if ImageChops is not None and daily_ratio > 0:
            water_mask = Image.new("L", image.size, 0)
            water_draw = ImageDraw.Draw(water_mask)
            fill_top = cy + height * 0.52 - height * daily_ratio
            water_draw.rectangle(self.ball_box(cx - width, fill_top, cx + width, cy + height), fill=255)
            water_mask = ImageChops.multiply(mask, water_mask)
            image.paste(water_fill, mask=water_mask)

        draw.line(scaled_points + [scaled_points[0]], fill=outline, width=BALL_RENDER_SCALE)
        draw.ellipse(self.ball_box(cx - width * 0.18, cy - height * 0.20, cx - width * 0.01, cy - height * 0.03), fill="#ffffff")

    def draw_bottom_progress(self, draw, ratio, x1, y, x2, fill, track):
        draw.rounded_rectangle(self.ball_box(x1, y, x2, y + 3), radius=1.5 * BALL_RENDER_SCALE, fill=track)
        if ratio <= 0:
            return
        width = max(3, (x2 - x1) * min(1, ratio))
        draw.rounded_rectangle(self.ball_box(x1, y, x1 + width, y + 3), radius=1.5 * BALL_RENDER_SCALE, fill=fill)

    def draw_ball_text(self, draw, text, x, y, fill, size, max_width, font_file=None):
        if font_file is None:
            font_file = "seguisb.ttf" if text.isascii() else "msyh.ttc"
        font = self.load_fitted_ball_font(font_file, text, size, max_width, draw)
        draw.text((x * BALL_RENDER_SCALE, y * BALL_RENDER_SCALE), text, fill=fill, font=font, anchor="lm")

    def load_fitted_ball_font(self, filename, text, size, max_width, draw):
        for current_size in range(size, 6, -1):
            font = self.load_ball_font(filename, current_size * BALL_RENDER_SCALE)
            try:
                bbox = draw.textbbox((0, 0), text, font=font)
            except UnicodeEncodeError:
                if filename != "msyh.ttc":
                    return self.load_fitted_ball_font("msyh.ttc", text, current_size, max_width, draw)
                raise
            if bbox[2] - bbox[0] <= max_width * BALL_RENDER_SCALE:
                return font
        return self.load_ball_font(filename, 7 * BALL_RENDER_SCALE)


    def clamp_ball_position(self, x, y):
        left, top, width, height = self.virtual_screen_bounds()
        max_x = left + max(0, width - BALL_WIDTH)
        max_y = top + max(0, height - BALL_HEIGHT)
        x = self.clamp_int(x, left, max_x, left + 60)
        y = self.clamp_int(y, top, max_y, top + 120)
        return x, y

    def virtual_screen_bounds(self):
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                left = user32.GetSystemMetrics(76)
                top = user32.GetSystemMetrics(77)
                width = user32.GetSystemMetrics(78)
                height = user32.GetSystemMetrics(79)
                if width > 0 and height > 0:
                    return left, top, width, height
            except (AttributeError, OSError):
                pass

        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()


    @staticmethod
    def set_windows_app_id():
        if sys.platform != "win32":
            return

        try:
            shell32 = ctypes.windll.shell32
            shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
            shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
            shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except (AttributeError, OSError, TypeError, ValueError):
            pass

    @staticmethod
    def native_window_handle(window):
        window.update_idletasks()
        try:
            frame = window.tk.call("wm", "frame", window._w)
            if frame:
                return ctypes.c_void_p(int(frame, 0))
        except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
            pass

        try:
            hwnd = ctypes.c_void_p(window.winfo_id())
            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = [ctypes.c_void_p]
            user32.GetParent.restype = ctypes.c_void_p
            parent = user32.GetParent(hwnd)
            return ctypes.c_void_p(parent or hwnd.value)
        except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
            return None

    @staticmethod
    def hide_window_from_taskbar(window):
        if sys.platform != "win32":
            return

        try:
            window.attributes("-toolwindow", True)
        except tk.TclError:
            pass

        try:
            hwnd = WaterReminder.native_window_handle(window)
            if not hwnd or not hwnd.value:
                return

            user32 = ctypes.windll.user32
            user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            user32.SetWindowPos.restype = ctypes.c_bool

            style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
            user32.SetWindowPos(
                hwnd,
                None,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
            )
        except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
            pass

    @staticmethod
    def force_hide_window_from_taskbar(window):
        WaterReminder.hide_window_from_taskbar(window)
        if sys.platform != "win32":
            return

        try:
            hwnd = WaterReminder.native_window_handle(window)
            if not hwnd or not hwnd.value:
                return

            user32 = ctypes.windll.user32
            user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.ShowWindow.restype = ctypes.c_bool
            user32.ShowWindow(hwnd, SW_HIDE)
        except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
            pass

    @staticmethod
    def keep_window_off_taskbar(window):
        WaterReminder.hide_window_from_taskbar(window)
        for delay in (50, 250, 1000):
            try:
                window.after(delay, lambda target=window: WaterReminder.hide_window_from_taskbar(target))
            except tk.TclError:
                return

    @staticmethod
    def apply_capsule_window_shape(window, width, height):
        if sys.platform != "win32":
            return

        try:
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            gdi32.CreateRoundRectRgn.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
            ]
            gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
            user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
            user32.SetWindowRgn.restype = ctypes.c_int

            hwnd = ctypes.c_void_p(window.winfo_id())
            region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, height, height)
            if region:
                user32.SetWindowRgn(hwnd, region, True)
        except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
            pass

    @staticmethod
    def load_ball_font(filename, size):
        font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / filename
        try:
            return ImageFont.truetype(str(font_path), size)
        except OSError:
            return ImageFont.load_default()

    def format_ball_countdown(self, remaining):
        hours, remainder = divmod(remaining, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def rounded_rect(self, canvas, x1, y1, x2, y2, radius, **kwargs):
        canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, **kwargs)
        canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, **kwargs)
        canvas.create_oval(x1, y1, x1 + radius * 2, y1 + radius * 2, **kwargs)
        canvas.create_oval(x2 - radius * 2, y1, x2, y1 + radius * 2, **kwargs)
        canvas.create_oval(x1, y2 - radius * 2, x1 + radius * 2, y2, **kwargs)
        canvas.create_oval(x2 - radius * 2, y2 - radius * 2, x2, y2, **kwargs)

    def on_ball_enter(self, _event):
        self.ball_hover = True
        self.update_floating_ball()

    def on_ball_leave(self, _event):
        self.ball_hover = False
        self.ball_image = None
        self.update_floating_ball()

    def start_ball_drag(self, event):
        self.ball_drag_offset = (event.x, event.y)

    def drag_floating_ball(self, event):
        if not self.ball_window or not self.ball_window.winfo_exists():
            return

        offset_x, offset_y = self.ball_drag_offset
        x = self.ball_window.winfo_x() + event.x - offset_x
        y = self.ball_window.winfo_y() + event.y - offset_y
        self.ball_window.geometry(f"+{max(0, x)}+{max(0, y)}")

    def save_ball_position(self, _event=None):
        if not self.ball_window or not self.ball_window.winfo_exists():
            return

        self.config["ball_x"] = self.ball_window.winfo_x()
        self.config["ball_y"] = self.ball_window.winfo_y()
        self.save_config()

    def floating_ball_is_visible(self):
        return (
            self.ball_window is not None
            and self.ball_window.winfo_exists()
            and self.ball_window.winfo_viewable()
        )

    def restore_main_window(self):
        self.root.deiconify()
        self.root.update_idletasks()
        self.keep_window_off_taskbar(self.root)
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.hide_floating_ball()

    def hide_floating_ball(self):
        if self.ball_window and self.ball_window.winfo_exists():
            self.save_ball_position()
            self.ball_window.withdraw()

    def tick(self):
        if self.reset_daily_total_if_needed():
            self.refresh_all()

        remaining = max(0, int(self.config["next_reminder_at"] - time.time()))
        minutes, seconds = divmod(remaining, 60)
        self.time_label.config(text=f"{minutes:02d}:{seconds:02d}")

        total = max(1, self.config["interval_minutes"] * 60)
        self.timer_progress["value"] = max(0, min(100, remaining / total * 100))
        self.next_time_label.config(text=self.format_reminder_time())
        self.update_floating_ball()

        if remaining <= 0:
            self.remind()

        self.root.after(1000, self.tick)

    def refresh_all(self):
        consumed = self.config["consumed_ml"]
        goal = self.config["daily_goal_ml"]
        amount = self.config["drink_amount_ml"]
        percent = min(100, round(consumed / max(goal, 1) * 100))
        remaining = max(0, goal - consumed)

        self.consumed_label.config(text=str(consumed))
        self.goal_label.config(text=f"目标 {goal} ml")
        self.remaining_label.config(text="已达成" if remaining == 0 else f"还差 {remaining} ml")
        self.remaining_label.config(fg=COLORS["green"] if remaining == 0 else COLORS["amber"])
        self.daily_progress["value"] = percent
        self.status_badge.config(text=f"{percent}%")
        self.drink_button.config(text=f"记录 {amount} ml")
        self.snooze_button.config(text=f"稍后 {self.config['snooze_minutes']} 分钟")
        self.undo_button.config(
            text=self.undo_button_text(),
            state="normal" if self.can_undo_today() else "disabled",
        )
        self.next_time_label.config(text=self.format_reminder_time())
        self.refresh_history_summary()
        self.draw_history_chart()

    def undo_button_text(self):
        entry = self.last_today_log_entry()
        if not entry:
            if self.config["consumed_ml"] > 0:
                amount = min(self.config["drink_amount_ml"], self.config["consumed_ml"])
                return f"撤销 {amount} ml"
            return "撤销"
        return f"撤销 {entry['amount_ml']} ml"

    def can_undo_today(self):
        return self.last_today_log_entry() is not None or self.config["consumed_ml"] > 0

    def last_today_log_entry(self):
        today = date.today().isoformat()
        for entry in reversed(self.config["drink_log"]):
            if entry.get("date") == today:
                return entry
        return None

    def refresh_history_summary(self):
        today = date.today()
        values = [
            self.config["history"].get((today - timedelta(days=offset)).isoformat(), 0)
            for offset in range(6, -1, -1)
        ]
        average = round(sum(values) / 7)
        self.week_average_label.config(text=f"日均 {average} ml")

    def draw_history_chart(self):
        if not hasattr(self, "history_canvas"):
            return

        canvas = self.history_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 320)
        height = 82
        chart_top = 9
        chart_bottom = 54
        label_y = 70
        goal = max(1, self.config["daily_goal_ml"])
        today = date.today()
        days = [today - timedelta(days=6 - index) for index in range(7)]
        values = [self.config["history"].get(day.isoformat(), 0) for day in days]
        max_value = max(goal, max(values, default=0), 1)
        slot = width / 7
        bar_width = min(24, slot * 0.46)

        for index, (day, value) in enumerate(zip(days, values)):
            center = slot * index + slot / 2
            ratio = value / max_value
            bar_height = max(3, int((chart_bottom - chart_top) * ratio)) if value else 3
            x1 = center - bar_width / 2
            x2 = center + bar_width / 2
            y1 = chart_bottom - bar_height
            y2 = chart_bottom
            color = COLORS["green"] if value >= goal else COLORS["bar"]

            canvas.create_rectangle(x1, chart_top, x2, chart_bottom, fill=COLORS["bar_bg"], width=0)
            canvas.create_rectangle(x1, y1, x2, y2, fill=color, width=0)
            canvas.create_text(center, label_y, text=day.strftime("%m/%d"), fill=COLORS["muted"], font=("Segoe UI", 8))
            canvas.create_text(center, y1 - 7, text=str(value), fill=COLORS["text"], font=("Segoe UI", 8, "bold"))

    def format_reminder_time(self):
        return time.strftime("%H:%M", time.localtime(self.config["next_reminder_at"]))

    def remind(self):
        if self.alert_window and self.alert_window.winfo_exists():
            return

        now = time.monotonic()
        if now - self.last_beep > 60:
            self.beep()
            self.last_beep = now

        if self.floating_ball_is_visible():
            self.hide_main_window_for_floating()
        else:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.keep_window_off_taskbar(self.root)

        amount = self.config["drink_amount_ml"]
        consumed = self.config["consumed_ml"]
        goal = self.config["daily_goal_ml"]

        self.alert_window = tk.Toplevel(self.root)
        self.alert_window.title("该喝水了")
        self.alert_window.geometry(self.alert_geometry())
        self.alert_window.resizable(False, False)
        self.alert_window.attributes("-topmost", True)
        self.alert_window.configure(bg=COLORS["card"])
        self.alert_window.transient(self.root)
        self.keep_window_off_taskbar(self.alert_window)
        self.alert_window.protocol("WM_DELETE_WINDOW", self.snooze)

        frame = tk.Frame(self.alert_window, bg=COLORS["card"], padx=18, pady=14)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame,
            text="该喝水了",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 17, "bold"),
        ).pack(anchor="w")

        tk.Label(
            frame,
            text=f"建议喝 {amount} ml。今日已喝 {consumed} / {goal} ml。",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(5, 14))

        actions = tk.Frame(frame, bg=COLORS["card"])
        actions.pack(fill="x")

        self.action_button(actions, f"已喝 {amount} ml", COLORS["primary"], "#ffffff", self.mark_drunk).pack(
            side="left", fill="x", expand=True
        )
        self.action_button(
            actions,
            f"{self.config['snooze_minutes']} 分钟后",
            COLORS["soft"],
            COLORS["primary_dark"],
            self.snooze,
        ).pack(side="left", fill="x", expand=True, padx=(8, 0))

    def alert_geometry(self):
        self.root.update_idletasks()
        if self.floating_ball_is_visible():
            x = self.ball_window.winfo_x() + 8
            y = self.ball_window.winfo_y() + BALL_HEIGHT + 8
        else:
            x = self.root.winfo_x() + 18
            y = self.root.winfo_y() + 80
        return f"350x150+{x}+{y}"

    def beep(self):
        if winsound is None:
            self.root.bell()
            return
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except RuntimeError:
            self.root.bell()

    def mark_drunk(self):
        self.reset_daily_total_if_needed()
        self.close_alert()
        now = time.time()
        today = date.today().isoformat()
        amount = self.config["drink_amount_ml"]
        previous_last_drink_at = self.config["last_drink_at"]
        previous_next_reminder_at = self.config["next_reminder_at"]
        self.config["consumed_ml"] += amount
        self.config["history"][today] = self.config["consumed_ml"]
        self.config["drink_log"].append(
            {
                "date": today,
                "time": now,
                "amount_ml": amount,
                "prev_last_drink_at": previous_last_drink_at,
                "prev_next_reminder_at": previous_next_reminder_at,
            }
        )
        self.config["last_date"] = today
        self.config["last_drink_at"] = now
        self.config["next_reminder_at"] = now + self.config["interval_minutes"] * 60
        self.last_beep = 0
        self.save_config()
        self.refresh_all()

    def undo_last_drink(self):
        entry = self.last_today_log_entry()
        if not entry:
            self.undo_legacy_drink()
            return

        self.config["drink_log"].remove(entry)
        today = date.today().isoformat()
        amount = entry["amount_ml"]
        self.config["consumed_ml"] = max(0, self.config["consumed_ml"] - amount)
        self.config["history"][today] = self.config["consumed_ml"]
        self.config["last_date"] = today

        if entry.get("prev_last_drink_at") and entry.get("prev_next_reminder_at"):
            self.config["last_drink_at"] = entry["prev_last_drink_at"]
            self.config["next_reminder_at"] = entry["prev_next_reminder_at"]
        else:
            previous = self.last_today_log_entry()
            now = time.time()
            if previous:
                self.config["last_drink_at"] = previous["time"]
                self.config["next_reminder_at"] = previous["time"] + self.config["interval_minutes"] * 60
            else:
                self.config["last_drink_at"] = now
                self.config["next_reminder_at"] = now + self.config["interval_minutes"] * 60

        self.last_beep = 0
        self.save_config()
        self.refresh_all()

    def undo_legacy_drink(self):
        if self.config["consumed_ml"] <= 0:
            return

        today = date.today().isoformat()
        amount = min(self.config["drink_amount_ml"], self.config["consumed_ml"])
        self.config["consumed_ml"] = max(0, self.config["consumed_ml"] - amount)
        self.config["history"][today] = self.config["consumed_ml"]
        self.config["last_date"] = today
        self.save_config()
        self.refresh_all()

    def snooze(self):
        self.close_alert()
        self.config["next_reminder_at"] = time.time() + self.config["snooze_minutes"] * 60
        self.last_beep = 0
        self.save_config()
        self.refresh_all()

    def reset_today(self):
        today = date.today().isoformat()
        self.config["consumed_ml"] = 0
        self.config["last_date"] = today
        self.config["history"][today] = 0
        self.config["drink_log"] = [
            entry for entry in self.config["drink_log"] if entry.get("date") != today
        ]
        self.save_config()
        self.refresh_all()

    def close_alert(self):
        if self.alert_window and self.alert_window.winfo_exists():
            self.alert_window.destroy()
        self.alert_window = None

    def update_settings(self):
        interval = self.clamp_int(self.interval_var.get(), 5, 240, self.config["interval_minutes"])
        goal = self.clamp_int(self.goal_var.get(), 300, 8000, self.config["daily_goal_ml"])
        amount = self.clamp_int(self.amount_var.get(), 50, 2000, self.config["drink_amount_ml"])
        floating_ball_enabled = bool(self.floating_ball_var.get())
        ball_style = self.normalize_ball_style(
            BALL_LABEL_TO_STYLE.get(self.ball_style_var.get(), self.ball_style_var.get())
        )
        autostart_enabled = bool(self.autostart_var.get())

        self.interval_var.set(str(interval))
        self.goal_var.set(str(goal))
        self.amount_var.set(str(amount))
        self.ball_style_var.set(BALL_STYLE_LABELS[ball_style])

        interval_changed = interval != self.config["interval_minutes"]
        self.config["interval_minutes"] = interval
        self.config["daily_goal_ml"] = goal
        self.config["drink_amount_ml"] = amount
        self.config["floating_ball_enabled"] = floating_ball_enabled
        self.config["ball_style"] = ball_style
        self.config["autostart_enabled"] = autostart_enabled

        if interval_changed:
            self.config["next_reminder_at"] = self.config["last_drink_at"] + interval * 60
            self.last_beep = 0

        if not floating_ball_enabled:
            self.hide_floating_ball()

        if not self.sync_autostart(show_errors=True):
            self.config["autostart_enabled"] = False
            self.autostart_var.set(False)

        self.save_config()
        self.refresh_all()
        self.update_floating_ball()

    def quit(self):
        self.remember_window_position()
        self.save_ball_position()
        self.save_config()
        self.root.destroy()

    def remember_window_position(self):
        try:
            self.config["window_x"] = self.root.winfo_x()
            self.config["window_y"] = self.root.winfo_y()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        WaterReminder().run()
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("喝水提醒启动失败", str(exc))
        sys.exit(1)
