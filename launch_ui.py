from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "VSCode 多窗口 Codex Key"
DEFAULT_WORKSPACE = r"D:\项目"
DEFAULT_RELAY_HOME = Path.home() / ".codex-relay"
DEFAULT_ACCOUNTS_PATH = DEFAULT_RELAY_HOME / "accounts.json"
DEFAULT_TEST_MODEL = "gpt-5.5"
CLAUDE_CODE_ONLY_MARKER = "can only be used in Claude Code"
PROVIDER_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
MAX_KEY_TEST_WORKERS = 8


def resource_path(name: str) -> Path:
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir) / name
    return Path(__file__).with_name(name)


SCRIPT_PATH = resource_path("Start-CodexRelayVSCode.ps1")


@dataclass(frozen=True)
class KeyTestResult:
    status: str
    detail: str


def compact_response_error(body: str) -> str:
    body = body.strip()
    if not body:
        return "无响应正文"

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        text = body
    else:
        error = data.get("error") if isinstance(data, dict) else data
        if isinstance(error, dict):
            text = str(error.get("message") or error.get("error") or error)
        else:
            text = str(error)

    text = " ".join(text.split())
    if len(text) > 180:
        return text[:177] + "..."
    return text


def classify_key_test_response(status_code: int, body: str) -> KeyTestResult:
    detail = compact_response_error(body)
    if 200 <= status_code < 300:
        return KeyTestResult("可用", f"HTTP {status_code}")
    if status_code == 401 and CLAUDE_CODE_ONLY_MARKER in body:
        return KeyTestResult("仅 Claude Code 可用", detail)
    if status_code == 401:
        return KeyTestResult("401 不可用", detail)
    if status_code == 403:
        return KeyTestResult("403 禁止", detail)
    if status_code == 429:
        return KeyTestResult("额度或限速", detail)
    if 500 <= status_code < 600:
        return KeyTestResult("服务端错误", f"HTTP {status_code}: {detail}")
    return KeyTestResult(f"HTTP {status_code}", detail)


def build_responses_url(base_url: str) -> str:
    return base_url.strip().rstrip("/") + "/responses"


def normalize_provider_name(value: str) -> str:
    return PROVIDER_NAME_PATTERN.sub("-", value.strip()).strip(".-")


def common_provider_from_items(items: list[object]) -> str:
    providers: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        provider = normalize_provider_name(str(item.get("provider") or "rightcode"))
        if provider:
            providers.add(provider)
    if len(providers) == 1:
        return next(iter(providers))
    return ""


def remove_accounts_by_name(items: list[object], names: set[str]) -> tuple[list[object], int]:
    remaining: list[object] = []
    removed = 0
    for item in items:
        if isinstance(item, dict) and str(item.get("name") or "") in names:
            removed += 1
            continue
        remaining.append(item)
    return remaining, removed


def initial_key_status(account_name: str, preferred: bool, previous_statuses: dict[str, str]) -> str:
    previous = previous_statuses.get(account_name)
    if previous:
        return previous
    return "preferred / 未测试" if preferred else "未测试"


