# ---------------------------------------------------------------
# BoxdList Application (Letterboxd)
# Imports an exported Letterboxd CSV file, displays movies in a table
# (with search, filtering, and sorting), and picks a random movie.
# ---------------------------------------------------------------

import csv
import json
import random
import re
import sys
import tkinter as tk
import unicodedata
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk, font
from tkinterdnd2 import DND_FILES, TkinterDnD
import urllib.request
import threading
import tempfile
import subprocess
import ctypes
from datetime import date, timedelta
  
def app_folder() -> Path:
    """Folder of the .exe when frozen, or of this file when running the code."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent

# ---------- Constants ----------
# (internal id, header title, width in pixels)
COLUMNS = (("name", "Movie", 440),
           ("year", "Year", 70),
           ("date", "Added", 110))
MANDATORY = {"Date", "Name", "Year", "Letterboxd URI"}
CONFIG_FILE = app_folder() / "data" / "boxdlist_config.json"
SEARCH_DELAY_MS = 150  # wait after the last keypress before filtering
NO_FILTER = "All"
EMPTY = "—"            # shown in the table when data is missing
VALID_YEAR = re.compile(r"\d{4}")
VALID_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DEFAULT_SIZE = "760x600"
VALID_SIZE = re.compile(r"\d{3,4}x\d{3,4}(\+-?\d+\+-?\d+)?")
MIN_VISIBLE = 200
APP_VERSION = "1.0.13"
RELEASES_API = "https://api.github.com/repos/goncaloo07/BoxdList/releases/latest"
RECENT_OPTIONS = {NO_FILTER: None, "Last 7 days": 7,
                  "Last 30 days": 30, "Last year": 365}
MAX_OPEN_AT_ONCE = 5 # above this, ask before opening many pages

# Letterboxd-inspired palette
BG = "#14181C"            # window background
SURFACE = "#1F252B"       # table, text boxes, buttons
TEXT = "#CDD6DE"          # normal text
MUTED = "#99AABB"         # secondary text
GREEN = "#406A52"         # soft green for big areas
GREEN_HOVER = "#4F7F65"   # lighter green for the main button on hover
ORANGE = "#FFB000"        # update warning
BLUE = "#40BCF4"          # detail color
ROW_ALT = "#262D34"       # alternate table rows and button hover
SCROLL = "#3A444D"        # scrollbar thumb
LOGO_GREEN = "#00E054"    # logo and import message
LOGO_ORANGE = "#FF8000"   # logo only


# ---------- Logic (pure functions, easy to test) ----------
def normalize(text: str) -> str:
    """Lowercase and accent-insensitive: 'Amélie' -> 'amelie'."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def load_movies(path: str) -> tuple[list[dict], int]:
    """Reads the CSV and returns (movies, ignored_lines).

    Ignores rows without a name or with extra columns (cannot determine
    which field each value belongs to). Invalid years, dates, and links
    are left blank.
    """
    movies = []
    ignored = 0
    # utf-8-sig removes the BOM; newline="" is the recommended way for CSVs
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames or not MANDATORY.issubset(reader.fieldnames):
            raise ValueError(
                "The file does not appear to be a Letterboxd watchlist.\n"
                "Expected columns: Date, Name, Year, Letterboxd URI")

        for row in reader:
            # Extra columns: csv stores them under the key None
            if None in row:
                ignored += 1
                continue

            name = (row.get("Name") or "").strip()
            if not name:
                # completely blank lines do not count as errors
                if any((v or "").strip() for v in row.values()):
                    ignored += 1
                continue

            year = (row.get("Year") or "").strip()
            date = (row.get("Date") or "").strip()
            url = (row.get("Letterboxd URI") or "").strip()
            movies.append({
                "name": name,
                "year": year if VALID_YEAR.fullmatch(year) else "",
                "date": date if VALID_DATE.fullmatch(date) else "",
                "url": url if url.startswith("http") else "",
            })
    return movies, ignored

