"""首次使用引导和可重复打开的连接设置。"""

import os
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import config
from settings_store import DEFAULTS, ENV_NAMES, ai_is_configured, validate_settings, write_settings


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, first_run=False):
        super().__init__(parent)
        self.withdraw()
        self.saved = False
        self.title("欢迎使用 · 首次配置" if first_run else "连接设置")
        self.transient(parent)
        self.resizable(False, False)
        self.variables = {key: tk.StringVar(self, value=getattr(config, key)) for key in DEFAULTS}
        if self.variables["API_KEY"].get() == "YOUR_API_KEY":
            self.variables["API_KEY"].set("")
        self.remember_password = tk.BooleanVar(self, value=bool(config.LOCAL_SETTINGS.get("CF_PASSWORD")))
        self.show_secrets = tk.BooleanVar(self, value=False)
        self.entries = {}
        self.secret_entries = []

        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="先连接 AI 服务，再开始训练" if first_run else "AI 与 Codeforces 连接设置",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(body, text="填写您自己的配置。CF 账号信息均为选填。", padding=(0, 6, 0, 10)).pack(anchor="w")
        if config.SETTINGS_ERROR:
            ttk.Label(body, text="原配置无法读取。保存前会备份原文件，然后写入新配置。",
                      foreground="#b45309").pack(anchor="w", pady=(0, 8))
        tabs = ttk.Notebook(body)
        tabs.pack(fill="both", expand=True)
        ai = ttk.Frame(tabs, padding=12)
        cf = ttk.Frame(tabs, padding=12)
        tabs.add(ai, text="AI 服务（必填）")
        tabs.add(cf, text="Codeforces（选填）")
        for frame in (ai, cf):
            frame.columnconfigure(1, weight=1)

        ttk.Label(ai, text="服务预设").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=6)
        self.provider = ttk.Combobox(ai, state="readonly", values=("DeepSeek", "OpenAI", "自定义"), width=18)
        url = self.variables["BASE_URL"].get().rstrip("/")
        self.provider.set("DeepSeek" if url == "https://api.deepseek.com" else
                          "OpenAI" if url in ("", "https://api.openai.com/v1") else "自定义")
        self.provider.grid(row=0, column=1, sticky="w")
        self.provider.bind("<<ComboboxSelected>>", self._choose_provider)
        self._field(ai, 1, "API_KEY", "API Key", secret=True)
        self._field(ai, 2, "BASE_URL", "服务地址")
        self._field(ai, 3, "MODEL", "模型名称")
        ttk.Label(ai, text="OpenAI 官方地址可留空；兼容接口请填写服务商提供的地址和模型。",
                  wraplength=490).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 4))
        ttk.Label(ai, text="保存只检查填写格式；首次组题时会实际连接，AI 用量由服务商计费。",
                  wraplength=490).grid(row=5, column=0, columnspan=2, sticky="w")

        self._field(cf, 0, "CF_HANDLE", "CF 用户名")
        self._field(cf, 1, "CF_PASSWORD", "CF 密码", secret=True)
        remember = ttk.Checkbutton(cf, text="记住 CF 密码（写入本地配置文件）", variable=self.remember_password)
        remember.grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 10))
        if "CF_PASSWORD" in os.environ:
            remember.state(["disabled"])
        ttk.Label(cf, text="抓取公开评测结果只需要用户名。密码可留空，在 Edge 中手动登录。",
                  wraplength=490).grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._field(cf, 4, "CF_API_KEY", "CF API Key", secret=True)
        self._field(cf, 5, "CF_API_SECRET", "CF API Secret", secret=True)
        ttk.Label(cf, text="API 凭据仅供补充原场统计等维护工具使用，与 CF 登录密码不同。",
                  wraplength=490).grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Checkbutton(body, text="显示密钥与密码", variable=self.show_secrets,
                        command=self._toggle_secrets).pack(anchor="w", pady=(10, 5))
        ttk.Label(body, text="API 密钥及勾选记住的密码将以明文保存在本机 settings.json，请勿随分享包外传。",
                  wraplength=560, foreground="#666").pack(anchor="w")
        overridden = [key for key in DEFAULTS if ENV_NAMES[key] in os.environ]
        if overridden:
            ttk.Label(body, text="带 * 的项目由环境变量管理；要修改，请先在系统中修改或移除相应环境变量。",
                      wraplength=560, foreground="#666").pack(anchor="w", pady=(6, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(16, 0))
        ttk.Button(buttons, text="稍后设置" if first_run else "取消", command=self.destroy).pack(side="left")
        ttk.Button(buttons, text="保存并开始" if first_run else "保存设置", command=self._save).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.update_idletasks()
        x = max(0, parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2)
        y = max(0, parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 2)
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.wait_visibility()
        self.grab_set()
        self.entries["API_KEY"].focus_set()

    def _field(self, parent, row, key, title, secret=False):
        locked = ENV_NAMES[key] in os.environ
        ttk.Label(parent, text=title + (" *" if locked else "")).grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=6)
        entry = ttk.Entry(parent, textvariable=self.variables[key], width=48,
                          show="*" if secret else "", state="readonly" if locked else "normal")
        entry.grid(row=row, column=1, sticky="ew")
        self.entries[key] = entry
        if secret:
            self.secret_entries.append(entry)

    def _toggle_secrets(self):
        for entry in self.secret_entries:
            entry.configure(show="" if self.show_secrets.get() else "*")

    def _choose_provider(self, _event=None):
        preset = {
            "DeepSeek": ("https://api.deepseek.com", "deepseek-chat"),
            "OpenAI": ("", "gpt-4o-mini"),
        }.get(self.provider.get())
        if preset:
            for key, value in zip(("BASE_URL", "MODEL"), preset):
                if ENV_NAMES[key] not in os.environ:
                    self.variables[key].set(value)

    def _save(self):
        values = {key: var.get() for key, var in self.variables.items()}
        # 密码保留原有字符；其他字段去除粘贴带来的首尾空白。
        values = {key: value if key == "CF_PASSWORD" else value.strip() for key, value in values.items()}
        try:
            validate_settings(values)
        except ValueError as exc:
            messagebox.showerror("请检查配置", str(exc), parent=self)
            return
        local = dict(config.LOCAL_SETTINGS)
        for key, value in values.items():
            if ENV_NAMES[key] not in os.environ:
                local[key] = value
        if not self.remember_password.get():
            local.pop("CF_PASSWORD", None)
        try:
            if config.SETTINGS_ERROR and Path(config._SETTINGS_FILE).exists():
                # 损坏配置不静默丢弃；备份也只允许本机用户读取（POSIX）。
                backup = Path(config._SETTINGS_FILE).with_name(
                    "settings.invalid-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
                with open(backup, "xb", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
                    stream.write(Path(config._SETTINGS_FILE).read_bytes())
            write_settings(config._SETTINGS_FILE, local)
        except OSError:
            messagebox.showerror("保存失败", "无法写入配置文件。请将程序移到可写目录后重试。原配置未更改。", parent=self)
            return
        config.apply_connection_settings(local, session_password=values["CF_PASSWORD"])
        self.saved = True
        self.destroy()


def show_settings(parent, first_run=False):
    dialog = SettingsDialog(parent, first_run=first_run)
    parent.wait_window(dialog)
    return dialog.saved


def needs_setup():
    return bool(config.SETTINGS_ERROR or not ai_is_configured(vars(config)))
