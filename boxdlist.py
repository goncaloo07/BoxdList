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

# ---------- Constants ----------
# (internal id, header title, width in pixels)
COLUMNS = (("name", "Movie", 440),
           ("year", "Year", 70),
           ("date", "Added", 110))
MANDATORY = {"Date", "Name", "Year", "Letterboxd URI"}
CONFIG_FILE = Path.home() / ".watchlist_config.json"
SEARCH_DELAY_MS = 200  # wait after the last keypress before filtering
NO_FILTER = "All"
EMPTY = "—"            # shown in the table when data is missing
VALID_YEAR = re.compile(r"\d{4}")
VALID_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


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


def is_incomplete(movie: dict) -> bool:
    """True if year, date, or link is missing."""
    return not (movie["year"] and movie["date"] and movie["url"])


def year_as_int(value: str) -> int | None:
    """Converts a year string to int; returns None if invalid."""
    value = value.strip()
    return int(value) if value.isdigit() else None


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


# ---------- Configuration (remember the last CSV) ----------
def read_last_path() -> str | None:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data.get("ultimo_csv")
    except (OSError, ValueError):
        return None


def save_last_path(path: str) -> None:
    try:
        CONFIG_FILE.write_text(
            json.dumps({"ultimo_csv": path}), encoding="utf-8")
    except OSError:
        pass  # not critical: the app still works fine
    
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
        self.title("BoxdList")
        self.geometry("760x600")
        self.set_icon()

        self.movies: list[dict] = []     # original list, in CSV order
        self.visible: list[dict] = []    # filtered and sorted (what is seen)
        self.sort_column: str | None = None
        self.reverse_order = False
        self.last_randomized: dict | None = None
        self._search_job = None          # pending after() ID (debounce)

        # ---------- Row 1: Import, search, and counter ----------
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="Import CSV (Ctrl+O)",
                   command=self.import_csv).pack(side="left", padx=(0, 10))

        ttk.Label(top, text="Search:").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self.schedule_update())
        ttk.Entry(top, textvariable=self.search).pack(
            side="left", fill="x", expand=True, padx=6)

        self.counter = ttk.Label(top, text="")
        self.counter.pack(side="right")

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

        self.table.bind("<Double-1>", lambda _: self.open_link())

        # ---------- Bottom Section ----------
        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")

        ttk.Button(bottom, text="Random Movie",
                   command=self.random_movie).pack(side="left")
        ttk.Button(bottom, text="Open in Letterboxd (Enter)",
                   command=self.open_link).pack(side="left", padx=6)

        self.result = ttk.Label(
            bottom, text="", font=("TkDefaultFont", 11, "bold"))
        self.result.pack(side="left", padx=12)

        # ---------- Keyboard Shortcuts ----------
        self.bind("<Control-o>", lambda _: self.import_csv())
        self.bind("<Return>", lambda _: self.open_link())

        self.update_list()
        self.load_last_csv()
        
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

        filtered = filter_movies(self.movies, self.search.get(),
                                 year_as_int(self.year_min.get()),
                                 year_as_int(self.year_max.get()))
        self.visible = sort_movies(
            filtered, self.sort_column, self.reverse_order)

        self.table.delete(*self.table.get_children())
        # iid = index in self.visible, to recover the selected movie
        for i, f in enumerate(self.visible):
            self.table.insert("", "end", iid=str(i),
                              values=(f["name"], f["year"] or EMPTY, f["date"] or EMPTY))

        self.counter.config(text=f"{len(self.visible)} / {len(self.movies)}")
        self.update_headers()
        self.update_warning()

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
        iid = str(self.visible.index(f))

        self.table.selection_set(iid)
        self.table.see(iid)
        year = f" ({f['year']})" if f["year"] else ""
        self.result.config(text=f["name"] + year)

    def open_link(self) -> None:
        sel = self.table.selection()
        if not sel:
            return
        url = self.visible[int(sel[0])]["url"]
        if url:
            webbrowser.open(url)
        else:
            messagebox.showinfo("BoxdList", "This movie has no link.")


if __name__ == "__main__":
    App().mainloop()