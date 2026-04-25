from __future__ import annotations

# Memo Search (.pyw 推奨)
# - Python標準ライブラリのみ使用
# - フォルダ内のテキストファイルを横断検索
# - 対象拡張子: .txt .md .py .pyw .json など
# - 大文字小文字の区別 / 正規表現検索に対応
# - 検索結果一覧、前後プレビュー、ダブルクリックで既定アプリ起動
# - memo.pyw から Ctrl+Shift+F で --query / --folder を受け取れる

from dataclasses import dataclass
from pathlib import Path
import argparse
import json
import os
import re
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
import tkinter.font as tkfont


APP_TITLE = "Memo Search"
BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "memo_search_settings.json"

DEFAULT_EXTENSIONS = ".txt .md .py .pyw .json"
PREVIEW_CONTEXT_LINES = 5
MAX_RESULT_COUNT = 5000
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024

TEXT_ENCODINGS = [
    "utf-8-sig",
    "utf-8",
    "cp932",
    "shift_jis",
]

SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
}


@dataclass
class SearchResult:
    path: Path
    line_no: int
    line_text: str


def enable_high_dpi_mode() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def apply_tk_scaling(root: tk.Tk) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = root.winfo_id()
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        if dpi:
            root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass


def choose_font_spec(root: tk.Tk) -> tuple[str, str]:
    preferred_specs = [
        ("BIZ UDPGothic", "normal"),
        ("BIZ UDGothic", "normal"),
        ("Yu Gothic UI", "normal"),
        ("Segoe UI", "normal"),
        ("Meiryo UI", "normal"),
        ("Meiryo", "normal"),
        ("MS Gothic", "normal"),
        ("Consolas", "normal"),
    ]
    try:
        families = set(tkfont.families(root))
    except Exception:
        families = set()

    for family, weight in preferred_specs:
        if family in families:
            return family, weight

    try:
        return tkfont.nametofont("TkTextFont").cget("family"), "normal"
    except Exception:
        return "TkDefaultFont", "normal"


def load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def parse_extensions(text: str) -> set[str]:
    """拡張子入力欄を .txt/.md の集合に正規化する。"""
    raw_parts = re.split(r"[\s,;、]+", text.strip())
    extensions: set[str] = set()
    for part in raw_parts:
        part = part.strip().lower()
        if not part:
            continue
        if not part.startswith("."):
            part = "." + part
        extensions.add(part)
    return extensions


def read_text_file(path: Path) -> str | None:
    """複数エンコーディングでテキストとして読めるものだけ読む。"""
    try:
        if path.stat().st_size > MAX_FILE_SIZE_BYTES:
            return None
    except OSError:
        return None

    for encoding in TEXT_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError:
            return None

    return None


def iter_target_files(folder: Path, extensions: set[str]) -> list[Path]:
    """検索対象ファイルを収集する。

    rglob 中にフォルダを除外するには os.walk の方が扱いやすいので、
    標準的に重い/ノイズになりやすいディレクトリだけは飛ばす。
    """
    files: list[Path] = []
    try:
        for root, dirnames, filenames in os.walk(folder):
            dirnames[:] = [name for name in dirnames if name not in SKIP_DIR_NAMES]
            root_path = Path(root)
            for filename in filenames:
                path = root_path / filename
                if path.suffix.lower() in extensions:
                    files.append(path)
    except OSError:
        pass
    return files


