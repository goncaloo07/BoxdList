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
import urllib.parse
import urllib.error
import threading
import tempfile
import subprocess
import ctypes
from datetime import date, timedelta
import textwrap
from concurrent.futures import ThreadPoolExecutor
import gzip
import math
import io
from PIL import Image, ImageTk
  
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
DETAILS_WIDTH = 260
DEFAULT_SIZE = f"{760 + DETAILS_WIDTH}x600"
VALID_SIZE = re.compile(r"\d{3,4}x\d{3,4}(\+-?\d+\+-?\d+)?")
MIN_VISIBLE = 200
APP_VERSION = "1.2.0"
RELEASES_API = "https://api.github.com/repos/goncaloo07/BoxdList/releases/latest"
DETAILS_API = "https://boxdlist-proxy.vercel.app/api/movie"
POSTER_URL = "https://image.tmdb.org/t/p/w185"
POSTER_WORKERS = 4 
RECENT_OPTIONS = {NO_FILTER: None, "Last 7 days": 7,
                  "Last 30 days": 30, "Last year": 365}
MAX_OPEN_AT_ONCE = 5 # above this, ask before opening many pages
DEFAULT_SORT_COLUMN = "date"   # sort used when nothing valid was saved
SUMMARY_HEADER_LINES = 2   # "3 movies selected" and a blank line
DETAIL_LINES = 6           # title, genres, rating, 2 lines of synopsis, blank line
MIN_DETAILED = 3           # fewer detailed movies than this: names only
DETAILS_CACHE_FILE = app_folder() / "data" / "details_cache.json"
DETAILS_WORKERS = 12
NOT_FOUND = {"not_found": True}
RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"    
RATINGS_CACHE_FILE = app_folder() / "data" / "ratings_cache.json"
RATINGS_MAX_AGE_DAYS = 5
NOT_FOUND_RETRY_DAYS = 5   # ask TMDB again about unknown films after this many days
PULSE_MS = 40          # time between small color steps of the skeleton bars
PULSE_CYCLE_MS = 1200  # time for one full pulse (dark, light, dark)
DETAILS_REFRESH_MS = 500   # wait before redrawing the list when new data arrives
POSTER_WIDTH = 185    # same size as the "w185" images from TMDB
POSTER_HEIGHT = 278   # posters are 2:3, so this is the grey block size
POSTER_MIN_HEIGHT = 60   # below this the poster is hidden, the text matters more
PANEL_MARGIN = 30        # space for the gaps between the labels of the panel

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


# ---------- Logic ----------
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

def parse_number(text: str) -> float | None:
    """'7.5' or '7,5' -> 7.5. Empty, invalid or negative -> None."""
    try:
        value = float(text.strip().replace(",", "."))   # accepts a comma too
    except ValueError:
        return None
    # float() also accepts "nan" and "inf", which make no sense here
    return value if math.isfinite(value) and value >= 0 else None

def number_range_error(low: str, high: str, label: str) -> str | None:
    """Short message if the two fields are invalid, else None."""
    values = []
    for text in (low.strip(), high.strip()):
        # empty is fine (no limit), but anything else must be a number
        if text and parse_number(text) is None:
            return f"{label} must be a number"
        values.append(parse_number(text) if text else None)
    if values[0] is not None and values[1] is not None and values[0] > values[1]:
        return f"{label}: 'From' is after 'to'"
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

def filter_complete(movies: list[dict]) -> list[dict]:
    """Keeps only the movies that have a year, date, and link."""
    return [m for m in movies if not is_incomplete(m)]

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
    
def is_not_found(details) -> bool:
    """True if 'details' is the marker for a movie that TMDB does not know."""
    return isinstance(details, dict) and details.get("not_found") is True