def save_movies(path: str, movies: list[dict]) -> None:
    """Writes the movies to a CSV that load_movies can read again."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Date", "Name", "Year", "Letterboxd URI"])
        writer.writeheader()   # writes the first line with the column titles
        for m in movies:
            writer.writerow({
                "Date": m["date"],
                "Name": m["name"],
                "Year": m["year"],
                "Letterboxd URI": m["url"],
            })


def is_incomplete(movie: dict) -> bool:
    """True if year, date, or link is missing."""
    return not (movie["year"] and movie["date"] and movie["url"])


def year_as_int(value: str) -> int | None:
    """Converts a year string to int; returns None if invalid."""
    value = value.strip()
    return int(value) if value.isdigit() else None


def year_range_error(year_min: str, year_max: str) -> str | None:
    """Returns a short message if the year fields are invalid, else None."""
    values = []
    for text in (year_min.strip(), year_max.strip()):
        # empty is fine (no limit), but anything else needs exactly 4 digits
        if text and not VALID_YEAR.fullmatch(text):
            return "Years must have 4 digits"
        values.append(int(text) if text else None)
    low, high = values
    if low is not None and high is not None and low > high:
        return "'From' is after 'to'"
    return None


def filter_movies(movies: list[dict], term: str,
                  year_min: int | None, year_max: int | None) -> list[dict]:
    """Filters by name (accent/case-insensitive) and year range."""
    term = normalize(term.strip())
    result = []
    for f in movies:
        if term and term not in normalize(f["name"]):
            continue
        if year_min is not None or year_max is not None:
            year = year_as_int(f["year"])
            if year is None:
                continue
            if year_min is not None and year < year_min:
                continue
            if year_max is not None and year > year_max:
                continue
        result.append(f)
    return result

def filter_recent(movies: list[dict], days: int | None,
                  today: date) -> list[dict]:
    """Keeps only the movies added in the last 'days' days (None = no filter)."""
    if days is None:
        return list(movies)
    limit = today - timedelta(days=days)   # oldest date that still counts
    result = []
    for m in movies:
        try:
            added = date.fromisoformat(m["date"])
        except ValueError:
            continue   # empty or impossible date, like 2024-13-45
        if added >= limit:
            result.append(m)
    return result

def sort_movies(movies: list[dict], column: str | None,
                reverse: bool) -> list[dict]:
    """Returns a sorted copy; the original list is not modified."""
    if column is None:
        return list(movies)

    def key(f: dict):
        value = f[column]
        if column == "year":
            return year_as_int(value) or 0  # empty years stay at the top
        if column == "name":
            return normalize(value)
        return value.lower()                # YYYY-MM-DD dates sort as text

    return sorted(movies, key=key, reverse=reverse)


def pick_random(movies: list[dict], last: dict | None) -> dict:
    """Picks a random movie, avoiding repeating the last one (if possible)."""
    candidates = [f for f in movies if f is not last]
    return random.choice(candidates or movies)

def count_movies_by_decade(movies: list[dict]) -> dict:
    """Counts how many movies there are by decade."""
    years = [a for a in (year_as_int(f["year"]) for f in movies) if a]
    counts = {}
    for year in years:
        decade = year // 10 * 10
        # .get gives 0 the first time a decade shows up
        counts[decade] = counts.get(decade, 0) + 1
    return counts

def parse_version(tag: str) -> tuple[int, ...]:
    """Turns 'v1.0.10' into (1, 0, 10)."""
    return tuple(int(n) for n in tag.lstrip("v").split("."))

def is_newer(latest: str, current: str) -> bool:
    """True only if 'latest' is a higher version than 'current'."""
    try:
        return parse_version(latest) > parse_version(current)
    except ValueError:
        return False   # strange version text: treat it as "no update"
    
def fetch_latest_release() -> dict | None:
    """Returns the latest release info, or None if anything fails."""
    try:
        # opens the address and waits at most 5 seconds for an answer
        with urllib.request.urlopen(RELEASES_API, timeout=5) as response:
            text = response.read().decode("utf-8")   # the answer, as text
        data = json.loads(text)   # turns the text into a Python dict
        if isinstance(data, dict):
            return data
        return None
    except (OSError, ValueError):
        return None
    
def find_installer_url(release: dict) -> str | None:
    """Returns the download link of BoxdListSetup.exe, or None if missing."""
    for release_asset in release.get("assets", []):
        if release_asset.get("name") == "BoxdListSetup.exe":
            return release_asset.get("browser_download_url")
    return None

def download_file(url: str, target: Path) -> bool:
    """Downloads 'url' into 'target'. Returns True if it worked."""
    # saves what is at the address directly into the file
    try:
        urllib.request.urlretrieve(url, target)
        return True
    except OSError:
        return False
    
def remove_movies(movies: list[dict], targets: list[dict]) -> list[dict]:
    """Returns a new list without any of the 'targets'."""
    # id() is unique per object, so duplicates are safe
    ids = {id(t) for t in targets}
    return [m for m in movies if id(m) not in ids]

def insert_movie(movies: list[dict], movie: dict, index: int) -> list[dict]:
    """Returns a new list with 'movie' put back at 'index'."""
    result = list(movies)
    result.insert(index, movie)   # an index past the end just appends
    return result

def movie_key(movie: dict) -> tuple[str, str]:
    """What identifies a movie: name + year."""
    return (movie["name"], movie["year"])

def count_new_movies(old: list[dict], new: list[dict]) -> int:
    """Counts movies in 'new' that were not in 'old'."""
    known = {movie_key(m) for m in old}   # a set makes the lookup fast
    return sum(1 for m in new if movie_key(m) not in known)

# ---------- Configuration (remember the last CSV) ----------
def read_last_path() -> str | None:
    data = read_config()
    return data.get("last_csv") or data.get("ultimo_csv")

def save_last_path(path: str) -> None:
    data = read_config()
    data["last_csv"] = path
    write_config(data)
    
def read_config() -> dict:
    """Returns the saved settings, or an empty dict if there is a problem."""
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, ValueError):
        return {}
    
def write_config(data: dict) -> None:
    """Saves all settings to the config file."""
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # not critical: the app still works fine
    
def valid_window_size(value) -> str:
    """Returns the saved size if it looks right, else the default."""
    if (isinstance(value, str) and VALID_SIZE.fullmatch(value)):
        return value
    return DEFAULT_SIZE

def fit_on_screen(size: str, screen: tuple[int, int, int, int]) -> str:
    """Drops the saved position if the window would be off the screen.
    'screen' is (left, top, right, bottom). 'size' looks like 900x650+120+80.
    """
    match = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", size)
    if not match:
        return size   # no position saved, nothing to check
    width, height, x, y = (int(n) for n in match.groups())
    left, top, right, bottom = screen
    # how many pixels of the window are inside the screen
    visible_w = min(x + width, right) - max(x, left)
    visible_h = min(y + height, bottom) - max(y, top)
    # y >= top keeps the title bar reachable, or you could not drag the window back
    if visible_w >= MIN_VISIBLE and visible_h >= MIN_VISIBLE and y >= top:
        return size
    return f"{width}x{height}"

def valid_sort_column(value) -> str | None:
    """Returns the saved column if it exists, else None."""
    if not isinstance(value, str):
        return None
    cols = {c[0] for c in COLUMNS}
    if value in cols:
        return value
    return None

def valid_reverse_order(value) -> bool:
    """Returns the saved direction if it is True or False, else False."""
    if isinstance(value, bool):
        return value
    return False

# ---------- Resources (icon) ----------
def resource_path(relative: str) -> Path:
    """Finds a file both when running the code and inside the .exe."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative

