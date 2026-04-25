from __future__ import annotations

# Minimal Memo (.pyw 推奨)
# - Python標準ライブラリのみ使用
# - Windows高DPI対策
# - positive.txt / negative.txt を切り替え
# - Ctrl+Tab でメモ切り替え
# - Ctrl+S で保存
# - 5分ごとの自動保存
# - 保存時に前回内容を日付フォルダへバックアップ
# - 選択文字列に Ctrl+Up / Ctrl+Down で重み付け
# - 選択文字列に Ctrl+[ で {text|} 化
# - Ctrl+F で検索（Enter=次, Shift+Enter=前）
# - Ctrl+クリックでカンマ区切りプロンプトを自動選択
# - Ctrl+1..9 で ctrl_1.txt..ctrl_9.txt を挿入
# - Ctrl+マウスホイールでフォントサイズ変更
# - フォントサイズ / ウィンドウ位置を settings.json に保存

from pathlib import Path
import ctypes
import difflib
import json
import re
import sys
from datetime import datetime
import tkinter as tk
from tkinter import messagebox
import tkinter.font as tkfont


APP_TITLE = "Minimal Memo"
BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "settings.json"
MEMO_FILES = [
    BASE_DIR / "positive.txt",
    BASE_DIR / "negative.txt",
]
MEMO_NAMES = ["positive", "negative"]

DEFAULT_FONT_SIZE = 14
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 40
TEXT_PADDING_X = 18
TEXT_PADDING_Y = 16

WEIGHT_STEP = 0.1
MIN_WEIGHT = 0.0
MAX_WEIGHT = 9.9
AUTOSAVE_INTERVAL_MS = 5 * 60 * 1000
MAX_BACKUP_HINT_LEN = 10
WHEEL_SCROLL_LINES = 5
COMMENT_COLOR = "#808080"
CHOICE_BRACE_COLOR = "#1565c0"
CHOICE_PIPE_COLOR = "#7b1fa2"
COMMA_COLOR = "#b26a00"

SHIFT_MASK = 0x0001
CONTROL_MASK = 0x0004
ALT_MASK = 0x0008

WEIGHTED_TEXT_RE = re.compile(r"^\((.*):([0-9]+(?:\.[0-9]+)?)\)$", re.DOTALL)
INVALID_FILENAME_CHARS_RE = re.compile(r'[\\/:*?"<>|\r\n\t]+')
MULTISPACE_RE = re.compile(r"\s+")


def enable_high_dpi_mode() -> None:
    if sys.platform != "win32":
        return

    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def apply_tk_scaling(root: tk.Tk) -> None:
    if sys.platform != "win32":
        return

    dpi = None
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
    except Exception:
        pass

    if dpi is None:
        try:
            dpi = ctypes.windll.user32.GetDpiForWindow(root.winfo_id())
        except Exception:
            dpi = None

    if dpi:
        try:
            root.tk.call("tk", "scaling", float(dpi) / 72.0)
        except Exception:
            pass


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def format_weight(value: float) -> str:
    text = f"{value:.1f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def parse_weighted_text(text: str) -> tuple[str, float] | None:
    match = WEIGHTED_TEXT_RE.match(text)
    if not match:
        return None

    body = match.group(1)
    try:
        weight = float(match.group(2))
    except ValueError:
        return None

    return body, weight


def make_weighted_text(body: str, weight: float) -> str:
    weight = round(clamp(weight, MIN_WEIGHT, MAX_WEIGHT), 1)
    if abs(weight - 1.0) < 1e-9:
        return body
    return f"({body}:{format_weight(weight)})"


def make_choice_text(body: str) -> str:
    return "{" + body + "|}"


def sanitize_backup_hint(text: str) -> str:
    text = text.strip()
    text = INVALID_FILENAME_CHARS_RE.sub("_", text)
    text = MULTISPACE_RE.sub("_", text)
    text = re.sub(r"[^\w-]+", "_", text, flags=re.UNICODE)
    text = text.strip("._-")
    if not text:
        text = "changed"
    return text[:MAX_BACKUP_HINT_LEN]


def clean_fragment_for_backup(fragment: str) -> str:
    fragment = fragment.strip()
    fragment = fragment.lstrip("#").strip()
    fragment = fragment.rstrip(",")
    parsed = parse_weighted_text(fragment)
    if parsed is not None:
        body, _weight = parsed
        fragment = body.strip()
    fragment = fragment.strip("[]{}() ")
    fragment = fragment.strip("._-:|, ")
    return fragment


def extract_prompt_fragment(text: str, position: int) -> str:
    if not text:
        return ""

    position = max(0, min(position, len(text)))

    left = position
    while left > 0 and text[left - 1] not in ",\n":
        left -= 1

    right = position
    while right < len(text) and text[right] not in ",\n":
        right += 1

    fragment = text[left:right]
    return clean_fragment_for_backup(fragment)


def first_changed_fragment(old_text: str, new_text: str) -> str:
    if old_text == new_text:
        return "changed"

    matcher = difflib.SequenceMatcher(a=old_text, b=new_text)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        if j1 != j2:
            fragment = extract_prompt_fragment(new_text, j1)
            if fragment:
                return fragment
            fragment = clean_fragment_for_backup(new_text[j1:j2])
            if fragment:
                return fragment

        if i1 != i2:
            fragment = extract_prompt_fragment(old_text, i1)
            if fragment:
                return fragment
            fragment = clean_fragment_for_backup(old_text[i1:i2])
            if fragment:
                return fragment

    return "changed"


def find_comment_start(line: str) -> int:
    for index, char in enumerate(line):
        if char == "#":
            return index
        if line.startswith("//", index):
            prev = line[index - 1] if index > 0 else ""
            if index == 0 or prev.isspace() or prev in ",([{":
                return index
    return -1


def iter_comment_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    offset = 0

    for line in text.splitlines(keepends=True):
        visible_line = line[:-1] if line.endswith("\n") else line
        comment_start = find_comment_start(visible_line)
        if comment_start >= 0:
            start = offset + comment_start
            end = offset + len(visible_line)
            if end > start:
                ranges.append((start, end))
        offset += len(line)

    return ranges