def open_with_default_app(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class MemoSearchApp:
    def __init__(self, root: tk.Tk, *, initial_folder: Path, initial_query: str = "") -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(900, 620)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.settings = load_settings()
        self.results: list[SearchResult] = []
        self._last_regex: re.Pattern[str] | None = None
        self._last_literal_query = ""
        self._last_case_sensitive = False
        self._last_regex_mode = False

        font_family, font_weight = choose_font_spec(root)
        self.ui_font = tkfont.Font(family=font_family, size=10, weight=font_weight)
        self.preview_font = tkfont.Font(family=font_family, size=11, weight=font_weight)

        saved_geometry = self.settings.get("geometry")
        if isinstance(saved_geometry, str) and saved_geometry:
            try:
                self.root.geometry(saved_geometry)
            except Exception:
                self.root.geometry("1100x760")
        else:
            self.root.geometry("1100x760")

        saved_folder = self.settings.get("folder")
        if initial_folder:
            folder_text = str(initial_folder)
        elif isinstance(saved_folder, str) and saved_folder:
            folder_text = saved_folder
        else:
            folder_text = str(BASE_DIR)

        saved_extensions = self.settings.get("extensions")
        if not isinstance(saved_extensions, str) or not saved_extensions.strip():
            saved_extensions = DEFAULT_EXTENSIONS

        self.query_var = tk.StringVar(value=initial_query)
        self.folder_var = tk.StringVar(value=folder_text)
        self.extensions_var = tk.StringVar(value=saved_extensions)
        self.case_sensitive_var = tk.BooleanVar(value=bool(self.settings.get("case_sensitive", False)))
        self.regex_var = tk.BooleanVar(value=bool(self.settings.get("regex", False)))
        self.status_var = tk.StringVar(value="")

        self._build_ui()
        if initial_query:
            self.root.after(120, self.search)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        query_frame = ttk.Frame(outer)
        query_frame.pack(fill="x")

        ttk.Label(query_frame, text="検索ワード:").grid(row=0, column=0, sticky="w")
        self.query_entry = ttk.Entry(query_frame, textvariable=self.query_var)
        self.query_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))

        search_button = ttk.Button(query_frame, text="検索", command=self.search)
        search_button.grid(row=0, column=2, padx=(0, 6))

        clear_button = ttk.Button(query_frame, text="クリア", command=self.clear_results)
        clear_button.grid(row=0, column=3)

        query_frame.columnconfigure(1, weight=1)

        folder_frame = ttk.Frame(outer)
        folder_frame.pack(fill="x", pady=(8, 0))

        ttk.Label(folder_frame, text="対象フォルダ:").grid(row=0, column=0, sticky="w")
        self.folder_entry = ttk.Entry(folder_frame, textvariable=self.folder_var)
        self.folder_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))

        folder_button = ttk.Button(folder_frame, text="選択", command=self.choose_folder)
        folder_button.grid(row=0, column=2)

        folder_frame.columnconfigure(1, weight=1)

        option_frame = ttk.Frame(outer)
        option_frame.pack(fill="x", pady=(8, 0))

        ttk.Label(option_frame, text="拡張子:").grid(row=0, column=0, sticky="w")
        extensions_entry = ttk.Entry(option_frame, textvariable=self.extensions_var, width=34)
        extensions_entry.grid(row=0, column=1, sticky="w", padx=(8, 16))

        case_check = ttk.Checkbutton(option_frame, text="大文字小文字を区別", variable=self.case_sensitive_var)
        case_check.grid(row=0, column=2, sticky="w", padx=(0, 12))

        regex_check = ttk.Checkbutton(option_frame, text="正規表現", variable=self.regex_var)
        regex_check.grid(row=0, column=3, sticky="w")

        self.status_label = ttk.Label(outer, textvariable=self.status_var, anchor="w")
        self.status_label.pack(fill="x", pady=(8, 6))

        paned = ttk.Panedwindow(outer, orient="vertical")
        paned.pack(fill="both", expand=True)

        result_frame = ttk.Frame(paned)
        preview_frame = ttk.Frame(paned)
        paned.add(result_frame, weight=3)
        paned.add(preview_frame, weight=2)

        columns = ("file", "line", "text")
        self.result_tree = ttk.Treeview(result_frame, columns=columns, show="headings", selectmode="browse")
        self.result_tree.heading("file", text="ファイル")
        self.result_tree.heading("line", text="行")
        self.result_tree.heading("text", text="ヒット行")
        self.result_tree.column("file", width=330, anchor="w")
        self.result_tree.column("line", width=70, anchor="e")
        self.result_tree.column("text", width=620, anchor="w")

        yscroll = ttk.Scrollbar(result_frame, orient="vertical", command=self.result_tree.yview)
        xscroll = ttk.Scrollbar(result_frame, orient="horizontal", command=self.result_tree.xview)
        self.result_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.result_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)

        preview_label_frame = ttk.Frame(preview_frame)
        preview_label_frame.pack(fill="x")
        ttk.Label(preview_label_frame, text="プレビュー:").pack(side="left")

        open_button = ttk.Button(preview_label_frame, text="選択ファイルを開く", command=self.open_selected_result)
        open_button.pack(side="right")

        copy_button = ttk.Button(preview_label_frame, text="パスをコピー", command=self.copy_selected_path)
        copy_button.pack(side="right", padx=(0, 8))

        text_frame = ttk.Frame(preview_frame)
        text_frame.pack(fill="both", expand=True, pady=(4, 0))

        self.preview_text = tk.Text(
            text_frame,
            wrap="none",
            font=self.preview_font,
            undo=False,
            padx=10,
            pady=8,
            height=10,
            state="disabled",
        )
        preview_y = ttk.Scrollbar(text_frame, orient="vertical", command=self.preview_text.yview)
        preview_x = ttk.Scrollbar(text_frame, orient="horizontal", command=self.preview_text.xview)
        self.preview_text.configure(yscrollcommand=preview_y.set, xscrollcommand=preview_x.set)
        self.preview_text.tag_configure("hit", background="#fff59d")
        self.preview_text.tag_configure("current_line", background="#e3f2fd")

        self.preview_text.grid(row=0, column=0, sticky="nsew")
        preview_y.grid(row=0, column=1, sticky="ns")
        preview_x.grid(row=1, column=0, sticky="ew")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        self.query_entry.bind("<Return>", lambda _event: self.search())
        self.result_tree.bind("<<TreeviewSelect>>", self.on_result_selected)
        self.result_tree.bind("<Double-Button-1>", lambda _event: self.open_selected_result())

        self.query_entry.focus_set()
        self.query_entry.selection_range(0, "end")

    def set_status(self, text: str) -> None:
        self.status_var.set(text)
        self.root.update_idletasks()

    def choose_folder(self) -> None:
        current = Path(self.folder_var.get()).expanduser()
        folder = filedialog.askdirectory(initialdir=str(current if current.exists() else BASE_DIR))
        if folder:
            self.folder_var.set(folder)

    def clear_results(self) -> None:
        self.results.clear()
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        self._set_preview_text("")
        self.set_status("検索結果をクリアしました。")

    def _build_matcher(self) -> tuple[re.Pattern[str] | None, str] | None:
        query = self.query_var.get()
        if not query:
            self.set_status("検索ワードを入力してください。")
            return None

        case_sensitive = self.case_sensitive_var.get()
        regex_mode = self.regex_var.get()

        self._last_literal_query = query
        self._last_case_sensitive = case_sensitive
        self._last_regex_mode = regex_mode

        if regex_mode:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                self._last_regex = re.compile(query, flags)
            except re.error as exc:
                messagebox.showerror("正規表現エラー", str(exc))
                return None
            return self._last_regex, query

        self._last_regex = None
        return None, query

    def _line_matches(self, line: str, compiled_regex: re.Pattern[str] | None, literal_query: str) -> bool:
        if compiled_regex is not None:
            return compiled_regex.search(line) is not None

        if self.case_sensitive_var.get():
            return literal_query in line
        return literal_query.casefold() in line.casefold()

    def search(self) -> str:
        matcher = self._build_matcher()
        if matcher is None:
            return "break"

        compiled_regex, literal_query = matcher
        folder = Path(self.folder_var.get()).expanduser()
        if not folder.exists() or not folder.is_dir():
            messagebox.showerror("対象フォルダエラー", f"対象フォルダが見つかりません。\\n\\n{folder}")
            return "break"

        extensions = parse_extensions(self.extensions_var.get())
        if not extensions:
            messagebox.showerror("拡張子エラー", "検索対象の拡張子を1つ以上指定してください。")
            return "break"

        self.results.clear()
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        self._set_preview_text("")

        files = iter_target_files(folder, extensions)
        self.set_status(f"{len(files)} 個のファイルを検索中...")

        stopped_by_limit = False
        for file_index, path in enumerate(files, start=1):
            if file_index % 50 == 0:
                self.set_status(f"検索中... {file_index}/{len(files)} ファイル / {len(self.results)} 件")
            text = read_text_file(path)
            if text is None:
                continue

            for line_no, line in enumerate(text.splitlines(), start=1):
                if self._line_matches(line, compiled_regex, literal_query):
                    self.results.append(SearchResult(path=path, line_no=line_no, line_text=line))
                    if len(self.results) >= MAX_RESULT_COUNT:
                        stopped_by_limit = True
                        break
            if stopped_by_limit:
                break

        self._render_results(folder)
        suffix = "（上限で打ち切り）" if stopped_by_limit else ""
        self.set_status(f"{len(self.results)} 件見つかりました。{suffix}")

        self._save_current_settings()
        return "break"

    def _render_results(self, folder: Path) -> None:
        for index, result in enumerate(self.results):
            try:
                rel_path = result.path.relative_to(folder)
            except ValueError:
                rel_path = result.path
            line = result.line_text.replace("\t", "    ").strip()
            self.result_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(str(rel_path), result.line_no, line),
            )

        if self.results:
            first = self.result_tree.get_children()[0]
            self.result_tree.selection_set(first)
            self.result_tree.focus(first)
            self.result_tree.see(first)

    def _get_selected_result(self) -> SearchResult | None:
        selection = self.result_tree.selection()
        if not selection:
            return None
        try:
            index = int(selection[0])
        except ValueError:
            return None
        if index < 0 or index >= len(self.results):
            return None
        return self.results[index]

    def on_result_selected(self, _event: object | None = None) -> None:
        result = self._get_selected_result()
        if result is None:
            return
        self.show_preview(result)

    def _set_preview_text(self, text: str) -> None:
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        if text:
            self.preview_text.insert("1.0", text)
        self.preview_text.configure(state="disabled")

    def show_preview(self, result: SearchResult) -> None:
        text = read_text_file(result.path)
        if text is None:
            self._set_preview_text("プレビューを読み込めませんでした。")
            return

        lines = text.splitlines()
        target_index = max(0, result.line_no - 1)
        start_index = max(0, target_index - PREVIEW_CONTEXT_LINES)
        end_index = min(len(lines), target_index + PREVIEW_CONTEXT_LINES + 1)

        width = max(4, len(str(end_index)))
        preview_lines: list[str] = []
        current_line_start_char = 0
        current_line_end_char = 0

        char_count = 0
        for index in range(start_index, end_index):
            line_no = index + 1
            marker = ">" if line_no == result.line_no else " "
            rendered = f"{marker}{line_no:>{width}}: {lines[index]}\\n"
            if line_no == result.line_no:
                current_line_start_char = char_count
                current_line_end_char = char_count + len(rendered)
            preview_lines.append(rendered)
            char_count += len(rendered)

        preview = "".join(preview_lines)
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", preview)
        self.preview_text.tag_remove("hit", "1.0", "end")
        self.preview_text.tag_remove("current_line", "1.0", "end")

        if preview:
            self.preview_text.tag_add(
                "current_line",
                f"1.0+{current_line_start_char}c",
                f"1.0+{current_line_end_char}c",
            )
            self._highlight_preview_matches(preview)

        self.preview_text.configure(state="disabled")

    def _highlight_preview_matches(self, preview: str) -> None:
        query = self._last_literal_query
        if not query:
            return

        ranges: list[tuple[int, int]] = []
        if self._last_regex_mode and self._last_regex is not None:
            try:
                for match in self._last_regex.finditer(preview):
                    if match.start() == match.end():
                        continue
                    ranges.append((match.start(), match.end()))
            except re.error:
                return
        else:
            haystack = preview if self._last_case_sensitive else preview.casefold()
            needle = query if self._last_case_sensitive else query.casefold()
            start = 0
            while needle:
                index = haystack.find(needle, start)
                if index < 0:
                    break
                ranges.append((index, index + len(needle)))
                start = index + len(needle)

        for start, end in ranges:
            self.preview_text.tag_add("hit", f"1.0+{start}c", f"1.0+{end}c")

    def open_selected_result(self) -> None:
        result = self._get_selected_result()
        if result is None:
            self.set_status("開く検索結果を選択してください。")
            return

        try:
            open_with_default_app(result.path)
            self.set_status(f"開きました: {result.path} / {result.line_no} 行目")
        except Exception as exc:
            messagebox.showerror("ファイルを開けません", f"{result.path}\\n\\n{exc}")

    def copy_selected_path(self) -> None:
        result = self._get_selected_result()
        if result is None:
            self.set_status("コピーする検索結果を選択してください。")
            return

        text = f"{result.path}:{result.line_no}"
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status(f"コピーしました: {text}")

    def _save_current_settings(self) -> None:
        save_settings(
            {
                "geometry": self.root.geometry(),
                "folder": self.folder_var.get(),
                "extensions": self.extensions_var.get(),
                "case_sensitive": self.case_sensitive_var.get(),
                "regex": self.regex_var.get(),
            }
        )

    def on_close(self) -> None:
        self._save_current_settings()
        self.root.destroy()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Memo Search")
    parser.add_argument("--query", default="", help="起動時に入力する検索ワード")
    parser.add_argument("--folder", default="", help="起動時の検索対象フォルダ")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args(sys.argv[1:])
    initial_folder = Path(args.folder).expanduser() if args.folder else BASE_DIR

    enable_high_dpi_mode()
    root = tk.Tk()
    apply_tk_scaling(root)
    MemoSearchApp(root, initial_folder=initial_folder, initial_query=args.query)
    root.mainloop()


if __name__ == "__main__":
    main()