def test_key_with_responses_api(account: "RelayAccount", timeout_seconds: int = 20) -> KeyTestResult:
    payload = {
        "model": account.model or DEFAULT_TEST_MODEL,
        "input": "ping",
        "max_output_tokens": 1,
    }
    request = urllib.request.Request(
        build_responses_url(account.base_url),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {account.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "vscode-codex-relay-key-tester/0.1",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            return classify_key_test_response(response.status, body)
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        return classify_key_test_response(exc.code, body)
    except TimeoutError:
        return KeyTestResult("超时", f"请求超过 {timeout_seconds} 秒")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            return KeyTestResult("超时", f"请求超过 {timeout_seconds} 秒")
        return KeyTestResult("网络失败", str(exc.reason))


@dataclass(frozen=True)
class RelayAccount:
    name: str
    base_url: str
    provider: str
    api_key: str
    model: str | None = None

    @property
    def masked_key(self) -> str:
        key = self.api_key
        if len(key) <= 12:
            return "****"
        return f"{key[:6]}...{key[-4:]}"


class AccountRow:
    def __init__(self, account: RelayAccount, preferred: bool, initial_status: str | None = None) -> None:
        self.account = account
        self.preferred = preferred
        self.selected = tk.BooleanVar(value=False)
        self.test_status = tk.StringVar(value=initial_status or ("preferred / 未测试" if preferred else "未测试"))
        self.test_running = False


class LauncherApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x640")
        self.minsize(860, 520)

        self.accounts_path = tk.StringVar(value=str(DEFAULT_ACCOUNTS_PATH))
        self.workspace_path = tk.StringVar(value=DEFAULT_WORKSPACE)
        self.provider_text = tk.StringVar(value="")
        self.status_text = tk.StringVar(value="就绪")
        self.search_text = tk.StringVar(value="")
        self.use_separate_extensions = tk.BooleanVar(value=False)
        self.no_copy_codex_state = tk.BooleanVar(value=False)
        self.share_state_by_provider = tk.BooleanVar(value=True)
        self.select_visible_accounts = tk.BooleanVar(value=False)

        self.rows: list[AccountRow] = []
        self.checkbuttons: dict[str, tk.Checkbutton] = {}

        self._build_ui()
        self.refresh_accounts()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self, padding=(12, 10, 12, 6))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="账号池").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(top, textvariable=self.accounts_path).grid(row=0, column=1, sticky="ew")
        ttk.Button(top, text="选择", command=self.choose_accounts_file).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(top, text="工作区").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Entry(top, textvariable=self.workspace_path).grid(row=1, column=1, sticky="ew", pady=(8, 0))
        ttk.Button(top, text="选择", command=self.choose_workspace).grid(row=1, column=2, padx=(8, 0), pady=(8, 0))

        ttk.Label(top, text="供应商").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Entry(top, textvariable=self.provider_text).grid(row=2, column=1, sticky="ew", pady=(8, 0))
        ttk.Button(top, text="应用到全部", command=self.apply_provider_to_all).grid(
            row=2,
            column=2,
            padx=(8, 0),
            pady=(8, 0),
        )

        controls = ttk.Frame(self, padding=(12, 0, 12, 8))
        controls.grid(row=1, column=0, sticky="ew")
        controls.columnconfigure(3, weight=1)

        ttk.Button(controls, text="刷新账号", command=self.refresh_accounts).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(controls, text="全选", command=self.select_all).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(controls, text="清空", command=self.clear_selection).grid(row=0, column=2, padx=(0, 8))
        ttk.Entry(controls, textvariable=self.search_text).grid(row=0, column=3, sticky="ew", padx=(0, 8))
        ttk.Button(controls, text="搜索", command=self.render_accounts).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(controls, text="测试选中", command=self.test_selected).grid(row=0, column=5, padx=(0, 8))
        ttk.Button(controls, text="选择可用", command=self.select_available).grid(row=0, column=6, padx=(0, 8))
        ttk.Button(controls, text="删除选中", command=self.delete_selected_accounts).grid(row=0, column=7, padx=(0, 8))
        ttk.Button(controls, text="打开选中窗口", command=self.launch_selected).grid(row=0, column=8)

        options = ttk.Frame(self, padding=(12, 0, 12, 8))
        options.grid(row=2, column=0, sticky="new")
        options.columnconfigure(3, weight=1)
        ttk.Checkbutton(
            options,
            text="隔离 VSCode extensions 目录",
            variable=self.use_separate_extensions,
        ).grid(row=0, column=0, sticky="w", padx=(0, 16))
        ttk.Checkbutton(
            options,
            text="同 provider 共享 Codex 会话状态",
            variable=self.share_state_by_provider,
        ).grid(row=0, column=1, sticky="w", padx=(0, 16))
        ttk.Checkbutton(
            options,
            text="不复制默认 Codex 状态",
            variable=self.no_copy_codex_state,
        ).grid(row=0, column=2, sticky="w")

        table_host = ttk.Frame(self, padding=(12, 0, 12, 8))
        table_host.grid(row=3, column=0, sticky="nsew")
        table_host.columnconfigure(0, weight=1)
        table_host.rowconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        self.canvas = tk.Canvas(table_host, highlightthickness=1, highlightbackground="#d0d7de")
        self.scrollbar = ttk.Scrollbar(table_host, orient="vertical", command=self.canvas.yview)
        self.accounts_frame = ttk.Frame(self.canvas)
        self.accounts_frame.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas_window = self.canvas.create_window((0, 0), window=self.accounts_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.bind("<Configure>", self._resize_canvas_window)

        footer = ttk.Frame(self, padding=(12, 0, 12, 10))
        footer.grid(row=4, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_text).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="退出", command=self.destroy).grid(row=0, column=1, sticky="e")

    def _resize_canvas_window(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def choose_accounts_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 accounts.json",
            initialdir=self.accounts_file_initial_dir(),
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.accounts_path.set(path)
            self.refresh_accounts()

    def accounts_file_initial_dir(self) -> str:
        current = Path(self.accounts_path.get()).expanduser()
        if current.exists():
            return str(current.parent if current.is_file() else current)
        if current.parent.exists():
            return str(current.parent)
        return str(Path.home())

    def choose_workspace(self) -> None:
        path = filedialog.askdirectory(
            title="选择 VSCode 工作区目录",
            initialdir=self.workspace_path.get() or DEFAULT_WORKSPACE,
        )
        if path:
            self.workspace_path.set(path)

    def refresh_accounts(self) -> None:
        try:
            previous_statuses = {row.account.name: row.test_status.get() for row in self.rows}
            relay_path = Path(self.accounts_path.get()).expanduser()
            relay = json.loads(relay_path.read_text(encoding="utf-8-sig"))
            preferred = str(relay.get("preferred") or "")
            accounts_raw = relay.get("accounts")
            if not isinstance(accounts_raw, list):
                raise ValueError("accounts.json 中的 accounts 必须是数组")
            self.provider_text.set(common_provider_from_items(accounts_raw))

            rows: list[AccountRow] = []
            seen: set[str] = set()
            for item in accounts_raw:
                name = str(item.get("name") or "").strip()
                base_url = str(item.get("baseUrl") or "").strip()
                provider = normalize_provider_name(str(item.get("provider") or "rightcode")) or "rightcode"
                api_key = str(item.get("apiKey") or "").strip()
                model = item.get("model")
                if not name or not base_url or not api_key:
                    continue
                if name in seen:
                    name = f"{name} (重复)"
                seen.add(name)
                is_preferred = name == preferred
                rows.append(
                    AccountRow(
                        RelayAccount(
                            name=name,
                            base_url=base_url,
                            provider=provider,
                            api_key=api_key,
                            model=str(model).strip() if model else None,
                        ),
                        preferred=is_preferred,
                        initial_status=initial_key_status(name, is_preferred, previous_statuses),
                    )
                )

            self.rows = rows
            self.render_accounts()
            self.status_text.set(f"已加载 {len(rows)} 个账号：{relay_path}")
        except Exception as exc:
            self.rows = []
            self.render_accounts()
            self.status_text.set("账号加载失败")
            messagebox.showerror(APP_TITLE, f"读取账号池失败：\n{exc}")

    def apply_provider_to_all(self) -> None:
        provider = normalize_provider_name(self.provider_text.get())
        if not provider:
            messagebox.showwarning(APP_TITLE, "供应商不能为空，只能使用字母、数字、下划线、点和短横线。")
            return

        try:
            relay_path = Path(self.accounts_path.get()).expanduser()
            relay = json.loads(relay_path.read_text(encoding="utf-8-sig"))
            accounts_raw = relay.get("accounts")
            if not isinstance(accounts_raw, list):
                raise ValueError("accounts.json 中的 accounts 必须是数组")

            updated = 0
            for item in accounts_raw:
                if not isinstance(item, dict):
                    continue
                item["provider"] = provider
                updated += 1

            relay_path.write_text(
                json.dumps(relay, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.refresh_accounts()
            self.status_text.set(f"已把 {updated} 个账号的供应商改为 {provider}")
        except Exception as exc:
            self.status_text.set("供应商更新失败")
            messagebox.showerror(APP_TITLE, f"更新供应商失败：\n{exc}")

    def render_accounts(self) -> None:
        for child in self.accounts_frame.winfo_children():
            child.destroy()
        self.checkbuttons.clear()

        visible_rows = self.visible_rows()
        self.select_visible_accounts.set(bool(visible_rows) and all(row.selected.get() for row in visible_rows))

        headers = ["选择", "账号名", "Base URL", "供应商", "Key", "模型", "Key状态", "操作"]
        weights = [0, 2, 3, 1, 1, 1, 1, 0]
        for col, (header, weight) in enumerate(zip(headers, weights, strict=True)):
            self.accounts_frame.columnconfigure(col, weight=weight)
            if col == 0:
                tk.Checkbutton(
                    self.accounts_frame,
                    text=header,
                    variable=self.select_visible_accounts,
                    command=self.toggle_visible_selection,
                ).grid(row=0, column=col, sticky="w", padx=(6, 0), pady=2)
                continue
            label = ttk.Label(self.accounts_frame, text=header, padding=(8, 6), anchor="w")
            label.grid(row=0, column=col, sticky="ew")

        for index, row in enumerate(visible_rows, start=1):
            account = row.account
            check = tk.Checkbutton(self.accounts_frame, variable=row.selected)
            check.grid(row=index, column=0, sticky="w", padx=(6, 0), pady=2)
            self.checkbuttons[account.name] = check

            values = [
                account.name,
                account.base_url,
                account.provider,
                account.masked_key,
                account.model or "",
            ]
            for col, value in enumerate(values, start=1):
                label = ttk.Label(self.accounts_frame, text=value, padding=(8, 4), anchor="w")
                label.grid(row=index, column=col, sticky="ew")
            ttk.Label(
                self.accounts_frame,
                textvariable=row.test_status,
                padding=(8, 4),
                anchor="w",
            ).grid(row=index, column=6, sticky="ew")
            ttk.Button(
                self.accounts_frame,
                text="测试",
                command=lambda current=row: self.test_account(current),
            ).grid(row=index, column=7, sticky="ew", padx=(4, 6), pady=2)

        self.canvas.yview_moveto(0)

    def visible_rows(self) -> list[AccountRow]:
        query = self.search_text.get().strip().lower()
        return [
            row
            for row in self.rows
            if not query
            or query in row.account.name.lower()
            or query in row.account.base_url.lower()
            or query in row.account.provider.lower()
            or query in row.account.masked_key.lower()
        ]

    def toggle_visible_selection(self) -> None:
        target = self.select_visible_accounts.get()
        rows = self.visible_rows()
        for row in rows:
            row.selected.set(target)
        action = "选中" if target else "取消选中"
        self.status_text.set(f"已{action} {len(rows)} 个当前可见账号")

    def select_all(self) -> None:
        rows = self.visible_rows()
        for row in rows:
            row.selected.set(True)
        self.select_visible_accounts.set(bool(rows))
        self.status_text.set(f"已选中 {len(rows)} 个当前可见账号")

    def clear_selection(self) -> None:
        for row in self.rows:
            row.selected.set(False)
        self.select_visible_accounts.set(False)
        self.status_text.set("已清空选择")

    def select_available(self) -> None:
        available_rows = [row for row in self.rows if row.test_status.get() == "可用"]
        for row in self.rows:
            row.selected.set(row in available_rows)
        self.select_visible_accounts.set(
            bool(self.visible_rows()) and all(row.selected.get() for row in self.visible_rows())
        )
        self.status_text.set(f"已选择 {len(available_rows)} 个可用账号")

    def selected_account_names(self) -> list[str]:
        return [row.account.name for row in self.rows if row.selected.get() and " (重复)" not in row.account.name]

    def selected_rows(self) -> list[AccountRow]:
        return [row for row in self.rows if row.selected.get()]

    def delete_selected_accounts(self) -> None:
        selected = self.selected_account_names()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请至少勾选一个账号。")
            return

        confirmed = messagebox.askyesno(
            APP_TITLE,
            "确定永久删除选中的账号吗？\n\n"
            f"将从 accounts.json 删除 {len(selected)} 个账号，并删除对应 profile 的 auth.json。\n"
            "此操作不能撤销。",
        )
        if not confirmed:
            return

        selected_names = set(selected)
        try:
            relay_path = Path(self.accounts_path.get()).expanduser()
            relay = json.loads(relay_path.read_text(encoding="utf-8-sig"))
            accounts_raw = relay.get("accounts")
            if not isinstance(accounts_raw, list):
                raise ValueError("accounts.json 中的 accounts 必须是数组")

            remaining, removed = remove_accounts_by_name(accounts_raw, selected_names)
            if removed == 0:
                messagebox.showinfo(APP_TITLE, "没有找到要删除的账号。")
                return

            relay["accounts"] = remaining
            if str(relay.get("preferred") or "") in selected_names:
                next_preferred = ""
                for item in remaining:
                    if isinstance(item, dict) and str(item.get("name") or "").strip():
                        next_preferred = str(item["name"])
                        break
                relay["preferred"] = next_preferred

            relay_path.write_text(
                json.dumps(relay, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            deleted_auth_files = self.delete_profile_auth_files(selected_names)
            self.refresh_accounts()
            self.status_text.set(f"已删除 {removed} 个账号，清理 {deleted_auth_files} 个 profile auth.json")
        except Exception as exc:
            self.status_text.set("删除账号失败")
            messagebox.showerror(APP_TITLE, f"删除账号失败：\n{exc}")

    def delete_profile_auth_files(self, account_names: set[str]) -> int:
        deleted = 0
        profiles_root = Path.home() / ".codex-profiles"
        for name in account_names:
            auth_path = profiles_root / name / "auth.json"
            if not auth_path.exists():
                continue
            auth_path.unlink()
            deleted += 1
        return deleted

    def test_account(self, row: AccountRow) -> None:
        self.start_key_tests([row])

    def test_selected(self) -> None:
        rows = self.selected_rows()
        if not rows:
            messagebox.showwarning(APP_TITLE, "请至少勾选一个账号。")
            return
        self.start_key_tests(rows)

    def start_key_tests(self, rows: list[AccountRow]) -> None:
        pending = [row for row in rows if not row.test_running]
        if not pending:
            return

        for row in pending:
            row.test_running = True
            row.test_status.set("测试中...")

        self.status_text.set(f"正在测试 {len(pending)} 个 key...")
        threading.Thread(target=self._key_test_worker, args=(pending,), daemon=True).start()

    def _key_test_worker(self, rows: list[AccountRow]) -> None:
        results: list[KeyTestResult] = []
        workers = max(1, min(MAX_KEY_TEST_WORKERS, len(rows)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(test_key_with_responses_api, row.account): row for row in rows}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = KeyTestResult("测试失败", str(exc))
                results.append(result)
                self.after(0, lambda current=row, current_result=result: self.finish_key_test(current, current_result))

        self.after(0, lambda: self.show_key_test_summary(results))

    def finish_key_test(self, row: AccountRow, result: KeyTestResult) -> None:
        row.test_running = False
        row.test_status.set(result.status)
        self.status_text.set(f"{row.account.name}: {result.status} - {result.detail}")

    def show_key_test_summary(self, results: list[KeyTestResult]) -> None:
        available = sum(1 for result in results if result.status == "可用")
        claude_only = sum(1 for result in results if result.status == "仅 Claude Code 可用")
        self.status_text.set(
            f"测试完成：{available}/{len(results)} 可用，{claude_only} 个仅 Claude Code 可用。可点“选择可用”。"
        )

    def launch_selected(self) -> None:
        selected = self.selected_account_names()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请至少勾选一个账号。")
            return

        workspace = self.workspace_path.get().strip()
        if not workspace:
            messagebox.showwarning(APP_TITLE, "请选择 VSCode 工作区目录。")
            return
        if not Path(workspace).exists():
            messagebox.showwarning(APP_TITLE, f"工作区不存在：\n{workspace}")
            return
        if not SCRIPT_PATH.exists():
            messagebox.showerror(APP_TITLE, f"找不到启动脚本：\n{SCRIPT_PATH}")
            return

        self.status_text.set(f"正在打开 {len(selected)} 个 VSCode 窗口...")
        threading.Thread(target=self._launch_worker, args=(selected, workspace), daemon=True).start()

    def _launch_worker(self, selected: list[str], workspace: str) -> None:
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT_PATH),
            "-AccountsPath",
            str(Path(self.accounts_path.get()).expanduser()),
            "-Accounts",
            ",".join(selected),
            "-Workspace",
            workspace,
        ]
        if self.use_separate_extensions.get():
            command.append("-UseSeparateExtensionsDir")
        if self.share_state_by_provider.get():
            command.append("-ShareStateByProvider")
        if self.no_copy_codex_state.get():
            command.append("-NoCopyCodexState")

        try:
            completed = subprocess.run(
                command,
                cwd=str(SCRIPT_PATH.parent),
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if completed.returncode != 0:
                message = completed.stderr.strip() or completed.stdout.strip() or f"退出码：{completed.returncode}"
                self.after(0, lambda: self._show_launch_error(message))
                return
            self.after(0, lambda: self.status_text.set(f"已打开 {len(selected)} 个 VSCode 窗口"))
        except Exception as exc:
            self.after(0, lambda: self._show_launch_error(str(exc)))

    def _show_launch_error(self, message: str) -> None:
        self.status_text.set("打开窗口失败")
        messagebox.showerror(APP_TITLE, f"打开 VSCode 窗口失败：\n{message}")


def main() -> int:
    if os.name != "nt":
        print("此工具当前只支持 Windows。", file=sys.stderr)
        return 1
    app = LauncherApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())