def is_proxy_not_found(error: urllib.error.HTTPError) -> bool:
    """True only if our proxy said 404, not a wrong address (Vercel has its own 404)."""
    try:
        body = json.loads(error.read().decode("utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(body, dict) and body.get("error") == "Not found"
    
def fetch_movie_details(name: str, year: str) -> dict | None:
    """Asks the proxy for genres, runtime and synopsis.
    Returns a copy of NOT_FOUND if TMDB does not know the movie,
    and None if anything else fails (so it can be tried again later).
    """
    params = {"name": name}
    if year:
        params["year"] = year   # some movies have no year
    url = f"{DETAILS_API}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            text = response.read().decode("utf-8")
        result = json.loads(text)
        if not isinstance(result, dict):
            return None
        return result
    except urllib.error.HTTPError as error:
        # HTTPError is a kind of OSError, so it must come first
        if error.code == 404 and is_proxy_not_found(error):
            return dict(NOT_FOUND)   # a copy, so nobody changes the constant
        return None
    except (OSError, ValueError):
        return None
    
def fetch_poster(poster_path) -> bytes | None:
    """Downloads a poster. Returns the image bytes, or None if anything fails."""
    # only a text that starts with "/" makes a valid address
    if not isinstance(poster_path, str) or not poster_path.startswith("/"):
        return None
    try:
        with urllib.request.urlopen(f"{POSTER_URL}{poster_path}", timeout=5) as response:
            bits = response.read()
    except (OSError, ValueError):
        return None
    return bits or None
    
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

def key_to_text(key: tuple[str, str]) -> str:
    """('Amélie', '2001') -> 'Amélie|2001'. JSON keys must be text."""
    return f"{key[0]}|{key[1]}"

def text_to_key(text: str) -> tuple[str, str]:
    """'Amélie|2001' -> ('Amélie', '2001')."""
    name, year = text.rsplit("|", 1)  # rsplit, in case the name has a "|"
    return (name, year)

def count_new_movies(old: list[dict], new: list[dict]) -> int:
    """Counts movies in 'new' that were not in 'old'."""
    known = {movie_key(m) for m in old}   # a set makes the lookup fast
    return sum(1 for m in new if movie_key(m) not in known)

def count_duplicates(movies: list[dict]) -> int:
    """Counts the extra copies: the same movie 3 times counts as 2."""
    seen = set()
    count = 0
    for m in movies:
        key = movie_key(m)
        if key in seen:
            count += 1
        else:
            seen.add(key)
    return count

def details_outdated(details: dict) -> bool:
    """True if the details were saved before the proxy sent 'imdb_id' or 'poster_path'."""
    if is_not_found(details):
        return False
    return "imdb_id" not in details or "poster_path" not in details

def not_found_expired(details: dict, today: date) -> bool:
    """True for an old 'not found' marker, or one saved without a date by an older version."""
    if not is_not_found(details):
        return False
    try:
        checked = date.fromisoformat(details.get("checked", ""))
    except (ValueError, TypeError):
        return True   # no date or a broken one: ask again
    # a date in the future (the PC clock changed) counts as expired too
    return checked > today or (today - checked).days >= NOT_FOUND_RETRY_DAYS

def drop_expired_not_found(cache: dict, today: date) -> dict:
    """Returns a copy of the cache without the 'not found' markers that are due again."""
    return {key: details for key, details in cache.items()
            if not not_found_expired(details, today)}

def movies_to_fetch(movies: list[dict], cache: dict, skip: set) -> list[dict]:
    """Movies that still need details, one per name+year."""
    seen = set()
    result = []
    for m in movies:
        key = movie_key(m)
        # in the cache AND up to date: nothing to fetch
        known = key in cache and not details_outdated(cache[key])
        if known or key in skip or key in seen:
            continue
        seen.add(key)
        result.append(m)
    return result

def format_runtime(minutes) -> str:
    """122 -> '2h 02min', 45 -> '45min', missing or invalid -> ''."""
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        return ""
    hours, mins = divmod(minutes, 60)
    if hours == 0:
        return f"{mins}min"
    return f"{hours}h {mins:02d}min"

def format_rating(rating, votes) -> str:
    """8.2, 850628 -> '★ 8.2/10 (850k votes)'. Missing or invalid -> ''."""
    # bool is a kind of int in Python, so it is excluded by hand
    if (isinstance(rating, bool) or not isinstance(rating, (int, float))
            or isinstance(votes, bool) or not isinstance(votes, int)):
        return ""
    if not 0 < rating <= 10 or votes < 0:
        return ""
    if votes >= 1_000_000:
        shown = f"{votes / 1_000_000:.1f}M"
    elif votes >= 1000:
        shown = f"{votes // 1000}k"   # whole thousands, so 999999 gives 999k
    else:
        shown = str(votes)
    return f"★ {rating:.1f}/10 ({shown} votes)"

def load_poster_image(data: bytes, width: int, max_height: int | None = None):
    """Opens image bytes and scales them to 'width'. Returns None if the image is broken.
    If 'max_height' is given, the image is made smaller to fit it, keeping the ratio.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()   # forces the decoding, so a broken file fails here
    except (OSError, ValueError):
        return None
    height = round(image.height * width / image.width)
    if max_height is not None and height > max_height:
        height = max(1, max_height)
        width = max(1, round(image.width * height / image.height))
    return image.convert("RGB").resize((width, height), Image.LANCZOS)

def rating_line(details: dict, ratings: dict) -> str:
    """'★ 8.2/10 (850k votes)'. Empty if there is no id or no rating yet."""
    imdb_id = details.get("imdb_id")
    # the proxy sends null when TMDB has no id
    if not isinstance(imdb_id, str):
        return ""
    entry = ratings.get(imdb_id)
    # format_rating gives "" for invalid values
    return format_rating(*entry) if entry else ""

def details_line(details: dict) -> str:
    """'Comedy, Romance · 2h 02min'. Leaves out whatever is missing."""
    parts = []
    genres = details.get("genres")
    if isinstance(genres, list):
        names = [g for g in genres if isinstance(g, str)]   # keep only real text
        if names:
            parts.append(", ".join(names))   # "Comedy, Romance"
    runtime = format_runtime(details.get("runtime"))
    if runtime:
        parts.append(runtime)
    return " · ".join(parts)

def read_ratings(path: Path, wanted: set[str]) -> dict[str, tuple[float, int]]:
    """Reads the IMDb ratings file and keeps only the ids in 'wanted'."""
    ratings = {}
    # "rt" makes the .gz file behave like a normal text file
    with gzip.open(path, "rt", encoding="utf-8") as f:
        next(f, None)   # skips the header line
        for line in f:
            try:
                tconst, rating, votes = line.rstrip("\n").split("\t")
                if tconst not in wanted:
                    continue
                ratings[tconst] = (float(rating), int(votes))
            except ValueError:
                continue   # broken line: skip it
    return ratings

def download_ratings() -> Path | None:
    """Downloads the IMDb ratings file. Returns its path, or None if it failed."""
    target = Path(tempfile.gettempdir()) / "boxdlist_ratings.tsv.gz"
    if download_file(RATINGS_URL, target):
        return target
    target.unlink(missing_ok=True)   # a cut file must not stay behind
    return None

def read_downloaded_ratings(path: Path, wanted: set[str]) -> dict[str, tuple[float, int]] | None:
    """Reads a downloaded ratings file, then deletes it.
    Returns None if the file is broken, so it can be tried again later.
    """
    try:
        return read_ratings(path, wanted)
    except (OSError, EOFError):
        return None   # broken or cut file
    finally:
        path.unlink(missing_ok=True)   # never leave the big file behind
        
def write_ratings_cache(ratings: dict, today: date) -> None:
    """Saves the ratings and the update date. Not critical, so errors are ignored."""
    data = {"updated": today.isoformat(), "ratings": ratings}
    try:
        RATINGS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        RATINGS_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    
def read_ratings_cache() -> tuple[dict[str, tuple[float, int]], date | None]:
    """Returns (ratings, update date). Empty and None if there is a problem."""
    try:
        data = json.loads(RATINGS_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, None   # missing or broken file
    if not isinstance(data, dict):
        return {}, None
    try:
        updated = date.fromisoformat(data.get("updated", ""))
    except (ValueError, TypeError):
        updated = None   # missing or invalid date
    saved = data.get("ratings")
    if not isinstance(saved, dict):
        return {}, updated
    ratings = {}
    for imdb_id, value in saved.items():
        # JSON gives lists, so we check the shape and turn them back into tuples
        if (isinstance(value, list) and len(value) == 2
                and isinstance(value[0], (int, float)) and not isinstance(value[0], bool)
                and isinstance(value[1], int) and not isinstance(value[1], bool)):
            ratings[imdb_id] = (float(value[0]), value[1])
    return ratings, updated        

def wanted_imdb_ids(movies: list[dict], cache: dict) -> set[str]:
    """IMDb ids of the movies in the list, taken from the details cache."""
    ids = set()
    for m in movies:
        details = cache.get(movie_key(m))
        if not details:
            continue   # no details yet
        imdb_id = details.get("imdb_id")
        # the proxy sends null when TMDB has no id, and "not found" has none
        if not isinstance(imdb_id, str) or not imdb_id:
            continue
        ids.add(imdb_id)
    return ids

def ratings_need_update(updated: date | None, today: date) -> bool:
    """True if the saved ratings are missing or too old."""
    return (updated is None or updated > today
            or (today - updated).days >= RATINGS_MAX_AGE_DAYS)

def ratings_need_refresh(updated: date | None, today: date,
                         wanted: set[str], ratings: dict) -> bool:
    """True if the ratings are too old, or a film in the list was never looked up."""
    if ratings_need_update(updated, today):
        return True
    return any(imdb_id not in ratings for imdb_id in wanted)

def with_unrated_marks(ratings: dict, wanted: set[str]) -> dict:
    """Adds (0.0, 0) for every wanted id that has no IMDb rating.
    That way the id counts as looked up and does not trigger another download.
    format_rating shows nothing for it, so the film still has no rating on screen.
    """
    marked = dict(ratings)
    for imdb_id in wanted:
        marked.setdefault(imdb_id, (0.0, 0))
    return marked

def plan_summary(total: int, lines: int) -> tuple[bool, int]:
    """Decides how the list of selected movies looks.
    Returns (with_details, how_many_movies_to_show).
    """
    usable = lines - SUMMARY_HEADER_LINES
    # every movie fits, with details
    if total * DETAIL_LINES <= usable:
        return True, total
    # only some fit with details; one line is kept for "+ N more"
    shown = (usable - 1) // DETAIL_LINES
    if shown >= MIN_DETAILED:
        return True, shown
    # too little space for details: names only
    if total <= usable:
        return False, total
    return False, max(usable - 1, 0)

def skeleton_line(width: int, fraction: float) -> str:
    """A bar of block characters that fills part of the line."""
    # int() rounds down, so the bar is never wider than the line
    return "█" * max(1, int(width * fraction))

def mix_colors(start: str, end: str, t: float) -> str:
    """Blends two '#RRGGBB' colors. t=0 gives start, t=1 gives end."""
    a = [int(start[i:i + 2], 16) for i in (1, 3, 5)]   # red, green, blue
    b = [int(end[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}"
                        for x, y in zip(a, b))

def build_summary(movies, shown, with_details, width, cache, loading,
                  ratings: dict | None = None,
                  ratings_pending: bool = False) -> list[tuple[str, str]]:
    """Lines of the multi-selection list, as (text, style) pairs.
    'width' is how many characters fit in one line. 'cache' holds the
    details we already have and 'loading' the movies being requested.
    'ratings_pending' is True while the IMDb ratings are still on their way.
    """
    lines = [(f"{len(movies)} movies selected", "header"), ("", "")]
    for movie in movies[:shown]:
        year = f" ({movie['year']})" if movie["year"] else ""
        title = textwrap.shorten(movie["name"] + year, width, placeholder="...")
        lines.append((title, "title"))
        if not with_details:
            continue
        key = movie_key(movie)
        details = cache.get(key)
        if not details and key in loading:
            # same 4 lines the real details use, so the list does not jump
            for fraction in (0.6, 0.35, 0.95, 0.7):
                lines.append((skeleton_line(width, fraction), "skeleton"))
            lines.append(("", ""))
            continue
        meta = ""
        rating_text, rating_style = "", "meta"
        if details:
            meta = details_line(details)   # genres and runtime only
            rating = rating_line(details, ratings or {})
            has_id = isinstance(details.get("imdb_id"), str)
            if has_id and not rating and ratings_pending:
                # details are here, only the rating is missing
                rating_text, rating_style = skeleton_line(width, 0.35), "skeleton"
            else:
                rating_text = rating
        lines.append((textwrap.shorten(meta, width, placeholder="..."), "meta"))
        # always one line, even when empty, so the list does not jump
        lines.append((textwrap.shorten(rating_text, width, placeholder="..."),
                      rating_style))
        overview = details.get("overview") if details else None
        text = overview.strip() if isinstance(overview, str) else ""
        wrapped = (textwrap.wrap(text, width, max_lines=2, placeholder="...")
                   if text else [])
        wrapped += [""] * (2 - len(wrapped))   # always 2 lines, so nothing jumps
        lines += [(line, "overview") for line in wrapped]
        lines.append(("", ""))
    if shown < len(movies):
        lines.append((f"+ {len(movies) - shown} more", "meta"))
    return lines

def movie_genres(details: dict | None) -> list[str]:
    """Genres of a movie, or an empty list if unknown."""
    genres = []
    if details is None:
        return genres
    raw = details.get("genres")
    if not isinstance(raw,list):
        return genres
    for g in raw:
        if isinstance(g, str):
            genres.append(g)
    return genres

def available_genres(movies: list[dict], cache: dict) -> list[str]:
    """Sorted genres found in the movies of the list, without repeats."""
    found = set()
    for m in movies:
        found.update(movie_genres(cache.get(movie_key(m))))
    return sorted(found)

def movie_runtime(details: dict | None) -> int | None:
    """Runtime in minutes, or None if unknown."""
    if details is None:
        return None
    runtime = details.get("runtime")
    # accept only a whole number bigger than 0
    if not (isinstance(runtime, int) and not isinstance(runtime, bool)) or runtime <= 0:
        return None
    return runtime

def movie_rating(details: dict | None, ratings: dict) -> float | None:
    """IMDb rating, or None if unknown."""
    if details is None:
        return None
    imdb_id = details.get("imdb_id")
    if not isinstance(imdb_id, str):
        return None
    entry = ratings.get(imdb_id)
    if entry is None:
        return None
    rating, votes = entry
    if rating <= 0 or votes <= 0:
        return None
    return rating

def filter_by_details(movies: list[dict], cache: dict,
                      genres: list[str],
                      runtime_min: int | None = None,
                      runtime_max: int | None = None,
                      ratings: dict | None = None,
                      rating_min: float | None = None,
                      rating_max: float | None = None) -> list[dict]:
    """Keeps only the movies that match the chosen genres, runtime and rating limits."""
    # no genres chosen: the filter is off, so everything passes
    if not genres and runtime_min is None and runtime_max is None and rating_max is None and rating_min is None:
        return list(movies)
    wanted = set(genres)
    ratings = ratings or {}
    result = []
    for m in movies:
        details = cache.get(movie_key(m))
        # a movie without details has no genres, so it is hidden
        if wanted <= set(movie_genres(details)):  
            if runtime_min is not None or runtime_max is not None:
                runtime = movie_runtime(details)
                if runtime is None: 
                   continue
                if runtime_min is not None and runtime < runtime_min:
                    continue
                if runtime_max is not None and runtime > runtime_max:
                    continue
            if rating_min is not None or rating_max is not None:
                rating = movie_rating(details, ratings)
                if rating is None: 
                    continue
                if rating_min is not None and rating < rating_min:
                    continue
                if rating_max is not None and rating > rating_max:
                    continue
            result.append(m) 
    return result
    
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
    
def read_details_cache() -> dict:
    """Returns the saved details, or an empty dict if there is a problem."""
    try:
        data = json.loads(DETAILS_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    cache = {}
    for text, details in data.items():
        # skip anything that looks wrong instead of crashing
        if "|" in text and isinstance(details, dict):
            cache[text_to_key(text)] = details
    return cache

def write_details_cache(cache: dict) -> None:
    """Saves the details to disk. Not critical, so errors are ignored."""
    data = {key_to_text(k): v for k, v in cache.items()}
    try:
        DETAILS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DETAILS_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # the app still works without the cache

def write_config(data: dict) -> None:
    """Saves all settings to the config file."""
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(data), encoding="utf-8")
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

def initial_sort(config: dict) -> tuple[str, bool]:
    """Returns (column, reverse) to start with: the saved sort, or the default."""
    column = valid_sort_column(config.get("sort_column"))
    if column is None:
        # nothing valid saved: oldest added first
        return DEFAULT_SORT_COLUMN, False
    return column, valid_reverse_order(config.get("reverse_order"))

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
        self.minsize(self.px(700 + DETAILS_WIDTH), self.px(400))
        self.set_icon()
        self.apply_theme()

        self.movies: list[dict] = []     # original list, in CSV order
        self.visible: list[dict] = []    # filtered and sorted (what is seen)
        self.sort_column, self.reverse_order = initial_sort(config)
        self.last_randomized: dict | None = None
        self.update_version: str | None = None
        self.update_url: str | None = None
        self._search_job = None          # pending after() ID (debounce)
        self._refresh_job = None   # pending after() ID of the details refresh
        self.downloading = False   # True while the installer is being downloaded
        self.last_deleted: list[tuple[dict, int]] | None = None
        self.details_cache: dict[tuple[str, str], dict] = drop_expired_not_found(read_details_cache(), date.today())   # movie_key -> details that worked
        self.details_target: dict | None = None   # the movie the panel is waiting for
        self.summary_movies: list[dict] = []   # the selected movies, when there are several
        self.details_inflight: set[tuple[str, str]] = set()   # requests in progress
        self.details_failed: set[tuple[str, str]] = set()     # requests that failed
        self.posters: dict[tuple[str, str], bytes] = {}   # movie_key -> image bytes
        self.posters_inflight: set[tuple[str, str]] = set()   # requests in progress
        self.posters_failed: set[tuple[str, str]] = set()     # requests that failed
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.executor = ThreadPoolExecutor(max_workers=DETAILS_WORKERS)
        self.prefetch_total = 0   # how many movies this round has to fetch
        self.prefetch_done = 0    # how many already answered
        # ratings saved on disk (imdb id -> (rating, votes)) and when they were downloaded
        self.ratings, self.ratings_updated = read_ratings_cache()
        self.ratings_downloading = False   # True while the ratings file is downloading
        self.ratings_file: Path | None = None   # downloaded file, waiting to be read
        self.ratings_loading = False   # True while that file is being read
        
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
        
        self.complete_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(filters, text="Complete only",
                        variable=self.complete_only,
                        command=self.update_list).pack(side="left", padx=(self.px(10), 0))

        ttk.Button(filters, text="Clear filters",
                   command=self.clear_filters).pack(side="left", padx=self.px(10))

        # Red message for invalid year fields (empty when everything is fine)
        self.year_error = ttk.Label(filters, text="", foreground="red")
        self.year_error.pack(side="left")
        
        # Green note about new movies after an import (under the counter)
        self.new_label = ttk.Label(filters, text="", foreground=GREEN)
        self.new_label.pack(side="right")
        
        # ---------- Row 3: runtime and rating (genre comes next) ----------
        more_filters = ttk.Frame(self, padding=(self.px(12), 0, self.px(12), self.px(8)))
        more_filters.pack(fill="x")

        self.runtime_min, self.runtime_max = tk.StringVar(), tk.StringVar()
        self.rating_min, self.rating_max = tk.StringVar(), tk.StringVar()
        ranges = (("Runtime (min)", self.runtime_min, self.runtime_max),
                ("IMDb rating", self.rating_min, self.rating_max))
        for i, (label, low_var, high_var) in enumerate(ranges):
            # extra space before the second group
            ttk.Label(more_filters, text=label, foreground=MUTED).pack(
                side="left", padx=(0 if i == 0 else self.px(16), self.px(6)))
            for var, hint in ((low_var, "From"), (high_var, "To")):
                entry = ttk.Entry(more_filters, textvariable=var, width=7)
                entry.pack(side="left", padx=(0, self.px(6)))
                self.add_placeholder(entry, var, hint)
                var.trace_add("write", lambda *_: self.schedule_update())
                
        # Genre: a button that opens a menu with one checkbox per genre
        self.genre_vars: dict[str, tk.BooleanVar] = {}   # genre -> checked or not
        ttk.Label(more_filters, text="Genre", foreground=MUTED).pack(
            side="left", padx=(self.px(16), self.px(6)))
        self.genre_button = ttk.Menubutton(more_filters, text="Choose...", width=10)
        self.genre_menu = tk.Menu(self.genre_button, tearoff=0, bg=SURFACE, fg=TEXT,
                                activebackground=GREEN, activeforeground=TEXT,
                                selectcolor=TEXT, borderwidth=0, relief="flat",
                                postcommand=self.update_genre_menu)   # runs before it opens
        self.genre_button["menu"] = self.genre_menu
        self.genre_button.pack(side="left")
        # Chips: one small label per chosen genre, click one to remove it
        self.genre_chips = ttk.Frame(more_filters)
        self.genre_chips.pack(side="left", padx=(self.px(6), 0))
        # Red message for invalid runtime or rating fields
        self.details_error = ttk.Label(more_filters, text="", foreground="red")
        self.details_error.pack(side="left", padx=(self.px(10), 0))

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

        self.scrollbar = ttk.Scrollbar(middle, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=self.scrollbar.set)
        
        # ---------- Details panel (right of the table) ----------
        panel_width = self.px(DETAILS_WIDTH)
        self.details = ttk.Frame(middle, width=panel_width,
                                 padding=(self.px(12), self.px(4), 0, 0))
        self.details.pack_propagate(False)   # keep the fixed width
        wrap = panel_width - self.px(12)     # text breaks lines to fit the panel

        # One movie selected: title, genres and synopsis
        self.single_view = ttk.Frame(self.details)
        self.single_view.bind("<Configure>", lambda _: self.schedule_fit())
        self.details_title = ttk.Label(self.single_view, text="", wraplength=wrap,
                               justify="left",
                               font=("Segoe UI", 12, "bold"))
        self.details_title.pack(anchor="w")
        self.details_meta = ttk.Label(self.single_view, text="", wraplength=wrap,
                                    justify="left", foreground=MUTED)
        self.details_meta.pack(anchor="w", pady=(self.px(4), 0))
        self.details_rating = ttk.Label(self.single_view, text="", wraplength=wrap,
                                justify="left", foreground=ORANGE,
                                font=("Segoe UI", 9, "bold"))
        self.details_rating.pack(anchor="w", pady=(self.px(4), 0))
        # Poster: the image, and a grey block that stands in for it while it loads
        self.poster_label = tk.Label(self.single_view, bg=BG, bd=0,
                             highlightthickness=1,   # thin frame around the image
                             highlightbackground=SCROLL, highlightcolor=SCROLL)
        self.poster_photo = None   # keep a reference or the image disappears
        self.poster_bar = tk.Frame(self.single_view, width=self.px(POSTER_WIDTH),
                                height=self.px(POSTER_HEIGHT), bg=ROW_ALT)
        self.details_overview = ttk.Label(self.single_view, text="", wraplength=wrap,
                                          justify="left")
        self.details_overview.pack(anchor="w", pady=(self.px(12), 0))
        
        # Skeleton: grey bars that stand in for the text while it loads
        self.skeleton = ttk.Frame(self.single_view)
        self.skeleton_bars = []
        for width, height in ((140, 10), (100, 10), (230, 10), (200, 10)):
            # a Frame with a fixed size and a background color looks like a bar
            bar = tk.Frame(self.skeleton, width=self.px(width),
                        height=self.px(height), bg=ROW_ALT)
            bar.pack(anchor="w", pady=(self.px(6), 0))
            self.skeleton_bars.append(bar)   # saved, so we can change their color later
            
        # Single bar for the rating line, used when only the rating is missing
        self.rating_bar = tk.Frame(self.single_view, width=self.px(100),
                                height=self.px(10), bg=ROW_ALT)
        
        self._pulse_job = None      # pending after() ID, None when not pulsing
        self._pulse_phase = 0.0   # position in the pulse cycle, from 0 to 1
        
        self.overview_full = ""   # the whole synopsis, before any cut to fit the panel
        self._fit_job = None      # pending after() ID of the panel refit

        # Several movies selected: a list that adapts to the space it has.
        # Every line is cut by hand (wrap="none"), so we know how many lines it uses.
        self.summary_font = font.Font(family="Segoe UI", size=9)
        self.summary = tk.Text(self.details, width=1, height=1, wrap="none",
                               bg=BG, fg=TEXT, font=self.summary_font,
                               bd=0, highlightthickness=0, padx=0, pady=0,
                               cursor="arrow", state="disabled", takefocus=0)
        self.summary.tag_configure("header", font=("Segoe UI", 11, "bold"))
        self.summary.tag_configure("title", font=("Segoe UI", 9, "bold"))
        self.summary.tag_configure("meta", foreground=MUTED)
        self.summary.bind("<Button-1>", lambda _: "break")   # no focus, no selection
        # a bigger or smaller window changes how much fits
        self.summary.bind("<Configure>", lambda _: self.render_summary())
        # grey blocks that stand in for text while it loads
        self.summary.tag_configure("skeleton", foreground=ROW_ALT)
        
        self.scrollbar.pack(side="right", fill="y", padx=(self.px(4), 0))
        self.table.pack(side="left", fill="both", expand=True)

        # Warning message overlay when there is nothing to display
        self.warning = ttk.Label(self.table, text="", foreground=MUTED)
        self.table.bind("<Double-1>", self.on_double_click)

        # ---------- Bottom Section ----------
        bottom = ttk.Frame(self, padding=self.px(12))
        bottom.pack(side="bottom", fill="x", before=middle)

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
        self.bind("<Escape>", lambda _: self.clear_selection())
        self.bind("<Control-BackSpace>", lambda _: self.clear_filters())
        
        self.table.bind("<<TreeviewSelect>>", lambda _: self.on_select())

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
        # Complete only checkbox
        style.configure("TCheckbutton", background=BG, foreground=MUTED,
                        indicatorbackground=SURFACE, indicatorforeground=TEXT,
                        upperbordercolor=SCROLL, lowerbordercolor=SCROLL,
                        focuscolor=BG,   # hides the dotted focus line
                        indicatormargin=(0, 0, self.px(6), 0))   # space before the text
        style.map("TCheckbutton",
                background=[("active", BG)],
                foreground=[("active", TEXT)],
                indicatorbackground=[("selected", "active", GREEN_HOVER),
                                    ("selected", GREEN),
                                    ("active", ROW_ALT)],
                upperbordercolor=[("selected", GREEN)],
                lowerbordercolor=[("selected", GREEN)])
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
        # Genre button: same look as the other dark controls
        style.configure("TMenubutton", background=SURFACE, foreground=TEXT,
                        arrowcolor=TEXT, bordercolor=SURFACE, lightcolor=SURFACE,
                        darkcolor=SURFACE, borderwidth=0,
                        padding=(self.px(6), self.px(4)))
        style.map("TMenubutton", background=[("active", ROW_ALT)])
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
                self.prefetch_details()
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
        self.prefetch_details()
        
        incomplete = sum(is_incomplete(f) for f in movies)
        duplicates = count_duplicates(movies)
        if ignored or incomplete or duplicates:
            messagebox.showwarning(
                "Import",
                f"{len(movies)} movies imported.\n"
                f"- {ignored} lines ignored (missing name or malformed)\n"
                f"- {incomplete} movies with missing data (shown with {EMPTY})\n"
                f"- {duplicates} duplicate {'movie' if duplicates == 1 else 'movies'} (same name and year)")
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
        
    def show_status(self, text: str, keep: bool = False) -> None:
        """Shows a short message that clears itself after 3 seconds.
        With keep=True it stays until the next message replaces it.
        """
        if self._status_job is not None:
            self.after_cancel(self._status_job)
            self._status_job = None
        self.status.config(text=text)
        if keep:
            return   # no timer, so the text stays
        self._status_job = self.after(3000, lambda: self.status.config(text=""))
        
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
            
    def update_genre_menu(self) -> None:
        """Rebuilds the genre menu. Runs every time the menu opens."""
        self.genre_menu.delete(0, "end")
        genres = available_genres(self.movies, self.details_cache)
        if not genres:
            self.genre_menu.add_command(label="No genres yet", state="disabled")
            return
        for genre in genres:
            # setdefault keeps the checkbox of a genre chosen before
            var = self.genre_vars.setdefault(genre, tk.BooleanVar())
            self.genre_menu.add_checkbutton(label=genre, variable=var,
                                            command=self.on_genre_change)

    def selected_genres(self) -> list[str]:
        """The genres that are checked right now."""
        return [g for g, var in self.genre_vars.items() if var.get()]

    def on_genre_change(self) -> None:
        """Redraws the chips and updates the list after a genre changed."""
        self.draw_genre_chips()
        self.update_list()

    def draw_genre_chips(self) -> None:
        """One small green label for each chosen genre."""
        for chip in self.genre_chips.winfo_children():
            chip.destroy()   # start from zero, there are only a few
        for genre in self.selected_genres():
            chip = tk.Label(self.genre_chips, text=f"{genre}  ✕", bg=GREEN, fg=TEXT,
                            padx=self.px(6), pady=self.px(2), cursor="hand2")
            chip.pack(side="left", padx=(0, self.px(4)))
            # g=genre fixes the value of genre in each loop iteration
            chip.bind("<Button-1>", lambda _, g=genre: self.remove_genre(g))

    def remove_genre(self, genre: str) -> None:
        """Unchecks a genre. The menu checkbox follows, because it shares the variable."""
        self.genre_vars[genre].set(False)
        self.on_genre_change()

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
     
    def _details_worker(self, movie: dict) -> None:
        details = fetch_movie_details(movie["name"], movie["year"])   # slow part, runs in the background
        # tkinter is not thread safe: hand the result to the main thread
        self.after(0, self._on_details_result, movie, details)
    
    def _on_details_result(self, movie: dict, details: dict | None) -> None:
        """Runs on the main thread when the proxy answered (or failed)."""
        key = movie_key(movie)
        self.details_inflight.discard(key)
        if details:
            if is_not_found(details):
                # remember the day, so it is asked again after some time
                details = {**details, "checked": date.today().isoformat()}
            self.details_cache[key] = details   # only successes are saved
        else:
            self.details_failed.add(key)   # so the list does not ask again and again
        self.schedule_details_refresh()
        if self.summary_movies:
            self.render_summary()   # several movies selected: refresh the list
            return
        if movie is not self.details_target:
            return   # the user already picked something else
        if details:
            self.show_details(movie, details)
            self.request_poster(movie, details)   # now we know the poster path
        else:
            self.show_message(movie, "Could not load details.")
    
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
        self.complete_only.set(False)
        self.runtime_min.set("")
        self.runtime_max.set("")
        self.rating_min.set("")
        self.rating_max.set("")
        for var in self.genre_vars.values():
            var.set(False)
        self.draw_genre_chips()
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
        if self.complete_only.get():
            filtered = filter_complete(filtered)
        # Invalid fields show a message and are ignored, like the year fields
        runtime_error = number_range_error(self.runtime_min.get(), self.runtime_max.get(), "Runtime")
        rating_error = number_range_error(self.rating_min.get(), self.rating_max.get(), "Rating")
        self.details_error.config(text=runtime_error or rating_error or "")
        runtime_low, runtime_high = (None, None) if runtime_error else (
            parse_number(self.runtime_min.get()), parse_number(self.runtime_max.get()))
        rating_low, rating_high = (None, None) if rating_error else (
            parse_number(self.rating_min.get()), parse_number(self.rating_max.get()))
        filtered = filter_by_details(filtered, self.details_cache, self.selected_genres(),
                                    runtime_low, runtime_high,
                                    self.ratings, rating_low, rating_high)
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

        self.update_counter()
        self.update_headers()
        self.update_warning()
        self.update_buttons()
        self.on_select()
    
    def filters_use_details(self) -> bool:
        """True if a genre, runtime or rating filter is on."""
        fields = (self.runtime_min, self.runtime_max, self.rating_min, self.rating_max)
        return bool(self.selected_genres()) or any(v.get().strip() for v in fields)

    def schedule_details_refresh(self) -> None:
        """Redraws the list soon, if a filter depends on data that just arrived."""
        # a job already waiting will pick up this change too
        if self._refresh_job is not None or not self.filters_use_details():
            return
        self._refresh_job = self.after(DETAILS_REFRESH_MS, self.refresh_filtered_list)

    def refresh_filtered_list(self) -> None:
        """Rebuilds the list and keeps the selected movies selected."""
        self._refresh_job = None
        selected = [self.visible[int(i)] for i in self.table.selection()]
        self.update_list()   # this clears the selection
        # the row ids changed, so find the same movies again (by identity)
        ids = {id(m) for m in selected}
        rows = [str(i) for i, m in enumerate(self.visible) if id(m) in ids]
        if rows:
            self.table.selection_set(*rows)
    
    def update_counter(self) -> None:
        """Shows 'visible / total' and, if any rows are selected, how many."""
        selected = len(self.table.selection())
        text = f"{len(self.visible)} / {len(self.movies)}"
        if selected:
            text += f" ({selected} selected)"
        self.counter.config(text=text)

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
    
    def clear_selection(self) -> None:
        """Deselects the rows and takes the focus out of the filter boxes."""
        self.table.selection_remove(self.table.selection())
        self.search_entry.selection_clear()   # drops the highlighted text, if any
        self.table.focus_set()                # leaves the search box and the filters
        
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
        
    def show_details(self, movie: dict, details: dict | None) -> None:
        """Fills the panel. 'details' is None if the lookup failed."""
        self.hide_skeleton()   # also puts the rating label back in place
        year = f" ({movie['year']})" if movie["year"] else ""
        self.details_title.config(text=movie["name"] + year)
        # the line functions cannot take None, so decide first
        self.details_meta.config(text=details_line(details) if details else "")
        rating = rating_line(details, self.ratings) if details else ""
        self.details_rating.config(text=rating)
        overview = details.get("overview") if details else None
        if is_not_found(details):
            overview = "No details found for this movie."
        elif not isinstance(overview, str) or not overview.strip():
            overview = "No synopsis available."
        self.details_overview.config(text=overview.strip())
        self.overview_full = overview.strip()
        # only when the film has an id, no rating yet, and ratings are on their way
        has_id = bool(details) and isinstance(details.get("imdb_id"), str)
        if has_id and not rating and self.ratings_pending():
            # the bar takes the place of the empty rating label
            self.details_rating.pack_forget()
            self.rating_bar.pack(anchor="w", pady=(self.px(6), self.px(2)),
                                before=self.details_overview)
            self.start_pulse()
        self.update_poster(movie)
        
    def show_message(self, movie: dict, text: str) -> None:
        """Shows the title and one line of text, like 'Loading...'."""
        self.hide_skeleton()
        year = f" ({movie['year']})" if movie["year"] else ""
        self.details_title.config(text=movie["name"] + year)
        self.details_meta.config(text="")   # clear the old genres and runtime
        self.details_rating.config(text="")   # and the old rating
        self.details_overview.config(text=text)
        self.overview_full = text
        
    def show_skeleton(self, movie: dict) -> None:
        """Shows the title and grey bars while the details load."""
        # show_message sets the title and empties the texts (it also hides old bars)
        self.show_message(movie, "")
        # before details_meta, so the bars sit right under the title
        self.skeleton.pack(anchor="w", before=self.details_meta)
        self.start_pulse()

    def hide_skeleton(self) -> None:
        """Hides the skeleton loading"""
        self.skeleton.pack_forget()
        self.rating_bar.pack_forget()
        self.poster_label.pack_forget()
        self.poster_bar.pack_forget()
        self.stop_pulse()
        # the rating label may have been taken off the screen by the bar
        if self.details_rating.winfo_manager() != "pack":
            self.details_rating.pack(anchor="w", pady=(self.px(4), 0),
                                    before=self.details_overview)
    
    def update_poster(self, movie: dict) -> None:
        """Shows the poster, a grey block while it loads, or nothing."""
        if movie is not self.details_target:
            return   # the user already picked something else
        key = movie_key(movie)
        self.poster_label.pack_forget()
        self.poster_bar.pack_forget()
        free = self.panel_free_height()
        poster_room = None   # None means the panel has no size yet, so no limit
        if free is None:
            self.fit_overview(None)
        else:
            self.details_overview.config(text=self.overview_full)
            self.update_idletasks()
            full = self.details_overview.winfo_reqheight()   # height of the whole synopsis
            poster_min = self.px(POSTER_MIN_HEIGHT)
            line = self.summary_font.metrics("linespace")
            if free - full >= poster_min:
                # everything fits: whole synopsis, and the poster takes what is left
                poster_room = min(free - full, self.px(POSTER_HEIGHT))
                text_room = full
            else:
                # tight: the text keeps about 3 lines, the poster gets the rest (but never less than the minimum)
                text_min = min(full, 3 * line)
                poster_room = max(poster_min, min(free - text_min, self.px(POSTER_HEIGHT)))
                text_room = free - poster_room
                if text_room < line:
                    # not even one line of text would be left: only text
                    poster_room, text_room = 0, free
            self.fit_overview(text_room)
        if poster_room == 0:
            self.stop_pulse_if_idle()
            return
        data = self.posters.get(key)
        if data:
            image = load_poster_image(data, self.px(POSTER_WIDTH), poster_room)
            if image is not None:
                self.poster_photo = ImageTk.PhotoImage(image)
                self.poster_label.config(image=self.poster_photo)
                self.poster_label.pack(anchor="w", pady=(self.px(10), 0),
                                    before=self.details_overview)
        elif key in self.posters_inflight:
            # same shape as a real poster (2:3), never taller than the room
            height = self.px(POSTER_HEIGHT)
            if poster_room is not None:
                height = min(height, poster_room)
            self.poster_bar.config(width=round(height / 1.5), height=height)
            self.poster_bar.pack(anchor="w", pady=(self.px(10), 0),
                                before=self.details_overview)
            self.start_pulse()
            return
        self.stop_pulse_if_idle()

    def stop_pulse_if_idle(self) -> None:
        """Stops the pulse, unless a skeleton is still on the screen."""
        shown = (self.skeleton, self.rating_bar, self.poster_bar)
        if not any(w.winfo_manager() == "pack" for w in shown):
            self.stop_pulse()
            
    def schedule_fit(self) -> None:
        """Waits a moment after the last resize, so dragging the window stays smooth."""
        if self._fit_job is not None:
            self.after_cancel(self._fit_job)
        self._fit_job = self.after(100, self.refit_panel)

    def refit_panel(self) -> None:
        """Redraws the synopsis and the poster for the new panel size."""
        self._fit_job = None
        if self.details_target is not None:
            self.update_poster(self.details_target)

    def panel_free_height(self) -> int | None:
        """Height left after the title, genres and rating. None if the panel has no size yet."""
        self.update_idletasks()
        total = self.single_view.winfo_height()
        if total <= 1:
            return None
        used = sum(w.winfo_reqheight() for w in self.single_view.pack_slaves()
                if w not in (self.details_overview, self.poster_label, self.poster_bar))
        return total - used - self.px(PANEL_MARGIN)

    def fit_overview(self, room: int | None) -> None:
        """Cuts the synopsis with '...' until it fits in 'room' (None = no limit)."""
        text = self.overview_full
        self.details_overview.config(text=text)   # start from the whole text
        if room is None or not text:
            return
        self.update_idletasks()
        words = text.split()
        while words and self.details_overview.winfo_reqheight() > room:
            # drop about a tenth of the words each round
            words = words[:len(words) - max(1, len(words) // 10)]
            self.details_overview.config(text=" ".join(words).rstrip(",.;:") + "...")
            self.update_idletasks()
    
    def start_pulse(self) -> None:
        """Starts the pulsing, unless it is already running."""
        if self._pulse_job is None:
            self._pulse_job = self.after(PULSE_MS, self._pulse_tick)

    def stop_pulse(self) -> None:
        """Stops the timer and puts the bars back to their normal color."""
        if self._pulse_job is not None:
            self.after_cancel(self._pulse_job)
            self._pulse_job = None
        self._pulse_phase = 0.0
        self._paint_skeleton(ROW_ALT)

    def _pulse_tick(self) -> None:
        # move a small step forward in the cycle, and wrap around at 1
        self._pulse_phase = (self._pulse_phase + PULSE_MS / PULSE_CYCLE_MS) % 1.0
        # cosine gives 0 -> 1 -> 0, slow at both ends, so it looks like breathing
        t = (1 - math.cos(self._pulse_phase * 2 * math.pi)) / 2
        self._paint_skeleton(mix_colors(ROW_ALT, SCROLL, t))
        self._pulse_job = self.after(PULSE_MS, self._pulse_tick)

    def _paint_skeleton(self, color: str) -> None:
        """Colors the bars of the panel and the blocks of the list."""
        for bar in (*self.skeleton_bars, self.rating_bar, self.poster_bar):
            bar.config(bg=color)
        self.summary.tag_configure("skeleton", foreground=color)

    def ratings_pending(self) -> bool:
        """True while the ratings are downloading, loading or waiting to be read."""
        return (self.ratings_downloading or self.ratings_loading
                or self.ratings_file is not None)
    
    def on_select(self) -> None:
        """Runs whenever the selection changes."""
        self.update_counter()
        self.details_failed.clear()   # a new selection may retry what failed
        self.posters_failed.clear()
        sel = self.table.selection()
        if not sel:
            self.details_target = None   # a late answer must not refill the panel
            self.summary_movies = []
            self.clear_details()
            self.hide_panel()
            return
        self.show_panel()
        if len(sel) > 1:
            self.details_target = None
            self.clear_details()   # the one-movie view is hidden, keep it empty
            # sorted, so the list follows the order of the table
            self.summary_movies = [self.visible[i]
                                   for i in sorted(int(i) for i in sel)]
            self.show_summary_view()
            self.update_idletasks()   # the real size is only known after the layout
            self.render_summary()
            return
        self.summary_movies = []
        self.show_single_view()
        movie = self.visible[int(sel[0])]
        self.details_target = movie
        cached = self.details_cache.get(movie_key(movie))
        if cached:
            self.show_details(movie, cached)   # no network needed
            self.request_poster(movie, cached)
            return
        self.show_skeleton(movie)
        threading.Thread(target=self._details_worker, args=(movie,),
                         daemon=True).start()

    def clear_details(self) -> None:
        """Empties the panel (nothing selected)."""
        self.hide_skeleton()
        self.overview_full = ""
        for label in (self.details_title, self.details_meta,
                      self.details_rating, self.details_overview):
            label.config(text="")
            
    def show_panel(self) -> None:
        """Shows the details panel on the right. The table gets narrower."""
        if self.details.winfo_manager() != "pack":
            # before the scrollbar, so the order stays: panel, scrollbar, table
            self.details.pack(side="right", fill="y", before=self.scrollbar)

    def hide_panel(self) -> None:
        """Hides the details panel. The table covers the whole width again."""
        self.details.pack_forget()
        
    def show_single_view(self) -> None:
        """The panel shows one movie (title, genres, synopsis)."""
        self.summary.pack_forget()
        self.single_view.pack(fill="both", expand=True)

    def show_summary_view(self) -> None:
        """The panel shows the list of selected movies."""
        self.single_view.pack_forget()
        self.summary.pack(fill="both", expand=True)

    def summary_capacity(self) -> int:
        """How many lines of text fit in the panel."""
        return max(1, self.summary.winfo_height() // self.summary_font.metrics("linespace"))

    def summary_width(self) -> int:
        """How many characters fit in one line of the panel."""
        return max(10, self.summary.winfo_width() // self.summary_font.measure("0") - 1)

    def request_missing_details(self, movies: list[dict]) -> None:
        """Starts a request for each movie that has no details yet."""
        for movie in movies:
            key = movie_key(movie)
            if (key in self.details_cache or key in self.details_inflight
                    or key in self.details_failed):
                continue   # known, already on its way, or failed a moment ago
            self.details_inflight.add(key)
            threading.Thread(target=self._details_worker, args=(movie,),
                             daemon=True).start()
            
    def request_poster(self, movie: dict, details: dict) -> None:
        """Starts a background request for the poster, if it is needed."""
        key = movie_key(movie)
        # known, already on its way, or failed a moment ago
        if key in self.posters or key in self.posters_inflight or key in self.posters_failed:
            return
        poster_path = details.get("poster_path")
        if not isinstance(poster_path, str):
            return   # the film has no poster
        self.posters_inflight.add(key)
        threading.Thread(target=self._poster_worker, args=(movie, poster_path),
                        daemon=True).start()
        self.update_poster(movie)   # shows the grey block right away
            
    def _poster_worker(self, movie: dict, poster_path: str) -> None:
        """Downloads one poster. Runs in a background thread."""
        data = fetch_poster(poster_path)   # slow part, runs in the background
        try:
            self.after(0, self._on_poster_result, movie, data)
        except (RuntimeError, tk.TclError):
            pass
        
    def _on_poster_result(self, movie: dict, data: bytes | None) -> None:
        """Runs on the main thread when a poster arrived (or failed)."""
        key = movie_key(movie)
        self.posters_inflight.discard(key)
        if data:
            self.posters[key] = data
        else:
            self.posters_failed.add(key)
        self.update_poster(movie)
    
    def prefetch_details(self) -> None:
        """Fetches the details of every movie in the background."""
        todo = movies_to_fetch(self.movies, self.details_cache, self.details_inflight)
        if not todo:
            self.update_ratings()   # all details are already known
            return
        self.start_ratings_download()   # downloads while the details come in
        for m in todo:
            self.details_inflight.add(movie_key(m))
            self.executor.submit(self._prefetch_worker, m)
        self.prefetch_total = len(todo)
        self.prefetch_done = 0
        
    def _prefetch_worker(self, movie: dict) -> None:
        """Fetches one movie's details. Runs in a background thread."""
        details = fetch_movie_details(movie["name"], movie["year"])
        try:
            self.after(0, self._on_prefetch_result, movie, details)
        except (RuntimeError, tk.TclError):
            pass
        
    def _on_prefetch_result(self, movie: dict, details: dict | None) -> None:
        """Runs on the main thread when one prefetch request answered or failed."""
        self._on_details_result(movie, details)
        self.prefetch_done += 1
        if self.prefetch_done >= self.prefetch_total:
            self.show_status("Details ready")
            self.update_ratings()
        else:
            self.show_status(f"Details {self.prefetch_done}/{self.prefetch_total}")
            
    def start_ratings_download(self, force: bool = False) -> None:
        """Starts downloading the ratings file, if it is needed and not started yet."""
        if self.ratings_downloading or self.ratings_file is not None:
            return   # already on its way, or already here
        if not force and not ratings_need_update(self.ratings_updated, date.today()):
            return   # the saved ratings are still fresh
        self.ratings_downloading = True   # set it before the thread starts
        threading.Thread(target=self._download_ratings_worker, daemon=True).start()

    def _download_ratings_worker(self) -> None:
        """Downloads the ratings file. Runs in a background thread."""
        path = download_ratings()   # slow part, runs in the background
        try:
            self.after(0, self._on_ratings_downloaded, path)
        except (RuntimeError, tk.TclError):
            # the window is gone, so nobody will read the file
            if path is not None:
                path.unlink(missing_ok=True)

    def _on_ratings_downloaded(self, path: Path | None) -> None:
        """Runs on the main thread when the download finished or failed."""
        self.ratings_downloading = False
        if path is None:
            self.show_status("Could not download ratings")
            self.refresh_after_ratings()
            return
        self.ratings_file = path
        # only read it when every film has its details (and so its imdb id)
        if self.prefetch_done >= self.prefetch_total:
            self.update_ratings()

    def discard_ratings_file(self) -> None:
        """Deletes a downloaded ratings file that will not be used."""
        if self.ratings_file is None:
            return
        try:
            self.ratings_file.unlink(missing_ok=True)
        except OSError:
            pass   # still in use (Windows): it is only a temp file
        self.ratings_file = None

    def update_ratings(self) -> None:
        """Reads the ratings of the films in the list, only if it is needed."""
        if self.ratings_loading:
            return   # a read is already running
        wanted = wanted_imdb_ids(self.movies, self.details_cache)
        if not wanted or not ratings_need_refresh(self.ratings_updated, date.today(),
                                                  wanted, self.ratings):
            self.discard_ratings_file()   # nothing to look up, so the file is not needed
            return
        self.start_ratings_download(force=True)   # does nothing if it already started
        if self.ratings_file is None:
            self.show_status("Downloading ratings...", keep=True)
            return   # _on_ratings_downloaded calls this method again
        path, self.ratings_file = self.ratings_file, None
        self.ratings_loading = True   # set it before the thread starts
        threading.Thread(target=self._read_ratings_worker, args=(wanted, path),
                         daemon=True).start()

    def _read_ratings_worker(self, wanted: set[str], path: Path) -> None:
        """Reads the downloaded ratings file. Runs in a background thread."""
        ratings = read_downloaded_ratings(path, wanted)
        if ratings is not None:
            # films with no IMDb rating get (0.0, 0), so they count as looked up
            ratings = with_unrated_marks(ratings, wanted)
        try:
            self.after(0, self._on_ratings_result, ratings)
        except (RuntimeError, tk.TclError):
            pass

    def _on_ratings_result(self, ratings: dict | None) -> None:
        """Runs on the main thread when the read finished or failed."""
        self.ratings_loading = False
        if ratings is None:
            self.show_status("Could not load ratings")
            # the rating bars must go away, because nothing else is coming
            self.refresh_after_ratings()
            return
        self.ratings = ratings
        self.ratings_updated = date.today()
        write_ratings_cache(self.ratings, self.ratings_updated)
        self.show_status("Ratings ready")
        self.refresh_after_ratings()
            
    def refresh_after_ratings(self) -> None:
        """Redraws the panel or the list, so the rating bars match the new state."""
        self.schedule_details_refresh()
        if self.summary_movies:
            self.render_summary()   # several movies selected: refresh the list
            return
        # one movie selected: refresh the panel, so the rating shows up
        movie = self.details_target
        if movie is None:
            return   # nothing selected
        details = self.details_cache.get(movie_key(movie))
        if details:
            self.show_details(movie, details)

    def render_summary(self) -> None:
        """Draws the list of selected movies, as detailed as the space allows."""
        if not self.summary_movies:
            return
        with_details, shown = plan_summary(len(self.summary_movies),
                                           self.summary_capacity())
        if with_details:
            # only the movies that are shown need their details
            self.request_missing_details(self.summary_movies[:shown])
        lines = build_summary(self.summary_movies, shown, with_details,
                    self.summary_width(), self.details_cache,
                    self.details_inflight, self.ratings,
                    self.ratings_pending())
        # pulse only while at least one skeleton line is on the screen
        if any(style == "skeleton" for _, style in lines):
            self.start_pulse()
        else:
            self.stop_pulse()
        self.summary.config(state="normal")   # a disabled Text cannot be changed
        self.summary.delete("1.0", "end")
        for text, style in lines:
            self.summary.insert("end", text + "\n", (style,) if style else ())
        self.summary.config(state="disabled")
        
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
        try:
            data = read_config()
            data["sort_column"] = self.sort_column
            data["reverse_order"] = self.reverse_order
            data["window_size"] = (f"{self.winfo_width()}x{self.winfo_height()}"
                                   f"+{self.winfo_x()}+{self.winfo_y()}")
            write_config(data)
            write_details_cache(self.details_cache)
        finally:
            # whatever fails above, the window must close
            self.discard_ratings_file()
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.stop_pulse()
            if self._refresh_job is not None:
                self.after_cancel(self._refresh_job)
            if self._fit_job is not None:
                self.after_cancel(self._fit_job)
            self.destroy()

if __name__ == "__main__":
    enable_dpi_awareness()   # must run before the window is created
    App().mainloop()