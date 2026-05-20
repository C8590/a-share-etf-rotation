from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path


def configure_tcl_environment() -> None:
    candidates = [
        (Path(r"D:\tcl\tcl8.6"), Path(r"D:\tcl\tk8.6")),
        (Path(sys.base_prefix) / "tcl" / "tcl8.6", Path(sys.base_prefix) / "tcl" / "tk8.6"),
        (Path(sys.prefix) / "tcl" / "tcl8.6", Path(sys.prefix) / "tcl" / "tk8.6"),
    ]
    for tcl_dir, tk_dir in candidates:
        if tcl_dir.joinpath("init.tcl").exists() and tk_dir.joinpath("tk.tcl").exists():
            os.environ.setdefault("TCL_LIBRARY", str(tcl_dir))
            os.environ.setdefault("TK_LIBRARY", str(tk_dir))
            break


configure_tcl_environment()

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk


PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
OUTPUT_DIR = PROJECT_ROOT / "output"
BACKUP_DIR = PROJECT_ROOT / "backup"
CURRENT_POSITION = PROJECT_ROOT / "config" / "current_position.yaml"
README = PROJECT_ROOT / "README.md"
QA_REPORT = OUTPUT_DIR / "qa_report.txt"
COMPARE_SIGNAL_TXT = OUTPUT_DIR / "compare_signal.txt"
COMPARE_SIGNAL_CSV = OUTPUT_DIR / "compare_signal.csv"
RECOMMENDED_STRATEGY = "日频右侧确认型 ETF 动量轮动策略"


def relative_command(command: str) -> str:
    return rf".\.venv\Scripts\python.exe main.py {command}"


def ensure_venv() -> None:
    if not VENV_PYTHON.exists():
        raise FileNotFoundError(
            f"未找到项目虚拟环境 Python:\n{VENV_PYTHON}\n\n"
            "请先创建并安装 .venv，不要使用全局 Python 或全局 pip。"
        )


def open_path(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"文件或目录不存在:\n{path}")
    start_file = getattr(path, "startfile", None)
    if start_file is not None:
        start_file()
        return
    import os

    os.startfile(str(path))  # type: ignore[attr-defined]


