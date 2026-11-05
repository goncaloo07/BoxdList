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
from tkinter import filedialog, messagebox, ttk
import urllib.request
import threading
import tempfile
import subprocess
  
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
SEARCH_DELAY_MS = 200  # wait after the last keypress before filtering
NO_FILTER = "All"
EMPTY = "—"            # shown in the table when data is missing
VALID_YEAR = re.compile(r"\d{4}")
VALID_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DEFAULT_SIZE = "760x600"
VALID_SIZE = re.compile(r"\d{3,4}x\d{3,4}")
APP_VERSION = "1.0.7"
RELEASES_API = "https://api.github.com/repos/goncaloo07/BoxdList/releases/latest"


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


# ---------- Interface ----------
class App(tk.Tk):
    """Main window. Inherits from tk.Tk, meaning the class itself is the window."""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"BoxdList {APP_VERSION}")
        config = read_config()
        self.geometry(valid_window_size(config.get("window_size")))
        self.minsize(700, 400)
        self.set_icon()

        self.movies: list[dict] = []     # original list, in CSV order
        self.visible: list[dict] = []    # filtered and sorted (what is seen)
        self.sort_column = valid_sort_column(config.get("sort_column"))
        self.reverse_order = valid_reverse_order(config.get("reverse_order"))
        self.last_randomized: dict | None = None
        self.update_version: str | None = None
        self.update_url: str | None = None
        self._search_job = None          # pending after() ID (debounce)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # ---------- Row 1: Import, search, and counter ----------
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="Import CSV (Ctrl+O)",
                   command=self.import_csv).pack(side="left", padx=(0, 10))

        ttk.Label(top, text="Search:").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self.schedule_update())
        self.search_entry = ttk.Entry(top, textvariable=self.search)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=6)

        self.counter = ttk.Label(top, text="")
        self.counter.pack(side="right")
        
        self.update_label = ttk.Label(top, text="Update pending",
                              foreground="orange", cursor="hand2")
        self.update_label.bind("<Button-1>", lambda _: self.ask_update())   

        # ---------- Row 2: Year filters and decade dropdown ----------
        filters = ttk.Frame(self, padding=(8, 0, 8, 6))
        filters.pack(fill="x")

        ttk.Label(filters, text="Years from:").pack(side="left")
        self.year_min = tk.StringVar()
        ttk.Entry(filters, textvariable=self.year_min, width=6).pack(
            side="left", padx=4)
        ttk.Label(filters, text="to").pack(side="left")
        self.year_max = tk.StringVar()
        ttk.Entry(filters, textvariable=self.year_max, width=6).pack(
            side="left", padx=4)
        for var in (self.year_min, self.year_max):
            var.trace_add("write", lambda *_: self.schedule_update())

        ttk.Label(filters, text="Decade:").pack(side="left", padx=(14, 4))
        self.decade = ttk.Combobox(filters, state="readonly", width=8,
                                   values=[NO_FILTER])
        self.decade.set(NO_FILTER)
        self.decade.pack(side="left")
        self.decade.bind("<<ComboboxSelected>>", lambda _: self.select_decade())

        ttk.Button(filters, text="Clear filters",
                   command=self.clear_filters).pack(side="left", padx=14)

        # Red message for invalid year fields (empty when everything is fine)
        self.year_error = ttk.Label(filters, text="", foreground="red")
        self.year_error.pack(side="left")

        # ---------- Table ----------
        middle = ttk.Frame(self, padding=(8, 0))
        middle.pack(fill="both", expand=True)

        self.table = ttk.Treeview(
            middle, columns=[c[0] for c in COLUMNS], show="headings")
        for col, title, width in COLUMNS:
            # c=col fixes the value of col in each loop iteration
            self.table.heading(col, text=title,
                               command=lambda c=col: self.sort(c))
            self.table.column(col, width=width, anchor="w")

        scrollbar = ttk.Scrollbar(middle, orient="vertical",
                                  command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Warning message overlay when there is nothing to display
        self.warning = ttk.Label(self.table, text="", foreground="gray")

        self.table.bind("<Double-1>", self.on_double_click)

        # ---------- Bottom Section ----------
        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")

        # Created now, but placed on screen later by update_buttons
        self.random_button = ttk.Button(bottom, text="Random Movie (Ctrl+R)",
                                        command=self.random_movie)
        self.open_button = ttk.Button(bottom, text="Open in Letterboxd (Enter)",
                                      command=self.open_link)
        self.export_button = ttk.Button(bottom, text="Export CSV",
                                        command=self.export_csv)

        self.result = ttk.Label(
            bottom, text="", font=("TkDefaultFont", 11, "bold"))
        self.result.pack(side="left", padx=12)
        
        # ---------- Right click menu ------------
        # tearoff=0 removes the dotted line at the top of the menu
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Open in Letterboxd", command=self.open_link)
        self.menu.add_command(label="Copy name", command=self.copy_name)

        # ---------- Keyboard Shortcuts ----------
        self.bind("<Control-o>", lambda _: self.import_csv())
        self.bind("<Control-f>", lambda _: self.focus_on_search())
        self.bind("<Control-F>", lambda _: self.focus_on_search())
        self.bind("<Control-r>", lambda _: self.random_movie())
        self.bind("<Control-R>", lambda _: self.random_movie())
        self.table.bind("<Return>", lambda _: self.open_link())
        self.table.bind("<Button-3>", self.on_right_click)

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

        try:
            movies, ignored = load_movies(path)
        except (OSError, ValueError, UnicodeDecodeError, csv.Error) as error:
            messagebox.showerror("Import Error", str(error))
            return

        save_last_path(path)
        self.set_movies(movies)

        incomplete = sum(is_incomplete(f) for f in movies)
        if ignored or incomplete:
            messagebox.showwarning(
                "Import",
                f"{len(movies)} movies imported.\n"
                f"- {ignored} lines ignored (missing name or malformed)\n"
                f"- {incomplete} movies with missing data (shown with {EMPTY})")

    def set_movies(self, movies: list[dict]) -> None:
        """Replaces the current watchlist and refreshes everything."""
        self.movies = movies
        self.last_randomized = None
        self.result.config(text="")

        # Updates available decades in the filter dropdown
        years = [a for a in (year_as_int(f["year"]) for f in movies) if a]
        decades = sorted({a // 10 * 10 for a in years})
        self.decade.config(values=[NO_FILTER] + [f"{d}s" for d in decades])
        self.clear_filters()
        
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
        answer = messagebox.askyesno(
        "Update available",
        f"Version {self.update_version} is available. Update now?")
        if answer:
            self.update_label.pack_forget()
            if self.update_url:
                target = Path(tempfile.gettempdir()) / "BoxdListSetup.exe"
                if download_file(self.update_url, target):
                    self.install_update(target)
                else:
                    messagebox.showerror("Update", "Download failed.")
            else:
                messagebox.showerror("Update Error", "Could not find the installer link.")
        else: 
            self.update_label.pack(side="right", padx=8)
        
    def install_update(self, installer: Path) -> None:
        try:
            # starts the installer as a separate program; our app does not wait for it
            subprocess.Popen([str(installer), "/SILENT", "/CLOSEAPPLICATIONS"])
        except OSError:
            messagebox.showerror("Update", "Could not start the installer.")
            return
        self.on_close()

    # ----- Filters -----
    def select_decade(self) -> None:
        choice = self.decade.get()
        if choice == NO_FILTER:
            self.year_min.set("")
            self.year_max.set("")
        else:
            start = int(choice[:-1])  # "1990s" -> 1990
            self.year_min.set(str(start))
            self.year_max.set(str(start + 9))

    def clear_filters(self) -> None:
        # Each set() triggers scheduling; only one update runs at the end
        self.search.set("")
        self.year_min.set("")
        self.year_max.set("")
        self.decade.set(NO_FILTER)
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
        self.visible = sort_movies(
            filtered, self.sort_column, self.reverse_order)
        if not any(m is self.last_randomized for m in self.visible):
            self.result.config(text="")

        self.table.delete(*self.table.get_children())
        # iid = index in self.visible, to recover the selected movie
        for i, f in enumerate(self.visible):
            self.table.insert("", "end", iid=str(i),
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
        """Shows the random and export buttons only when movies are visible."""
        buttons = (self.open_button, self.random_button, self.export_button)
        if self.visible:
            # each one goes right before the result label, so the order stays
            for button in buttons:
                button.pack(side="left", padx=(0, 6), before=self.result)
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

    def open_link(self) -> None:
        sel = self.table.selection()
        if not sel:
            return
        url = self.visible[int(sel[0])]["url"]
        if url:
            webbrowser.open(url)
        else:
            messagebox.showinfo("BoxdList", "This movie has no link.")

    def on_double_click(self, event) -> None:
        """Opens the link only if a row was double-clicked, not the header."""
        # identify_region returns "heading", "cell" or "nothing"
        if self.table.identify_region(event.x, event.y) == "cell":
            self.open_link()
            
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
        data["window_size"] = f"{self.winfo_width()}x{self.winfo_height()}"
        write_config(data)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()