def enable_dpi_awareness() -> None:
    """Tells Windows the app handles screen scaling itself (sharp text)."""
    if sys.platform != "win32":
        return  # ctypes.windll only exists on Windows
    try:
        # 2 = per monitor aware, the best option on Windows 8.1 and newer
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()   # older Windows
        except (OSError, AttributeError):
            pass  # not critical, the app still works, just a bit blurry
        
def rounded_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    """Points of a rounded rectangle, to draw with a smooth canvas polygon."""
    # each corner point is repeated so the curve stays tight
    return [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1,
            x2, y1 + r, x2, y1 + r, x2, y2 - r, x2, y2 - r, x2, y2,
            x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2,
            x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]

# ---------- Interface ----------
class App(TkinterDnD.Tk):
    """Main window. Inherits from TkinterDnD.Tk, meaning the class itself is the window."""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"BoxdList v{APP_VERSION}")
        config = read_config()
        size = valid_window_size(config.get("window_size"))
        # the whole desktop area: left, top, right, bottom (can be negative with 2 monitors)
        screen = (self.winfo_vrootx(), self.winfo_vrooty(),
                self.winfo_vrootx() + self.winfo_vrootwidth(),
                self.winfo_vrooty() + self.winfo_vrootheight())
        size = fit_on_screen(size, screen)
        if size == DEFAULT_SIZE:
            # only the default needs scaling; a saved size is already in real pixels
            w, h = size.split("x")
            size = f"{self.px(int(w))}x{self.px(int(h))}"
        self.geometry(size)
        self.minsize(self.px(700), self.px(400))
        self.set_icon()
        self.apply_theme()

        self.movies: list[dict] = []     # original list, in CSV order
        self.visible: list[dict] = []    # filtered and sorted (what is seen)
        self.sort_column = valid_sort_column(config.get("sort_column"))
        self.reverse_order = valid_reverse_order(config.get("reverse_order"))
        self.last_randomized: dict | None = None
        self.update_version: str | None = None
        self.update_url: str | None = None
        self._search_job = None          # pending after() ID (debounce)
        self.downloading = False   # True while the installer is being downloaded
        self.last_deleted: list[tuple[dict, int]] | None = None
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # ---------- Header: logo and app name ----------
        header = ttk.Frame(self, padding=(self.px(12), self.px(10), self.px(12), 0))
        header.pack(fill="x")
        self.build_logo(header).pack(side="left")
        ttk.Label(header, text="BoxdList", font=("Segoe UI", 11, "bold")).pack(
                  side="left", padx=(self.px(8), 0))
        self.status = ttk.Label(header, text="",
                        font=("Segoe UI", 10, "bold"), foreground=LOGO_GREEN)
        self.status.pack(side="right")
        self._status_job = None
        tk.Frame(self, height=self.px(1), bg=ROW_ALT, bd=0,
                 highlightthickness=0, padx=self.px(12)).pack(fill="x", pady=(self.px(10), 0))

        # ---------- Row 1: Import, search, and counter ----------
        top = ttk.Frame(self, padding=(self.px(12), self.px(12),
                                       self.px(12), self.px(8)))
        top.pack(fill="x")

        ttk.Button(top, text="Import CSV (Ctrl+O)",
                   command=self.import_csv).pack(side="left", padx=(0, self.px(10)))

        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self.schedule_update())
        self.search_entry = ttk.Entry(top, textvariable=self.search)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=self.px(8))
        self.add_placeholder(self.search_entry, self.search, "Search movies...")

        self.counter = ttk.Label(top, text="")
        self.counter.pack(side="right")
        
        self.update_label = ttk.Label(top, text="Update pending",
                              foreground=ORANGE, cursor="hand2")
        self.update_label.bind("<Button-1>", lambda _: self.ask_update())   

        # ---------- Row 2: Year filters and decade dropdown ----------
        filters = ttk.Frame(self, padding=(self.px(12), 0, self.px(12), self.px(8)))
        filters.pack(fill="x")

        self.year_min = tk.StringVar()
        year_min_entry = ttk.Entry(filters, textvariable=self.year_min, width=10)
        year_min_entry.pack(side="left", padx=(0, self.px(6)))
        self.add_placeholder(year_min_entry, self.year_min, "From Year")

        self.year_max = tk.StringVar()
        year_max_entry = ttk.Entry(filters, textvariable=self.year_max, width=10)
        year_max_entry.pack(side="left", padx=(0, self.px(6)))
        self.add_placeholder(year_max_entry, self.year_max, "To Year")

        for var in (self.year_min, self.year_max):
            var.trace_add("write", lambda *_: self.schedule_update())

        ttk.Label(filters, text="Decade", foreground=MUTED).pack(
            side="left", padx=(self.px(10), self.px(6)))
        self.decade = ttk.Combobox(filters, state="readonly", width=8,
                                   values=[NO_FILTER])
        self.decade.set(NO_FILTER)
        self.decade.pack(side="left")
        self.decade.bind("<<ComboboxSelected>>", lambda _: self.select_decade())
        
        ttk.Label(filters, text="Added", foreground=MUTED).pack(
            side="left", padx=(self.px(10), self.px(6)))
        self.recent = ttk.Combobox(filters, state="readonly", width=12,
                                values=list(RECENT_OPTIONS))   # the dict keys are the texts
        self.recent.set(NO_FILTER)
        self.recent.pack(side="left")
        self.recent.bind("<<ComboboxSelected>>", lambda _: self.update_list())

        ttk.Button(filters, text="Clear filters",
                   command=self.clear_filters).pack(side="left", padx=self.px(10))

        # Red message for invalid year fields (empty when everything is fine)
        self.year_error = ttk.Label(filters, text="", foreground="red")
        self.year_error.pack(side="left")
        
        # Green note about new movies after an import (under the counter)
        self.new_label = ttk.Label(filters, text="", foreground=GREEN)
        self.new_label.pack(side="right")

        # ---------- Table ----------
        middle = ttk.Frame(self, padding=(self.px(12), 0))
        middle.pack(fill="both", expand=True)

        self.table = ttk.Treeview(
            middle, columns=[c[0] for c in COLUMNS], show="headings")
        for col, title, width in COLUMNS:
            # c=col fixes the value of col in each loop iteration
            self.table.heading(col, text=title,
                               command=lambda c=col: self.sort(c))
            self.table.column(col, width=self.px(width), anchor="w")

        # Alternating row colors act as a soft divider between rows
        self.table.tag_configure("even", background=SURFACE)
        self.table.tag_configure("odd", background=ROW_ALT)

        scrollbar = ttk.Scrollbar(middle, orient="vertical",
                                  command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y", padx=(self.px(4), 0))

        # Warning message overlay when there is nothing to display
        self.warning = ttk.Label(self.table, text="", foreground=MUTED)

        self.table.bind("<Double-1>", self.on_double_click)

        # ---------- Bottom Section ----------
        bottom = ttk.Frame(self, padding=self.px(12))
        bottom.pack(fill="x")

        # Created now, but placed on screen later by update_buttons
        self.random_button = ttk.Button(bottom, text="Random Movie (Ctrl+R)",
                                        style="Accent.TButton",
                                        command=self.random_movie)
        self.open_button = ttk.Button(bottom, text="Open in Letterboxd (Enter)",
                                      command=self.open_link)
        self.export_button = ttk.Button(bottom, text="Export CSV",
                                        command=self.export_csv)

        self.result = ttk.Label(bottom, text="", font=("Segoe UI", 11, "bold"),
                                foreground=GREEN)
        self.result.pack(side="left", padx=self.px(12))
        
        # ---------- Right click menu ------------
        # Dark colors, and tearoff=0 removes the dotted line at the top
        self.menu = tk.Menu(self, tearoff=0, bg=SURFACE, fg=TEXT,
                            activebackground=GREEN, activeforeground=TEXT,
                            borderwidth=0, relief="flat")
        self.menu.add_command(label="Open in Letterboxd", command=self.open_link)
        self.menu.add_command(label="Copy name", command=self.copy_name)
        self.menu.add_command(label="Delete", command=self.delete_selected)

        # ---------- Keyboard Shortcuts ----------
        self.bind("<Control-o>", lambda _: self.import_csv())
        self.bind("<Control-f>", lambda _: self.focus_on_search())
        self.bind("<Control-F>", lambda _: self.focus_on_search())
        self.bind("<Control-r>", lambda _: self.random_movie())
        self.bind("<Control-R>", lambda _: self.random_movie())
        self.bind("<Control-z>", lambda _: self.on_undo_key())
        self.bind("<Control-Z>", lambda _: self.on_undo_key())
        self.table.bind("<Return>", lambda _: self.open_link())
        self.table.bind("<Button-3>", self.on_right_click)
        self.table.bind("<Delete>", lambda _: self.delete_selected())
        self.drag_start: str | None = None   # row where the mouse drag began
        self.table.bind("<Button-1>", self.on_press, add="+")
        self.table.bind("<B1-Motion>", self.on_drag, add="+")

        self.dark_title_bar()   # ask Windows for a dark title bar
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self.on_drop)
        self.update_list()
        self.load_last_csv()
        if getattr(sys, "frozen", False):
            self.check_for_update()

    def set_icon(self) -> None:
        """Sets the app icon on the window and taskbar."""
        try:
            if sys.platform == "win32":
                self.iconbitmap(str(resource_path("assets/boxdlist.ico")))
            else:
                self.icon = tk.PhotoImage(
                    file=str(resource_path("assets/boxdlist_logo.png")))
                self.iconphoto(True, self.icon)  # keep a reference or it disappears
        except tk.TclError:
            pass  # the app still works without an icon
    
    def px(self, value: int) -> int:
        """Scales a pixel value to the screen DPI (100% scale = no change)."""
        # "tk scaling" is pixels per point; 96 DPI equals 96/72
        return round(value * float(self.tk.call("tk", "scaling")) / (96 / 72))
    
    def add_placeholder(self, entry: ttk.Entry, var: tk.StringVar, text: str) -> None:
        """Shows grey hint text inside an empty entry, without touching its value."""
        # A label on top of the entry, so var.get() stays "" when empty
        hint = ttk.Label(entry, text=text, style="Hint.TLabel", cursor="xterm")
        hint.bind("<Button-1>", lambda _: entry.focus_set())  # click goes to the entry

        def toggle(*_) -> None:
            if var.get():
                hint.place_forget()   # something typed: hide the hint
            else:
                hint.place(x=self.px(9), rely=0.5, anchor="w")

        var.trace_add("write", toggle)
        toggle()
        
    def build_logo(self, parent) -> tk.Canvas:
        """Draws the BoxdList logo with shapes, so no image file is needed."""
        width = self.px(26)     # logo width on screen, change this to resize it
        k = width / 320         # scale from the logo design (320 wide) to screen pixels
        canvas = tk.Canvas(parent, width=width, height=round(250 * k),
                        bg=BG, highlightthickness=0, bd=0)

        def at(x: float, y: float) -> tuple[float, float]:
            # moves a logo coordinate to canvas pixels
            return (x - 96) * k, (y - 131) * k

        # three cards, from back to front (x, y, color)
        cards = ((168, 139, BLUE), (136, 171, LOGO_GREEN), (104, 203, LOGO_ORANGE))
        for x, y, color in cards:
            x1, y1 = at(x, y)
            x2, y2 = at(x + 240, y + 170)
            canvas.create_polygon(rounded_points(x1, y1, x2, y2, 28 * k),
                                smooth=True, fill=color, outline=BG,
                                width=max(1, round(8 * k)))
        # play triangle on the front card
        triangle = [*at(208, 260), *at(208, 316), *at(252, 288)]
        canvas.create_polygon(triangle, fill=BG, outline=BG,
                            width=max(1, round(12 * k)))
        return canvas

    def apply_theme(self) -> None:
        """Applies the Letterboxd colors to the whole window."""
        style = ttk.Style(self)
        style.theme_use("clam")         # the theme that accepts custom colors
        self.configure(bg=BG)           # the window itself (not a ttk widget)
        # Give every default Tk font the same family and size
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            font.nametofont(name).configure(family="Segoe UI", size=9)
        # "TFrame" means every ttk.Frame in the app
        style.configure("TFrame", background=BG)
        # Text labels
        style.configure("TLabel", background=BG, foreground=TEXT)
        # Secondary buttons: quiet, they only lighten a little on hover
        style.configure("TButton", background=SURFACE, foreground=TEXT,
                        borderwidth=0, padding=(self.px(10), self.px(5)),
                        bordercolor=SURFACE, lightcolor=SURFACE,
                        darkcolor=SURFACE, focuscolor=SURFACE)
        style.map("TButton",
                  background=[("active", ROW_ALT)],
                  foreground=[("active", TEXT)])
        # Main button: always green, so it stands out from the others
        style.configure("Accent.TButton", background=GREEN, foreground=TEXT,
                        borderwidth=0, padding=(self.px(10), self.px(5)),
                        bordercolor=GREEN, lightcolor=GREEN,
                        darkcolor=GREEN, focuscolor=GREEN)
        style.map("Accent.TButton",
                  background=[("active", GREEN_HOVER)],
                  foreground=[("active", TEXT)])
        # Grey hint text that sits inside the text boxes
        style.configure("Hint.TLabel", background=SURFACE, foreground=MUTED)
        # Text boxes (insertcolor is the blinking cursor)
        style.configure("TEntry", background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, insertcolor=TEXT, bordercolor=SURFACE,
                        lightcolor=SURFACE, darkcolor=SURFACE,
                        padding=(self.px(6), self.px(4)))
        # Keep the border flat even when the box has focus
        style.map("TEntry",
                  bordercolor=[("focus", SURFACE)],
                  lightcolor=[("focus", SURFACE)],
                  darkcolor=[("focus", SURFACE)])
        # Table rows, and the selected row in green
        style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, bordercolor=SURFACE, lightcolor=SURFACE,
                        darkcolor=SURFACE, rowheight=self.px(28), borderwidth=0)
        style.map("Treeview",
                  background=[("selected", GREEN)],
                  foreground=[("selected", TEXT)])
        # Column titles
        style.configure("Treeview.Heading", background=BG, foreground=MUTED,
                        relief="flat", bordercolor=SURFACE, lightcolor=SURFACE,
                        darkcolor=SURFACE, padding=(self.px(8), self.px(5)),
                        font=("Segoe UI", 10, "bold"))
        # Decade dropdown (the map is needed because it is readonly)
        style.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE,
                        foreground=TEXT, arrowcolor=TEXT, bordercolor=SURFACE,
                        lightcolor=SURFACE, darkcolor=SURFACE,
                        padding=(self.px(6), self.px(4)))
        style.map("TCombobox", fieldbackground=[("readonly", SURFACE)])
        # The list that opens from the dropdown is not a ttk widget,
        # so it is styled with global options
        self.option_add("*TCombobox*Listbox.background", SURFACE)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", GREEN)
        self.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        # Scrollbar: dark track, green thumb when the mouse is over it
        style.layout("Vertical.TScrollbar",
             [("Vertical.Scrollbar.trough",
               {"sticky": "ns",
                "children": [("Vertical.Scrollbar.thumb",
                              {"expand": "1", "sticky": "nswe"})]})])
        # arrowsize also sets the bar width
        style.configure("TScrollbar", background=SCROLL, troughcolor=BG,
                        bordercolor=BG, lightcolor=SCROLL, darkcolor=SCROLL,
                        arrowsize=self.px(10), gripcount=0)
        # Thumb turns green when the mouse is over it
        style.map("TScrollbar", background=[("active", GREEN)])
        
    def dark_title_bar(self) -> None:
        """Asks Windows to draw the title bar in dark mode."""
        if sys.platform != "win32":
            return  # ctypes.windll only exists on Windows
        try:
            self.update()  # the window must exist before it has a handle
            # winfo_id is the inner frame, GetParent gives the real window
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(1)  # 1 = dark mode on
            # 20 works on recent Windows, 19 on some older Windows 10 builds
            for attribute in (20, 19):
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
                if result == 0:  # 0 means it worked
                    break
        except (OSError, AttributeError):
            pass  # not critical: the app still works with a light title bar
        
    # ----- Data Loading -----
    def load_last_csv(self) -> None:
        """On startup, attempts to reload the last used CSV."""
        path = read_last_path()
        if path and Path(path).is_file():
            try:
                self.set_movies(load_movies(path)[0])
            except (OSError, ValueError, UnicodeDecodeError, csv.Error):
                pass  # file changed or became invalid: starts empty

    def import_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose Watchlist CSV",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        self.load_path(path)
    
    def load_path(self, path: str) -> None:
        """Loads a watchlist CSV from a path and refreshes the app."""
        try:
            movies, ignored = load_movies(path)
        except (OSError, ValueError, UnicodeDecodeError, csv.Error) as error:
            messagebox.showerror("Import Error", str(error))
            return
        
        previous = self.movies
        save_last_path(path)
        self.set_movies(movies)
        
        incomplete = sum(is_incomplete(f) for f in movies)
        if ignored or incomplete:
            messagebox.showwarning(
                "Import",
                f"{len(movies)} movies imported.\n"
                f"- {ignored} lines ignored (missing name or malformed)\n"
                f"- {incomplete} movies with missing data (shown with {EMPTY})")
        else:
            self.show_status(f"{len(movies)} movies imported")
                    
        if previous:
            new_count = count_new_movies(previous, movies)
            if new_count:
                word = "movie" if new_count == 1 else "movies"
                self.new_label.config(
                    text=f"{new_count} new {word} since last time")
                
    def on_drop(self, event) -> None:
        """Loads the CSV that was dropped on the window."""
        paths = self.tk.splitlist(event.data)
        if not paths:
            return
        path = paths[0]
        if not path.lower().endswith(".csv"):
            messagebox.showinfo("BoxdList", "Only CSV files can be dropped.")
            return
        self.load_path(path)       
        
    def show_status(self, text: str) -> None:
        """Shows a short message that clears itself after 4 seconds."""
        if self._status_job is not None:
            self.after_cancel(self._status_job)
        self.status.config(text=text)
        self._status_job = self.after(4000, lambda: self.status.config(text="")) 
        
    def update_decades(self) -> None:
        """Refreshes the decade dropdown with the decades that exist in the list."""
        # remember the chosen decade as a number, because the text will change
        current = self.decade.get()
        start = int(current.split("s")[0]) if current != NO_FILTER else None
        counts = count_movies_by_decade(self.movies)
        values = [NO_FILTER] + [f"{d}s ({counts[d]})" for d in sorted(counts)]
        self.decade.config(values=values)
        if start in counts:
            # still exists: only the number in the text may have changed
            self.decade.set(f"{start}s ({counts[start]})")
        elif start is not None:
            # that decade is gone: go back to "All" and clear the year fields
            self.decade.set(NO_FILTER)
            self.select_decade()

    def set_movies(self, movies: list[dict]) -> None:
        """Replaces the current watchlist and refreshes everything."""
        self.movies = movies
        self.last_randomized = None
        self.result.config(text="")
        self.update_decades()
        self.clear_filters()
        self.new_label.config(text="")
        self.last_deleted = None
        
    def check_for_update(self) -> None:
        """Starts the check in the background so the window never freezes."""
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self) -> None:
        release = fetch_latest_release()
        # tkinter is not thread safe: hand the result to the main thread
        self.after(0, self._on_update_result, release)
    
    def _on_update_result(self, release: dict | None) -> None:
        if not release:
            return  # network error or invalid JSON: ignore it
        if not is_newer(release.get("tag_name", ""), APP_VERSION):
            return  # no new version available
        self.update_version = release.get("tag_name", "").lstrip("v")
        self.update_url = find_installer_url(release)
        self.ask_update()
        
    def ask_update(self) -> None:
        if self.downloading:
            return  # a download is already running
        answer = messagebox.askyesno(
            "Update available",
            f"Version {self.update_version} is available. Update now?")
        if not answer:
            self.show_update_label("Update pending")
            return
        if not self.update_url:
            messagebox.showerror("Update Error", "Could not find the installer link.")
            self.show_update_label("Update pending")
            return
        self.downloading = True
        self.show_update_label("Downloading update...")
        target = Path(tempfile.gettempdir()) / "BoxdListSetup.exe"
        threading.Thread(target=self._download_worker,
                        args=(self.update_url, target), daemon=True).start()

    def show_update_label(self, text: str) -> None:
        """Shows the update label on the right with the given text."""
        self.update_label.config(text=text)
        self.update_label.pack(side="right", padx=self.px(8))
        
    def install_update(self, installer: Path) -> None:
        try:
            # starts the installer as a separate program; our app does not wait for it
            subprocess.Popen([str(installer), "/SILENT", "/CLOSEAPPLICATIONS"])
        except OSError:
            messagebox.showerror("Update", "Could not start the installer.")
            self.show_update_label("Update pending")
            return
        self.on_close()
        
    def _download_worker(self, url: str, target: Path) -> None:
        ok = download_file(url, target)   # slow part, runs in the background
        # tkinter is not thread safe: hand the result to the main thread
        self.after(0, self._on_download_done, ok, target)

    def _on_download_done(self, ok: bool, target: Path) -> None:
        self.downloading = False
        if ok:
            self.install_update(target)
        else:
            messagebox.showerror("Update", "Download failed.")
            # let the user try again
            self.show_update_label("Update pending")

    # ----- Filters -----
    def select_decade(self) -> None:
        choice = self.decade.get()
        if choice == NO_FILTER:
            self.year_min.set("")
            self.year_max.set("")
        else:
            start = int(choice.split("s")[0])  # "1990s" -> 1990
            self.year_min.set(str(start))
            self.year_max.set(str(start + 9))
        self.update_list()

    def clear_filters(self) -> None:
        # Each set() triggers scheduling; only one update runs at the end
        self.search.set("")
        self.year_min.set("")
        self.year_max.set("")
        self.decade.set(NO_FILTER)
        self.recent.set(NO_FILTER)
        self.update_list()

    # ----- Table Updates -----
    def schedule_update(self) -> None:
        """Waits a bit after the last change (prevents filtering on every keypress)."""
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(SEARCH_DELAY_MS, self.update_list)

    def update_list(self) -> None:
        """Filters, sorts (without touching original list) and rebuilds the table."""
        if self._search_job is not None:
            self.after_cancel(self._search_job)
            self._search_job = None

        # Show a message if the year fields are invalid or in the wrong order
        self.year_error.config(
            text=year_range_error(self.year_min.get(), self.year_max.get()) or "")

        filtered = filter_movies(self.movies, self.search.get(),
                                 year_as_int(self.year_min.get()),
                                 year_as_int(self.year_max.get()))
        days = RECENT_OPTIONS[self.recent.get()]
        filtered = filter_recent(filtered, days, date.today())
        self.visible = sort_movies(
            filtered, self.sort_column, self.reverse_order)
        if not any(m is self.last_randomized for m in self.visible):
            self.result.config(text="")

        self.table.delete(*self.table.get_children())
        # iid = index in self.visible, to recover the selected movie
        for i, f in enumerate(self.visible):
            # Even rows are plain, odd rows are slightly lighter
            tag = "odd" if i % 2 else "even"
            self.table.insert("", "end", iid=str(i), tags=(tag,),
                              values=(f["name"], f["year"] or EMPTY, f["date"] or EMPTY))

        self.counter.config(text=f"{len(self.visible)} / {len(self.movies)}")
        self.update_headers()
        self.update_warning()
        self.update_buttons()

    def update_headers(self) -> None:
        """Shows ▲ / ▼ on the column currently used for sorting."""
        arrow = " ▼" if self.reverse_order else " ▲"
        for col, title, _ in COLUMNS:
            text = title + (arrow if col == self.sort_column else "")
            self.table.heading(col, text=text)

    def update_warning(self) -> None:
        if not self.movies:
            self.warning.config(text="Import a CSV to begin (Ctrl+O)")
        elif not self.visible:
            self.warning.config(text="No movies match the filters")
        else:
            self.warning.place_forget()
            return
        self.warning.place(relx=0.5, rely=0.4, anchor="center")
    
    def update_buttons(self) -> None:
        """Shows the random, open and export buttons only when movies are visible."""
        buttons = (self.random_button, self.open_button, self.export_button)
        if self.visible:
            # each one goes right before the result label, so the order stays
            for button in buttons:
                button.pack(side="left", padx=(0, self.px(6)), before=self.result)
        else:
            for button in buttons:
                button.pack_forget()

    # ----- Actions -----
    def sort(self, column: str) -> None:
        """Sorts by column; clicking the same column reverses direction."""
        if column == self.sort_column:
            self.reverse_order = not self.reverse_order
        else:
            self.sort_column = column
            self.reverse_order = False
        self.update_list()

    def random_movie(self) -> None:
        if not self.visible:
            messagebox.showinfo("BoxdList",
                                "No movies to choose from. Import a CSV first.")
            return

        f = pick_random(self.visible, self.last_randomized)
        self.last_randomized = f
        # find by identity, so duplicate movies map to the right row
        iid = str(next(i for i, m in enumerate(self.visible) if m is f))

        self.table.selection_set(iid)
        self.table.see(iid)
        year = f" ({f['year']})" if f["year"] else ""
        self.result.config(text=f["name"] + year)
        self.table.focus_set()
        
    def on_double_click(self, event) -> None:
        """Opens the link only if a row was double-clicked, not the header."""
        # identify_region returns "heading", "cell" or "nothing"
        if self.table.identify_region(event.x, event.y) == "cell":
            self.open_link()

    def open_link(self) -> None:
        """Opens the Letterboxd page of every selected movie."""
        sel = self.table.selection()
        if not sel:
            return
        # selected movies, then only their links (movies without a link are skipped)
        selected = [self.visible[int(i)] for i in sel]
        urls = [m["url"] for m in selected if m["url"]]
        if not urls:
            text = ("This movie has no link." if len(selected) == 1
                    else "None of these movies has a link.")
            messagebox.showinfo("BoxdList", text)
            return
        if len(urls) > MAX_OPEN_AT_ONCE:
            text = (f"Are you sure you want to open {len(urls)} browser pages?")
            answer = messagebox.askyesno("BoxdList", text)
            if not answer:
                return
        for url in urls:
            webbrowser.open(url)        
            
    def on_press(self, event) -> None:
        """Remembers the row where a drag starts (plain clicks only)."""
        # 0x1 is Shift and 0x4 is Ctrl: they keep their normal behavior
        if event.state & 0x5:
            self.drag_start = None
            return
        # empty text (header or blank area) becomes None
        self.drag_start = self.table.identify_row(event.y) or None

    def on_drag(self, event) -> None:
        """Selects every row between the start row and the row under the mouse."""
        if self.drag_start is None:
            return
        current = self.table.identify_row(event.y)
        rows = self.table.get_children()
        # the mouse may be outside the rows, or the table may have been rebuilt
        if not current or self.drag_start not in rows:
            return
        first = rows.index(self.drag_start)
        last = rows.index(current)
        low, high = min(first, last), max(first, last)
        self.table.selection_set(rows[low:high + 1])
            
    def on_right_click(self, event) -> None:
        """Shows the menu for the row under the mouse."""
        row = self.table.identify_row(event.y)
        if not row:
            return
        self.table.selection_set(row)
        try:
            # x_root and y_root are the position on the whole screen
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            # always release, or the menu can block clicks in the window
            self.menu.grab_release()
            
    def focus_on_search(self) -> None:
        """Focus on the search bar"""
        # puts the cursor inside the box
        self.search_entry.focus_set()
        # selects the old text, so typing replaces it
        self.search_entry.select_range(0, "end")
        
    def copy_name(self) -> None:
        """Copies the selected movie name to the clipboard."""
        sel = self.table.selection()
        if not sel:
            return
        name = self.visible[int(sel[0])]["name"]
        self.clipboard_clear()          # empties the clipboard
        self.clipboard_append(name)   # puts the text in it
        
    def save_to_last_csv(self, movies: list[dict], error_title: str) -> bool:
        """Saves to the last imported CSV. Returns False if saving failed."""
        path = read_last_path()
        if not path:
            return True   # no CSV yet: nothing to save
        try:
            save_movies(path, movies)
        except OSError as error:
            messagebox.showerror(error_title, str(error))
            return False
        return True

    def delete_selected(self) -> None:
        """Removes the selected movies from the list and from the CSV."""
        sel = self.table.selection()
        if not sel:
            return
        # every selected row id becomes a movie
        targets = [self.visible[int(i)] for i in sel]
        if len(targets) == 1:
            question = (f"Are you sure you want to delete "
                        f"'{targets[0]['name']}' from the list?")
        else:
            question = (f"Are you sure you want to delete "
                        f"{len(targets)} movies from the list?")
        if not messagebox.askyesno("Delete Movie", question):
            return
        # position of each movie in the full list (not the filtered one),
        # walking from the top so the positions come out in increasing order
        ids = {id(t) for t in targets}
        deleted = [(m, i) for i, m in enumerate(self.movies) if id(m) in ids]
        remaining = remove_movies(self.movies, targets)
        # save first: if it fails, the list stays as it was
        if not self.save_to_last_csv(remaining, "Delete Error"):
            return
        self.movies = remaining
        self.last_deleted = deleted
        self.update_decades()
        self.update_list()
        if len(deleted) == 1:
            self.show_status(f"Deleted '{deleted[0][0]['name']}' (Ctrl+Z to undo)")
        else:
            self.show_status(f"Deleted {len(deleted)} movies (Ctrl+Z to undo)")
        
    def on_undo_key(self) -> None:
        """Ctrl+Z: undo a delete, unless the user is typing in a text box."""
        try:
            focused = self.focus_get()
        except KeyError:
            return   # Tk can fail here while a dropdown list is open
        if isinstance(focused, ttk.Entry):   # also covers the Combobox
            return
        self.undo_delete()

    def undo_delete(self) -> None:
        """Puts the last deleted movies back, in the list and in the CSV."""
        if self.last_deleted is None:
            return
        restored = self.movies
        for movie, index in self.last_deleted:
            restored = insert_movie(restored, movie, index)
        if not self.save_to_last_csv(restored, "Undo Error"):
            return   # keep last_deleted, so the user can try again
        count = len(self.last_deleted)
        first_name = self.last_deleted[0][0]["name"]
        self.movies = restored
        self.last_deleted = None
        self.update_decades()
        self.update_list()
        if count == 1:
            self.show_status(f"Restored '{first_name}'")
        else:
            self.show_status(f"Restored {count} movies")
        
    def export_csv(self) -> None:
        """Saves the movies currently shown to a CSV file."""
        if not self.visible:
            messagebox.showinfo("BoxdList", "No movies to export.")
            return
        path = filedialog.asksaveasfilename(
                title="Export CSV",
                defaultextension=".csv",        # adds .csv if the user forgets it
                initialfile="watchlist.csv",    # suggested name
                filetypes=[("CSV", "*.csv")]
                )
        if not path:
            return
        try:
            save_movies(path, self.visible)
        except OSError as error:
            messagebox.showerror("Export Error", str(error))
        
    def on_close(self) -> None:
        """Saves the sort and window size, then closes the app."""
        data = read_config()
        data["sort_column"] = self.sort_column
        data["reverse_order"] = self.reverse_order
        data["window_size"] = (f"{self.winfo_width()}x{self.winfo_height()}"
                               f"+{self.winfo_x()}+{self.winfo_y()}")
        write_config(data)
        self.destroy()


if __name__ == "__main__":
    enable_dpi_awareness()   # must run before the window is created
    App().mainloop()