def iter_literal_ranges(text: str, literal: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    if not literal:
        return ranges

    start = 0
    literal_len = len(literal)
    while True:
        index = text.find(literal, start)
        if index < 0:
            break
        ranges.append((index, index + literal_len))
        start = index + literal_len

    return ranges


def iter_char_ranges(text: str, target: str) -> list[tuple[int, int]]:
    if not target:
        return []
    return [(index, index + 1) for index, char in enumerate(text) if char == target]


def choose_font_spec(root: tk.Tk) -> tuple[str, str]:
    preferred_specs = [
        ("BIZ UDPGothic", "normal"),
        ("BIZ UDGothic", "normal"),
        ("Yu Gothic UI Semibold", "normal"),
        ("Segoe UI Semibold", "normal"),
        ("Meiryo UI", "normal"),
        ("Meiryo", "normal"),
        ("Yu Gothic UI", "normal"),
        ("Segoe UI", "normal"),
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


class SearchDialog:
    def __init__(self, app: "MemoApp") -> None:
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title("検索")
        self.window.transient(app.root)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        frame = tk.Frame(self.window, padx=12, pady=10)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="検索:").grid(row=0, column=0, sticky="w")

        self.entry = tk.Entry(frame, width=36)
        self.entry.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0))

        btn_prev = tk.Button(frame, text="前", width=8, command=self.find_prev)
        btn_prev.grid(row=1, column=1, pady=(10, 0), sticky="e")

        btn_next = tk.Button(frame, text="次", width=8, command=self.find_next)
        btn_next.grid(row=1, column=2, padx=(8, 0), pady=(10, 0), sticky="w")

        btn_close = tk.Button(frame, text="閉じる", width=8, command=self.close)
        btn_close.grid(row=1, column=3, padx=(8, 0), pady=(10, 0), sticky="w")

        self.status_var = tk.StringVar(value="")
        status_label = tk.Label(frame, textvariable=self.status_var, anchor="w")
        status_label.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))

        history_label = tk.Label(frame, text="履歴:")
        history_label.grid(row=3, column=0, sticky="nw", pady=(10, 0))

        history_frame = tk.Frame(frame)
        history_frame.grid(row=3, column=1, columnspan=3, sticky="nsew", padx=(8, 0), pady=(10, 0))

        self.history_listbox = tk.Listbox(history_frame, height=6, width=36, exportselection=False)
        self.history_listbox.pack(side="left", fill="both", expand=True)

        history_scrollbar = tk.Scrollbar(history_frame, orient="vertical", command=self.history_listbox.yview)
        history_scrollbar.pack(side="right", fill="y")
        self.history_listbox.configure(yscrollcommand=history_scrollbar.set)

        frame.columnconfigure(1, weight=1)

        self._history_nav_index: int | None = None
        self._last_query_text = ""
        self._suppress_history_select = False

        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<KP_Enter>", self._on_enter)
        self.entry.bind("<Escape>", self._on_escape)
        self.entry.bind("<Control-z>", self._on_undo_query)
        self.entry.bind("<Control-y>", self._on_redo_query)
        self.entry.bind("<Control-Shift-z>", self._on_redo_query)
        self.entry.bind("<Control-Shift-Z>", self._on_redo_query)
        self.entry.bind("<KeyRelease>", self._on_query_changed)
        self.history_listbox.bind("<<ListboxSelect>>", self._on_history_select)
        self.history_listbox.bind("<Double-Button-1>", self._on_history_activate)
        self.history_listbox.bind("<Return>", self._on_history_activate)
        self.window.bind("<Escape>", self._on_escape)

        current_selection = app.get_selected_text()
        if current_selection:
            self.entry.insert(0, current_selection)

        self._last_query_text = self.entry.get()
        self._refresh_history_list()
        self._sync_history_selection(current_text=self._last_query_text)
        self.focus_entry(select_all=True)
        self._refresh_highlight()

    def _on_enter(self, event: object | None = None) -> str:
        state = int(getattr(event, "state", 0) or 0)
        if state & SHIFT_MASK:
            self.find_prev()
        else:
            self.find_next()
        return "break"

    def _on_escape(self, _event: object | None = None) -> str:
        self.close()
        return "break"

    def _on_query_changed(self, _event: object | None = None) -> None:
        current_text = self.entry.get()
        if current_text == self._last_query_text:
            return
        self._last_query_text = current_text
        self._history_nav_index = None
        self.set_status("")
        self.app.clear_search_highlight()
        self._sync_history_selection(current_text=current_text)
        self._refresh_highlight()

    def _set_entry_text(self, text: str, *, select_all: bool = True) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        self._last_query_text = text
        self._history_nav_index = None
        self.set_status("")
        self.app.clear_search_highlight()
        self._sync_history_selection(current_text=text)
        self._refresh_highlight()
        if select_all:
            self.entry.selection_range(0, "end")
        else:
            self.entry.selection_clear()
        self.entry.icursor("end")

    @property
    def _query_history(self) -> list[str]:
        return self.app.search_query_history

    def _append_history(self, query: str) -> None:
        query = query.strip()
        if not query:
            return
        if self._query_history and self._query_history[-1] == query:
            self._history_nav_index = len(self._query_history) - 1
            self._refresh_history_list()
            return
        self._query_history.append(query)
        if len(self._query_history) > 100:
            del self._query_history[:-100]
        self._history_nav_index = len(self._query_history) - 1
        self.app._save_settings()
        self._refresh_history_list()

    def _refresh_history_list(self) -> None:
        current_text = self.entry.get()
        self._suppress_history_select = True
        try:
            self.history_listbox.delete(0, "end")
            for query in reversed(self._query_history):
                self.history_listbox.insert("end", query)
            self._sync_history_selection(current_text=current_text)
        finally:
            self._suppress_history_select = False

    def _latest_history_index(self, query: str) -> int | None:
        if not query:
            return None
        try:
            return len(self._query_history) - 1 - self._query_history[::-1].index(query)
        except ValueError:
            return None

    def _sync_history_selection(self, query: str = "", history_index: int | None = None, *, current_text: str | None = None) -> None:
        self.history_listbox.selection_clear(0, "end")
        if history_index is None:
            if current_text is None:
                current_text = query
            history_index = self._latest_history_index(current_text)
        if history_index is None:
            return
        index = len(self._query_history) - 1 - history_index
        if index < 0 or index >= self.history_listbox.size():
            return
        self.history_listbox.selection_set(index)
        self.history_listbox.see(index)

    def _get_selected_history_query(self) -> str | None:
        selection = self.history_listbox.curselection()
        if not selection:
            return None
        list_index = int(selection[0])
        history_index = len(self._query_history) - 1 - list_index
        if history_index < 0 or history_index >= len(self._query_history):
            return None
        return self._query_history[history_index]

    def _on_history_select(self, _event: object | None = None) -> None:
        if self._suppress_history_select:
            return
        selection = self.history_listbox.curselection()
        if not selection:
            return
        list_index = int(selection[0])
        history_index = len(self._query_history) - 1 - list_index
        if history_index < 0 or history_index >= len(self._query_history):
            return
        query = self._query_history[history_index]
        if query == self.entry.get():
            self._history_nav_index = history_index
            return
        self._apply_history_query(history_index, select_all=True)
        self.entry.focus_set()

    def _on_history_activate(self, _event: object | None = None) -> str:
        selection = self.history_listbox.curselection()
        if not selection:
            return "break"
        list_index = int(selection[0])
        history_index = len(self._query_history) - 1 - list_index
        if history_index < 0 or history_index >= len(self._query_history):
            return "break"
        self._apply_history_query(history_index, select_all=True)
        self.find_next()
        return "break"

    def _apply_history_query(self, history_index: int, *, select_all: bool = True) -> None:
        if history_index < 0 or history_index >= len(self._query_history):
            return
        query = self._query_history[history_index]
        self._set_entry_text(query, select_all=select_all)
        self._history_nav_index = history_index
        self._sync_history_selection(history_index=history_index)

    def _on_undo_query(self, _event: object | None = None) -> str:
        if not self._query_history:
            return "break"
        if self._history_nav_index is None:
            current_text = self.entry.get().strip()
            if current_text in self._query_history:
                self._history_nav_index = self._latest_history_index(current_text)
            else:
                self._history_nav_index = len(self._query_history)
        target_index = max(0, self._history_nav_index - 1)
        if self._history_nav_index == len(self._query_history):
            target_index = len(self._query_history) - 1
        self._apply_history_query(target_index, select_all=True)
        return "break"

    def _on_redo_query(self, _event: object | None = None) -> str:
        if not self._query_history:
            return "break"
        if self._history_nav_index is None:
            current_text = self.entry.get().strip()
            if current_text in self._query_history:
                self._history_nav_index = self._latest_history_index(current_text)
            else:
                self._history_nav_index = len(self._query_history)
        if self._history_nav_index >= len(self._query_history) - 1:
            return "break"
        target_index = self._history_nav_index + 1
        self._apply_history_query(target_index, select_all=True)
        return "break"

    def _refresh_highlight(self) -> None:
        self.app.highlight_search_all(self.entry.get())

    def focus_entry(self, select_all: bool = False) -> None:
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        self.entry.focus_set()
        if select_all:
            self.entry.selection_range(0, "end")
        else:
            self.entry.selection_clear()
            self.entry.icursor("end")

    def restore_focus_after_search(self) -> None:
        self.window.after_idle(self.focus_entry)

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def find_next(self) -> None:
        query = self.entry.get().strip()
        self._append_history(query)
        result, wrapped = self.app.find_next(query, keep_dialog_focus=True)
        if not result:
            self.set_status("見つかりません")
        elif wrapped:
            self.set_status("先頭から再検索")
        else:
            self.set_status("")

    def find_prev(self) -> None:
        query = self.entry.get().strip()
        self._append_history(query)
        result, wrapped = self.app.find_prev(query, keep_dialog_focus=True)
        if not result:
            self.set_status("見つかりません")
        elif wrapped:
            self.set_status("末尾から再検索")
        else:
            self.set_status("")

    def close(self) -> None:
        self.app.clear_search_highlight()
        self.window.destroy()
        self.app.search_dialog = None
        self.app._get_current_editor().focus_set()


class WideGripScrollbar(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        *,
        width: int,
        command=None,
        trough_color: str = "#e3e3e3",
        thumb_color: str = "#8b8b8b",
        thumb_active_color: str = "#767676",
    ) -> None:
        super().__init__(
            master,
            width=width,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            bg=trough_color,
            cursor="hand2",
        )
        self.command = command
        self.trough_color = trough_color
        self.thumb_color = thumb_color
        self.thumb_active_color = thumb_active_color
        self._first = 0.0
        self._last = 1.0
        self._drag_offset_y: float | None = None
        self._thumb_top = 0.0
        self._thumb_bottom = 0.0
        self._thumb_hover = False

        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_button_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_button_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def config(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        return super().config(**kwargs)

    configure = config

    def set(self, first: str | float, last: str | float) -> None:
        try:
            self._first = max(0.0, min(1.0, float(first)))
            self._last = max(0.0, min(1.0, float(last)))
        except Exception:
            self._first = 0.0
            self._last = 1.0
        self._redraw()

    def _get_thumb_metrics(self) -> tuple[int, int, int, int, int]:
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        pad_x = max(1, width // 32)
        pad_y = max(2, width // 12)
        track_top = pad_y
        track_bottom = max(track_top + 1, height - pad_y)
        track_height = max(1, track_bottom - track_top)
        visible_fraction = max(0.0, min(1.0, self._last - self._first))
        thumb_height = max(36, int(round(track_height * visible_fraction)))
        thumb_height = min(track_height, thumb_height)
        movable = max(0, track_height - thumb_height)
        thumb_top = track_top + int(round(movable * self._first))
        thumb_bottom = thumb_top + thumb_height
        if thumb_bottom > track_bottom:
            thumb_bottom = track_bottom
            thumb_top = thumb_bottom - thumb_height
        return pad_x, track_top, width - pad_x, track_bottom, thumb_top

    def _redraw(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        if width <= 1 or height <= 1:
            return

        pad_x, track_top, track_right, track_bottom, thumb_top = self._get_thumb_metrics()
        track_left = pad_x
        track_width = max(1, track_right - track_left)
        track_height = max(1, track_bottom - track_top)
        visible_fraction = max(0.0, min(1.0, self._last - self._first))
        thumb_height = max(36, int(round(track_height * visible_fraction)))
        thumb_height = min(track_height, thumb_height)
        thumb_bottom = min(track_bottom, thumb_top + thumb_height)

        self.create_rectangle(
            track_left,
            track_top,
            track_right,
            track_bottom,
            fill=self.trough_color,
            outline="",
        )
        thumb_color = self.thumb_active_color if self._thumb_hover or self._drag_offset_y is not None else self.thumb_color
        self.create_rectangle(
            track_left,
            thumb_top,
            track_right,
            thumb_bottom,
            fill=thumb_color,
            outline="",
            width=0,
        )
        self._thumb_top = float(thumb_top)
        self._thumb_bottom = float(thumb_bottom)

    def _moveto_from_y(self, y: float, center: bool = False) -> None:
        if self.command is None:
            return
        pad_x, track_top, _track_right, track_bottom, thumb_top = self._get_thumb_metrics()
        _ = pad_x, thumb_top
        track_height = max(1, track_bottom - track_top)
        visible_fraction = max(0.0, min(1.0, self._last - self._first))
        thumb_height = max(36, int(round(track_height * visible_fraction)))
        thumb_height = min(track_height, thumb_height)
        movable = max(1, track_height - thumb_height)
        desired_top = y - (thumb_height / 2.0 if center else (self._drag_offset_y or 0.0))
        fraction = (desired_top - track_top) / movable
        fraction = max(0.0, min(1.0, fraction))
        self.command("moveto", fraction)

    def _on_configure(self, _event: tk.Event) -> None:
        self._redraw()

    def _on_button_press(self, event: tk.Event) -> str:
        y = float(event.y)
        if self._thumb_top <= y <= self._thumb_bottom:
            self._drag_offset_y = y - self._thumb_top
        else:
            self._drag_offset_y = None
            self._moveto_from_y(y, center=True)
        self._thumb_hover = True
        self._redraw()
        return "break"

    def _on_drag(self, event: tk.Event) -> str:
        self._moveto_from_y(float(event.y), center=False)
        self._thumb_hover = True
        self._redraw()
        return "break"

    def _on_button_release(self, _event: tk.Event) -> str:
        self._drag_offset_y = None
        self._redraw()
        return "break"

    def _on_enter(self, _event: tk.Event) -> None:
        self._thumb_hover = True
        self._redraw()

    def _on_leave(self, _event: tk.Event) -> None:
        if self._drag_offset_y is None:
            self._thumb_hover = False
            self._redraw()


class MemoApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(520, 360)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.settings = self._load_settings()
        self.font_size = int(clamp(float(self.settings.get("font_size", DEFAULT_FONT_SIZE)), MIN_FONT_SIZE, MAX_FONT_SIZE))
        self.font_family, self.font_weight = choose_font_spec(root)
        self.text_font = tkfont.Font(family=self.font_family, size=self.font_size, weight=self.font_weight)

        self._set_initial_geometry()

        self.editor_frames: list[tk.Frame] = []
        self.text_widgets: list[tk.Text] = []
        self.scrollbars: list[WideGripScrollbar] = []
        self._last_saved_texts: list[str] = ["" for _ in MEMO_FILES]
        self.current_index = 0
        self.search_dialog: SearchDialog | None = None
        self._selection_from_search = False
        self._search_query = ""
        self._search_current_span: tuple[str, str] | None = None
        raw_search_history = self.settings.get("search_history", [])
        if isinstance(raw_search_history, list):
            self.search_query_history = [str(x) for x in raw_search_history if str(x).strip()]
        else:
            self.search_query_history = []

        self._ensure_storage()
        self._build_ui()
        self._load_all_memos()
        self._show_current_editor()
        self._update_title()
        self._schedule_autosave()

    def _load_settings(self) -> dict:
        if not SETTINGS_FILE.exists():
            return {}
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_settings(self) -> None:
        data = {
            "font_size": self.font_size,
            "geometry": self.root.geometry(),
            "search_history": self.search_query_history[-100:],
        }
        try:
            SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _set_initial_geometry(self) -> None:
        saved_geometry = self.settings.get("geometry")
        if isinstance(saved_geometry, str) and saved_geometry:
            try:
                self.root.geometry(saved_geometry)
                return
            except Exception:
                pass

        screen_w = max(1, self.root.winfo_screenwidth())
        screen_h = max(1, self.root.winfo_screenheight())
        width = min(1900, max(1100, int(screen_w * 0.42)))
        height = min(1500, max(820, int(screen_h * 0.68)))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")


    def _get_scrollbar_width(self) -> int:
        """Return a DPI-aware scrollbar width.

        Tk's classic Scrollbar width is in logical pixels. On high-DPI Windows,
        leaving it at the default lets Tk/Windows scale it automatically, but
        forcing a small fixed width like 28 can actually make it look *smaller*
        than before. So we scale from a 100%% baseline.
        """
        try:
            scaling = float(self.root.tk.call("tk", "scaling"))
        except Exception:
            scaling = 96.0 / 72.0

        base_scaling = 96.0 / 72.0  # Tk scaling at 100% / 96 DPI
        ui_scale = max(1.0, scaling / base_scaling)

        # Intentionally very wide for high-DPI use.
        return max(56, int(round(48 * ui_scale)))

    def _build_ui(self) -> None:
        container = tk.Frame(self.root, borderwidth=0, highlightthickness=0)
        container.pack(fill="both", expand=True)

        for _index in range(len(MEMO_FILES)):
            frame = tk.Frame(container, borderwidth=0, highlightthickness=0)
            frame.place(relx=0, rely=0, relwidth=1, relheight=1)

            scrollbar = WideGripScrollbar(frame, width=self._get_scrollbar_width())
            scrollbar.pack(side="right", fill="y")

            editor = tk.Text(
                frame,
                undo=True,
                exportselection=False,
                wrap="word",
                font=self.text_font,
                padx=TEXT_PADDING_X,
                pady=TEXT_PADDING_Y,
                borderwidth=0,
                highlightthickness=0,
                relief="flat",
                insertborderwidth=0,
                spacing1=1,
                spacing2=0,
                spacing3=1,
                yscrollcommand=scrollbar.set,
            )
            editor.pack(side="left", fill="both", expand=True)
            scrollbar.config(command=editor.yview)

            self._bind_editor_shortcuts(editor)

            editor.tag_configure("comma", foreground=COMMA_COLOR)
            editor.tag_configure("choice_brace", foreground=CHOICE_BRACE_COLOR)
            editor.tag_configure("choice_pipe", foreground=CHOICE_PIPE_COLOR)
            editor.tag_configure("comment", foreground=COMMENT_COLOR)
            editor.tag_configure("search_all", background="#fff59d")
            editor.tag_configure("search_current", background="#ffb74d")

            self.editor_frames.append(frame)
            self.text_widgets.append(editor)
            self.scrollbars.append(scrollbar)

        self._bind_root_shortcuts()

    def _bind_editor_shortcuts(self, editor: tk.Text) -> None:
        editor.bind("<Control-s>", self._on_ctrl_s)
        editor.bind("<Control-Tab>", self._on_ctrl_tab)
        editor.bind("<Control-ISO_Left_Tab>", self._on_ctrl_shift_tab)
        editor.bind("<Control-Shift-Tab>", self._on_ctrl_shift_tab)
        editor.bind("<Control-Up>", self._on_ctrl_up)
        editor.bind("<Control-Down>", self._on_ctrl_down)
        editor.bind("<Control-bracketleft>", self._on_ctrl_bracketleft)
        editor.bind("<Control-f>", self._on_ctrl_f)
        editor.bind("<Control-Left>", self._on_ctrl_left)
        editor.bind("<Control-Right>", self._on_ctrl_right)
        editor.bind("<Left>", self._on_left_key)
        editor.bind("<Right>", self._on_right_key)
        editor.bind("<KeyPress-bracketleft>", self._on_bracketleft)
        editor.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel)
        editor.bind("<MouseWheel>", self._on_mousewheel)
        editor.bind("<Control-Button-1>", self._on_ctrl_click)
        editor.bind("<Button-4>", self._on_wheel_up)
        editor.bind("<Button-5>", self._on_wheel_down)
        editor.bind("<Control-Button-4>", self._on_ctrl_wheel_up)
        editor.bind("<Control-Button-5>", self._on_ctrl_wheel_down)
        editor.bind("<KeyRelease>", self._on_content_maybe_changed)
        editor.bind("<<Paste>>", self._on_content_maybe_changed)
        editor.bind("<<Cut>>", self._on_content_maybe_changed)
        editor.bind("<<Undo>>", self._on_content_maybe_changed)
        editor.bind("<<Redo>>", self._on_content_maybe_changed)

        for digit in range(1, 10):
            editor.bind(f"<Control-Key-{digit}>", lambda event, n=digit: self._on_ctrl_preset(event, n))

    def _bind_root_shortcuts(self) -> None:
        self.root.bind("<Control-s>", self._on_ctrl_s)
        self.root.bind("<Control-Tab>", self._on_ctrl_tab)
        self.root.bind("<Control-ISO_Left_Tab>", self._on_ctrl_shift_tab)
        self.root.bind("<Control-Shift-Tab>", self._on_ctrl_shift_tab)
        self.root.bind("<Control-Up>", self._on_ctrl_up)
        self.root.bind("<Control-Down>", self._on_ctrl_down)
        self.root.bind("<Control-bracketleft>", self._on_ctrl_bracketleft)
        self.root.bind("<Control-f>", self._on_ctrl_f)
        self.root.bind("<Control-Left>", self._on_ctrl_left)
        self.root.bind("<Control-Right>", self._on_ctrl_right)
        self.root.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel)
        self.root.bind("<Control-Button-4>", self._on_ctrl_wheel_up)
        self.root.bind("<Control-Button-5>", self._on_ctrl_wheel_down)

        for digit in range(1, 10):
            self.root.bind(f"<Control-Key-{digit}>", lambda event, n=digit: self._on_ctrl_preset(event, n))

    def _ensure_storage(self) -> None:
        for path in MEMO_FILES:
            if not path.exists():
                path.write_text("", encoding="utf-8")

    def _load_all_memos(self) -> None:
        for index, path in enumerate(MEMO_FILES):
            try:
                content = path.read_text(encoding="utf-8")
            except Exception as exc:
                messagebox.showerror("読み込みエラー", f"{path.name} を開けませんでした。\n\n{exc}")
                content = ""

            editor = self.text_widgets[index]
            editor.delete("1.0", "end")
            editor.insert("1.0", content)
            editor.edit_reset()
            self._refresh_syntax_highlight(editor)
            self._last_saved_texts[index] = content

    def _show_current_editor(self) -> None:
        for index, frame in enumerate(self.editor_frames):
            if index == self.current_index:
                frame.lift()
        current_editor = self._get_current_editor()
        self._refresh_syntax_highlight(current_editor)
        current_editor.focus_set()
        if self.search_dialog is not None:
            self.search_dialog._refresh_highlight()

    def _get_current_editor(self) -> tk.Text:
        return self.text_widgets[self.current_index]

    def _get_editor_text(self, index: int) -> str:
        return self.text_widgets[index].get("1.0", "end-1c")

    def _is_modified(self, index: int) -> bool:
        return self._get_editor_text(index) != self._last_saved_texts[index]

    def _refresh_syntax_highlight(self, editor: tk.Text) -> None:
        for tag_name in ("comma", "choice_brace", "choice_pipe", "comment"):
            editor.tag_remove(tag_name, "1.0", "end")

        content = editor.get("1.0", "end-1c")
        for start_offset, end_offset in iter_char_ranges(content, ","):
            start = f"1.0+{start_offset}c"
            end = f"1.0+{end_offset}c"
            editor.tag_add("comma", start, end)

        for symbol in ("{", "}"):
            for start_offset, end_offset in iter_char_ranges(content, symbol):
                start = f"1.0+{start_offset}c"
                end = f"1.0+{end_offset}c"
                editor.tag_add("choice_brace", start, end)

        for start_offset, end_offset in iter_char_ranges(content, "|"):
            start = f"1.0+{start_offset}c"
            end = f"1.0+{end_offset}c"
            editor.tag_add("choice_pipe", start, end)

        for start_offset, end_offset in iter_comment_ranges(content):
            start = f"1.0+{start_offset}c"
            end = f"1.0+{end_offset}c"
            editor.tag_add("comment", start, end)

    def _refresh_current_syntax_highlight(self) -> None:
        self._refresh_syntax_highlight(self._get_current_editor())

    def _has_active_search_selection(self, editor: tk.Text) -> bool:
        if not self._selection_from_search:
            return False

        current_span = self._get_current_search_span(editor)
        if current_span is None:
            self._selection_from_search = False
            return False

        try:
            selection = (editor.index("sel.first"), editor.index("sel.last"))
        except tk.TclError:
            self._selection_from_search = False
            return False

        if selection != current_span:
            self._selection_from_search = False
            return False

        return True

    def _collapse_selection(self, *, to_end: bool) -> str | None:
        editor = self._get_current_editor()
        try:
            selection_start = editor.index("sel.first")
            selection_end = editor.index("sel.last")
        except tk.TclError:
            self._selection_from_search = False
            return None

        current_span = self._get_current_search_span(editor)
        is_search_selection = bool(
            self._selection_from_search
            and current_span is not None
            and (selection_start, selection_end) == current_span
        )

        editor.tag_remove("sel", "1.0", "end")
        editor.mark_set("insert", selection_end if to_end else selection_start)
        editor.see("insert")

        if is_search_selection:
            self._selection_from_search = False
        return "break"

    def get_selected_text(self) -> str:
        editor = self._get_current_editor()
        try:
            return editor.get("sel.first", "sel.last")
        except tk.TclError:
            return ""

    def _schedule_autosave(self) -> None:
        self.root.after(AUTOSAVE_INTERVAL_MS, self._autosave_tick)

    def _autosave_tick(self) -> None:
        try:
            self._save_all_changed(show_error=False)
        finally:
            self._schedule_autosave()

    def _make_backup_path(self, index: int, old_text: str, new_text: str) -> Path:
        now = datetime.now()
        date_part = now.strftime("%Y%m%d")
        time_part = now.strftime("%Y%m%d_%H%M")
        changed_hint = sanitize_backup_hint(first_changed_fragment(old_text, new_text))
        folder = BASE_DIR / date_part
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{MEMO_NAMES[index]}_{time_part}_{changed_hint}.txt"

    def _save_index(self, index: int, show_error: bool) -> bool:
        current_text = self._get_editor_text(index)
        old_text = self._last_saved_texts[index]

        if current_text == old_text:
            return True

        path = MEMO_FILES[index]
        try:
            if old_text:
                backup_path = self._make_backup_path(index, old_text, current_text)
                backup_path.write_text(old_text, encoding="utf-8")
            path.write_text(current_text, encoding="utf-8")
        except Exception as exc:
            if show_error:
                messagebox.showerror("保存エラー", f"{path.name} を保存できませんでした。\n\n{exc}")
            return False

        self._last_saved_texts[index] = current_text
        self._update_title()
        return True

    def _save_all_changed(self, show_error: bool) -> bool:
        for index in range(len(MEMO_FILES)):
            if self._is_modified(index):
                if not self._save_index(index, show_error=show_error):
                    return False
        return True

    def _update_title(self) -> None:
        dirty_mark = " *" if self._is_modified(self.current_index) else ""
        self.root.title(f"{APP_TITLE} - {MEMO_NAMES[self.current_index]}{dirty_mark}")

    def _switch_to(self, new_index: int) -> None:
        self.current_index = new_index
        self._search_current_span = None
        self._selection_from_search = False
        self._show_current_editor()
        self._update_title()

    def _replace_range(
        self,
        editor: tk.Text,
        start: str,
        end: str,
        replacement: str,
        *,
        select_inserted: bool,
        cursor_offset: int | None = None,
    ) -> None:
        editor.edit_separator()
        editor.replace(start, end, replacement)
        editor.edit_separator()

        new_end = editor.index(f"{start}+{len(replacement)}c")
        editor.tag_remove("sel", "1.0", "end")
        if select_inserted:
            editor.tag_add("sel", start, new_end)
            editor.mark_set("insert", new_end)
        elif cursor_offset is not None:
            editor.mark_set("insert", f"{start}+{cursor_offset}c")
        else:
            editor.mark_set("insert", new_end)

        editor.see("insert")
        self._selection_from_search = False
        self._refresh_syntax_highlight(editor)
        self._update_title()
        if self.search_dialog is not None:
            self.search_dialog._refresh_highlight()

    def _replace_selection_or_insert(self, replacement: str, select_inserted: bool = False) -> None:
        editor = self._get_current_editor()
        try:
            start = editor.index("sel.first")
            end = editor.index("sel.last")
        except tk.TclError:
            start = editor.index("insert")
            end = start
        self._replace_range(editor, start, end, replacement, select_inserted=select_inserted)

    def _adjust_selected_weight(self, delta: float) -> None:
        editor = self._get_current_editor()
        try:
            start = editor.index("sel.first")
            end = editor.index("sel.last")
        except tk.TclError:
            return

        selected_text = editor.get(start, end)
        parsed = parse_weighted_text(selected_text)
        if parsed is None:
            body = selected_text
            current_weight = 1.0
        else:
            body, current_weight = parsed

        new_weight = round(clamp(current_weight + delta, MIN_WEIGHT, MAX_WEIGHT), 1)
        replacement = make_weighted_text(body, new_weight)
        self._replace_range(editor, start, end, replacement, select_inserted=True)

    def _wrap_selected_choice(self) -> None:
        editor = self._get_current_editor()
        try:
            start = editor.index("sel.first")
            end = editor.index("sel.last")
            selected_text = editor.get(start, end)
            replacement = make_choice_text(selected_text)
            self._replace_range(editor, start, end, replacement, select_inserted=True)
        except tk.TclError:
            insert = editor.index("insert")
            self._replace_range(editor, insert, insert, "{|}", select_inserted=False, cursor_offset=1)

    def _insert_preset(self, slot: int) -> None:
        preset_path = BASE_DIR / f"ctrl_{slot}.txt"
        if not preset_path.exists():
            return
        try:
            content = preset_path.read_text(encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("読み込みエラー", f"{preset_path.name} を開けませんでした。\n\n{exc}")
            return
        self._replace_selection_or_insert(content, select_inserted=False)

    def _change_font_size(self, delta: int) -> str:
        new_size = int(clamp(self.font_size + delta, MIN_FONT_SIZE, MAX_FONT_SIZE))
        if new_size != self.font_size:
            self.font_size = new_size
            self.text_font.configure(size=self.font_size)
            self._save_settings()
        return "break"

    def _iter_prompt_ranges(self, text: str) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        offset = 0

        for chunk in re.split(r"([,\n])", text):
            if chunk in {",", "\n"}:
                offset += len(chunk)
                continue

            raw_start = offset
            raw_end = offset + len(chunk)
            start = raw_start
            end = raw_end

            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1

            if start < end:
                ranges.append((start, end))

            offset = raw_end

        return ranges

    def _offset_to_index(self, offset: int) -> str:
        return f"1.0+{offset}c"

    def _index_to_offset(self, index: str) -> int:
        editor = self._get_current_editor()
        return len(editor.get("1.0", index))

    def _select_prompt_offsets(self, start_offset: int, end_offset: int) -> None:
        editor = self._get_current_editor()
        start = self._offset_to_index(start_offset)
        end = self._offset_to_index(end_offset)
        editor.tag_remove("sel", "1.0", "end")
        editor.tag_add("sel", start, end)
        editor.mark_set("insert", end)
        editor.see(start)
        editor.focus_set()
        self._selection_from_search = False

    def _select_prompt_at_index(self, text_index: str) -> None:
        editor = self._get_current_editor()
        content = editor.get("1.0", "end-1c")
        if not content:
            return

        offset = self._index_to_offset(text_index)
        ranges = self._iter_prompt_ranges(content)
        if not ranges:
            return

        for start_offset, end_offset in ranges:
            if start_offset <= offset < end_offset:
                self._select_prompt_offsets(start_offset, end_offset)
                return

        if offset == len(content):
            start_offset, end_offset = ranges[-1]
            self._select_prompt_offsets(start_offset, end_offset)
            return

        for start_offset, end_offset in ranges:
            if offset < start_offset:
                self._select_prompt_offsets(start_offset, end_offset)
                return

    def _select_adjacent_prompt(self, direction: int) -> None:
        editor = self._get_current_editor()
        content = editor.get("1.0", "end-1c")
        ranges = self._iter_prompt_ranges(content)
        if not ranges:
            return

        try:
            selection_start = self._index_to_offset(editor.index("sel.first"))
            selection_end = self._index_to_offset(editor.index("sel.last"))
            has_selection = True
        except tk.TclError:
            has_selection = False
            selection_start = selection_end = -1

        if not has_selection:
            self._select_prompt_at_index(editor.index("insert"))
            return

        current_idx = None
        for idx, (start_offset, end_offset) in enumerate(ranges):
            if start_offset == selection_start and end_offset == selection_end:
                current_idx = idx
                break

        if current_idx is None:
            anchor = selection_start if direction < 0 else selection_end
            for idx, (start_offset, end_offset) in enumerate(ranges):
                if start_offset <= anchor < end_offset or (anchor == end_offset and direction < 0):
                    current_idx = idx
                    break
            if current_idx is None:
                current_idx = len(ranges) - 1 if direction < 0 else 0

        next_idx = current_idx + direction
        if 0 <= next_idx < len(ranges):
            self._select_prompt_offsets(*ranges[next_idx])

    def clear_search_highlight(self) -> None:
        self._selection_from_search = False
        self._search_query = ""
        self._search_current_span = None
        for editor in self.text_widgets:
            editor.tag_remove("search_all", "1.0", "end")
            editor.tag_remove("search_current", "1.0", "end")

    def highlight_search_all(self, query: str) -> None:
        for text_widget in self.text_widgets:
            text_widget.tag_remove("search_all", "1.0", "end")

        if not query:
            return

        editor = self._get_current_editor()
        start = "1.0"
        while True:
            match_start = editor.search(query, start, stopindex="end", nocase=True)
            if not match_start:
                break
            match_end = f"{match_start}+{len(query)}c"
            editor.tag_add("search_all", match_start, match_end)
            start = match_end

    def _get_current_search_span(self, editor: tk.Text) -> tuple[str, str] | None:
        if self._search_current_span is not None:
            return self._search_current_span
        ranges = editor.tag_ranges("search_current")
        if len(ranges) >= 2:
            return str(ranges[0]), str(ranges[1])
        return None

    def _find_from(self, query: str, backwards: bool, keep_dialog_focus: bool = False) -> tuple[bool, bool]:
        editor = self._get_current_editor()
        if not query:
            self._search_query = ""
            self._search_current_span = None
            editor.tag_remove("search_current", "1.0", "end")
            self._selection_from_search = False
            return False, False

        current_span = self._search_current_span if self._search_query == query else None
        editor.tag_remove("search_current", "1.0", "end")
        self._selection_from_search = False

        wrapped = False

        if backwards:
            if current_span is not None:
                start_index = editor.index(f"{current_span[0]} - 1c")
            else:
                start_index = editor.index("insert")
            match_start = editor.search(query, start_index, stopindex="1.0", backwards=True, nocase=True)
            if not match_start:
                match_start = editor.search(query, "end - 1c", stopindex="1.0", backwards=True, nocase=True)
                wrapped = bool(match_start)
        else:
            if current_span is not None:
                start_index = current_span[1]
            else:
                start_index = editor.index("insert")
            match_start = editor.search(query, start_index, stopindex="end", nocase=True)
            if not match_start:
                match_start = editor.search(query, "1.0", stopindex="end", nocase=True)
                wrapped = bool(match_start)

        if not match_start:
            self._search_query = query
            self._search_current_span = None
            if keep_dialog_focus and self.search_dialog is not None:
                self.search_dialog.restore_focus_after_search()
            return False, False

        match_end = f"{match_start}+{len(query)}c"
        editor.tag_remove("sel", "1.0", "end")
        editor.tag_add("sel", match_start, match_end)
        editor.tag_add("search_current", match_start, match_end)
        editor.mark_set("insert", match_start)
        editor.see(match_start)
        self._selection_from_search = True
        self._search_query = query
        self._search_current_span = (editor.index(match_start), editor.index(match_end))

        if keep_dialog_focus and self.search_dialog is not None:
            self.search_dialog.restore_focus_after_search()
        else:
            editor.focus_set()

        return True, wrapped

    def find_next(self, query: str, keep_dialog_focus: bool = False) -> tuple[bool, bool]:
        self.highlight_search_all(query)
        return self._find_from(query, backwards=False, keep_dialog_focus=keep_dialog_focus)

    def find_prev(self, query: str, keep_dialog_focus: bool = False) -> tuple[bool, bool]:
        self.highlight_search_all(query)
        return self._find_from(query, backwards=True, keep_dialog_focus=keep_dialog_focus)

    def _scroll_current_editor(self, direction: int, units: int = WHEEL_SCROLL_LINES) -> str:
        editor = self._get_current_editor()
        editor.yview_scroll(direction * units, "units")
        return "break"

    def _on_ctrl_s(self, _event: object | None = None) -> str:
        self._save_current(show_error=True)
        return "break"

    def _save_current(self, show_error: bool) -> bool:
        return self._save_index(self.current_index, show_error=show_error)

    def _on_ctrl_tab(self, _event: object | None = None) -> str:
        self._switch_to((self.current_index + 1) % len(self.text_widgets))
        return "break"

    def _on_ctrl_shift_tab(self, _event: object | None = None) -> str:
        self._switch_to((self.current_index - 1) % len(self.text_widgets))
        return "break"

    def _on_ctrl_up(self, _event: object | None = None) -> str:
        self._adjust_selected_weight(+WEIGHT_STEP)
        return "break"

    def _on_ctrl_down(self, _event: object | None = None) -> str:
        self._adjust_selected_weight(-WEIGHT_STEP)
        return "break"

    def _on_ctrl_bracketleft(self, _event: object | None = None) -> str:
        self._wrap_selected_choice()
        return "break"

    def _on_ctrl_f(self, _event: object | None = None) -> str:
        if self.search_dialog is None:
            self.search_dialog = SearchDialog(self)
        else:
            self.search_dialog.focus_entry(select_all=True)
        return "break"

    def _on_ctrl_left(self, _event: object | None = None) -> str:
        self._select_adjacent_prompt(-1)
        return "break"

    def _on_ctrl_right(self, _event: object | None = None) -> str:
        self._select_adjacent_prompt(+1)
        return "break"

    def _should_preserve_default_arrow_behavior(self, event: object | None) -> bool:
        if not isinstance(event, tk.Event):
            return False
        state = int(getattr(event, "state", 0) or 0)
        return bool(state & (SHIFT_MASK | CONTROL_MASK | ALT_MASK))

    def _on_left_key(self, event: object | None = None) -> str | None:
        if self._should_preserve_default_arrow_behavior(event):
            return None
        return self._collapse_selection(to_end=False)

    def _on_right_key(self, event: object | None = None) -> str | None:
        if self._should_preserve_default_arrow_behavior(event):
            return None
        return self._collapse_selection(to_end=True)

    def _on_bracketleft(self, _event: object | None = None) -> str | None:
        editor = self._get_current_editor()
        try:
            editor.index("sel.first")
            editor.index("sel.last")
        except tk.TclError:
            return None
        self._wrap_selected_choice()
        return "break"

    def _on_ctrl_preset(self, _event: object | None, slot: int) -> str:
        self._insert_preset(slot)
        return "break"

    def _on_ctrl_mousewheel(self, event: tk.Event) -> str:
        delta = 1 if event.delta > 0 else -1
        return self._change_font_size(delta)

    def _on_mousewheel(self, event: tk.Event) -> str:
        if event.delta == 0:
            return "break"
        clicks = max(1, abs(int(event.delta)) // 120)
        direction = -1 if event.delta > 0 else 1
        return self._scroll_current_editor(direction, units=clicks * WHEEL_SCROLL_LINES)

    def _on_wheel_up(self, _event: object | None = None) -> str:
        return self._scroll_current_editor(-1)

    def _on_wheel_down(self, _event: object | None = None) -> str:
        return self._scroll_current_editor(+1)

    def _on_ctrl_wheel_up(self, _event: object | None = None) -> str:
        return self._change_font_size(+1)

    def _on_ctrl_wheel_down(self, _event: object | None = None) -> str:
        return self._change_font_size(-1)

    def _on_ctrl_click(self, event: tk.Event) -> str:
        editor = self._get_current_editor()
        index = editor.index(f"@{event.x},{event.y}")
        self._select_prompt_at_index(index)
        return "break"

    def _on_content_maybe_changed(self, event: object | None = None) -> None:
        self._selection_from_search = False
        self._search_current_span = None
        editor = self._get_current_editor()
        if isinstance(event, tk.Event) and getattr(event, "widget", None) in self.text_widgets:
            editor = event.widget
        self._refresh_syntax_highlight(editor)
        self._update_title()
        if self.search_dialog is not None:
            self.search_dialog._refresh_highlight()

    def on_close(self) -> None:
        dirty_indexes = [i for i in range(len(self.text_widgets)) if self._is_modified(i)]
        if not dirty_indexes:
            self._save_settings()
            self.root.destroy()
            return

        names = ", ".join(MEMO_FILES[i].name for i in dirty_indexes)
        answer = messagebox.askyesnocancel(
            "保存の確認",
            f"未保存の変更があります。\n保存対象: {names}\n\n保存してから閉じますか？",
        )

        if answer is None:
            return

        if answer is True:
            for index in dirty_indexes:
                if not self._save_index(index, show_error=True):
                    return

        self._save_settings()
        self.root.destroy()


def main() -> None:
    enable_high_dpi_mode()
    root = tk.Tk()
    apply_tk_scaling(root)
    MemoApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