def run_main_command(command: str) -> subprocess.CompletedProcess[str]:
    ensure_venv()
    return subprocess.run(
        [str(VENV_PYTHON), "main.py", *shlex.split(command, posix=False)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def qa_check_allows_observation() -> bool:
    if not (OUTPUT_DIR / "qa_report.json").exists():
        return False
    with (OUTPUT_DIR / "qa_report.json").open("r", encoding="utf-8") as f:
        report = json.load(f)
    return bool(report.get("allow_small_observation"))


def backup_output_directory() -> Path:
    if not OUTPUT_DIR.exists():
        raise FileNotFoundError(f"output 目录不存在:\n{OUTPUT_DIR}")
    BACKUP_DIR.mkdir(exist_ok=True)
    destination = BACKUP_DIR / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    shutil.copytree(OUTPUT_DIR, destination)
    return destination


class QuantLauncher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("日频右侧确认型 ETF 动量轮动策略")
        self.geometry("980x720")
        self.minsize(860, 620)

        self.status_var = tk.StringVar(value="空闲")
        self.buttons: list[ttk.Button] = []

        self._build_ui()
        self._log("启动器已就绪。所有命令都会使用项目 .venv 内的 Python。")
        if not VENV_PYTHON.exists():
            self._set_status("失败")
            self._log(f"[ERROR] 未找到虚拟环境 Python: {VENV_PYTHON}")

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        info = ttk.Frame(self, padding=12)
        info.grid(row=0, column=0, sticky="ew")
        info.columnconfigure(1, weight=1)

        ttk.Label(info, text="项目路径:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(info, text=str(PROJECT_ROOT)).grid(row=0, column=1, sticky="w")
        ttk.Label(info, text="Python 路径:").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Label(info, text=str(VENV_PYTHON)).grid(row=1, column=1, sticky="w")
        ttk.Label(info, text="当前主观察策略:").grid(row=2, column=0, sticky="w", padx=(0, 8))
        ttk.Label(info, text=RECOMMENDED_STRATEGY).grid(row=2, column=1, sticky="w")
        ttk.Label(info, text="小资金观察建议:").grid(row=3, column=0, sticky="w", padx=(0, 8))
        ttk.Label(info, text="1000-3000 元").grid(row=3, column=1, sticky="w")

        risk = (
            "风险提示：本工具只生成日频动量轮动观察信号，不自动下单，不连接券商，"
            "不替代人工判断，不构成投资建议。"
        )
        ttk.Label(info, text=risk, foreground="#9a3412", wraplength=900).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

        actions = ttk.Frame(self, padding=(12, 0, 12, 8))
        actions.grid(row=1, column=0, sticky="ew")
        for idx in range(5):
            actions.columnconfigure(idx, weight=1)

        self._add_button(actions, "一键生成信号", lambda: self.run_workflow(["update-data", "qa-check", "generate-signal --use-cache"], open_reports=True), 0, 0)
        self._add_button(actions, "只更新数据", lambda: self.run_workflow(["update-data"]), 0, 1)
        self._add_button(actions, "只做质量检查", lambda: self.run_workflow(["qa-check"]), 0, 2)
        self._add_button(actions, "只生成信号", lambda: self.run_workflow(["generate-signal --use-cache"]), 0, 3)
        self._add_button(actions, "备份当前 output", self.backup_output, 0, 4)

        self._add_button(actions, "打开质量报告", lambda: self.open_file(QA_REPORT), 1, 0)
        self._add_button(actions, "打开信号文件", lambda: self.open_file(COMPARE_SIGNAL_TXT), 1, 1)
        self._add_button(actions, "打开信号表格", lambda: self.open_file(COMPARE_SIGNAL_CSV), 1, 2)
        self._add_button(actions, "编辑当前持仓", lambda: self.open_file(CURRENT_POSITION), 1, 3)
        self._add_button(actions, "打开 output 文件夹", lambda: self.open_file(OUTPUT_DIR), 1, 4)
        self._add_button(actions, "打开 README.md", lambda: self.open_file(README), 2, 0)

        self.log = scrolledtext.ScrolledText(self, height=24, wrap="word", font=("Consolas", 10))
        self.log.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))

        status = ttk.Frame(self, padding=(12, 0, 12, 10))
        status.grid(row=3, column=0, sticky="ew")
        ttk.Label(status, text="状态:").pack(side="left")
        ttk.Label(status, textvariable=self.status_var).pack(side="left", padx=(6, 0))

    def _add_button(self, parent: ttk.Frame, text: str, command, row: int, column: int) -> None:
        button = ttk.Button(parent, text=text, command=command)
        button.grid(row=row, column=column, sticky="ew", padx=4, pady=4)
        self.buttons.append(button)

    def _set_status(self, value: str) -> None:
        self.status_var.set(value)
        self.update_idletasks()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self.buttons:
            button.configure(state=state)

    def _log(self, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.insert("end", f"[{timestamp}] {text}\n")
        self.log.see("end")
        self.update_idletasks()

    def _log_process_result(self, command: str, result: subprocess.CompletedProcess[str]) -> None:
        self._log(f"命令: {relative_command(command)}")
        self._log(f"返回码: {result.returncode}")
        self._log("标准输出:")
        self._log(result.stdout.rstrip() if result.stdout.strip() else "(无)")
        self._log("错误输出:")
        self._log(result.stderr.rstrip() if result.stderr.strip() else "(无)")

    def run_workflow(self, commands: list[str], open_reports: bool = False) -> None:
        def worker() -> None:
            self.after(0, self._set_status, "运行中")
            self.after(0, self._set_buttons_enabled, False)
            failed = False
            try:
                ensure_venv()
                for command in commands:
                    self.after(0, self._log, f"开始执行: {relative_command(command)}")
                    result = run_main_command(command)
                    self.after(0, self._log_process_result, command, result)
                    if result.returncode != 0:
                        failed = True
                        if command == "qa-check":
                            self.after(0, self._qa_failed_warning)
                        break
                    if command == "qa-check" and not qa_check_allows_observation():
                        failed = True
                        self.after(0, self._qa_failed_warning)
                        break

                if failed:
                    self.after(0, self._set_status, "失败")
                    return

                self.after(0, self._set_status, "完成")
                self.after(0, self._log, "流程完成。")
                if open_reports:
                    self.after(0, self._open_reports_after_workflow)
            except Exception as exc:  # GUI boundary: show readable errors instead of closing.
                self.after(0, self._set_status, "失败")
                self.after(0, self._log, f"[ERROR] {exc}")
                self.after(0, messagebox.showerror, "执行失败", str(exc))
            finally:
                self.after(0, self._set_buttons_enabled, True)

        threading.Thread(target=worker, daemon=True).start()

    def _qa_failed_warning(self) -> None:
        text = "质量检查未通过，不建议操作。"
        self._log(f"[IMPORTANT] {text}")
        messagebox.showwarning("质量检查未通过", text)

    def _open_reports_after_workflow(self) -> None:
        for path in (QA_REPORT, COMPARE_SIGNAL_TXT):
            try:
                open_path(path)
                self._log(f"已打开: {path}")
            except Exception as exc:
                self._log(f"[ERROR] 打开失败 {path}: {exc}")

    def open_file(self, path: Path) -> None:
        try:
            open_path(path)
            self._log(f"已打开: {path}")
        except Exception as exc:
            self._set_status("失败")
            self._log(f"[ERROR] {exc}")
            messagebox.showerror("打开失败", str(exc))

    def backup_output(self) -> None:
        try:
            destination = backup_output_directory()
            self._set_status("完成")
            self._log(f"已备份 output 到: {destination}")
            messagebox.showinfo("备份完成", f"已备份到:\n{destination}")
        except Exception as exc:
            self._set_status("失败")
            self._log(f"[ERROR] 备份失败: {exc}")
            messagebox.showerror("备份失败", str(exc))


def run_self_test() -> int:
    ensure_venv()
    required_paths = [PROJECT_ROOT, VENV_PYTHON, README, OUTPUT_DIR, CURRENT_POSITION]
    for path in required_paths:
        if not path.exists():
            raise FileNotFoundError(path)
    print("SELF_TEST_OK")
    print(f"project={PROJECT_ROOT}")
    print(f"python={VENV_PYTHON}")
    return 0


def run_command_test(command: str) -> int:
    result = run_main_command(command)
    print(f"command={relative_command(command)}")
    print(f"returncode={result.returncode}")
    print("stdout:")
    print(result.stdout)
    print("stderr:")
    print(result.stderr)
    return result.returncode


def run_open_targets_test() -> int:
    for path in (QA_REPORT, COMPARE_SIGNAL_TXT, COMPARE_SIGNAL_CSV, CURRENT_POSITION, OUTPUT_DIR, README):
        open_path(path)
        print(f"opened={path}")
    return 0


def run_backup_test() -> int:
    destination = backup_output_directory()
    print(f"backup={destination}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A-share ETF local launcher")
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-command", choices=["update-data", "qa-check", "generate-signal --use-cache"], help=argparse.SUPPRESS)
    parser.add_argument("--test-open-targets", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--backup-output", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()
    if args.run_command:
        return run_command_test(args.run_command)
    if args.test_open_targets:
        return run_open_targets_test()
    if args.backup_output:
        return run_backup_test()

    ensure_venv()
    app = QuantLauncher()
    app.mainloop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise
