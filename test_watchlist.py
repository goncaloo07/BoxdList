import pytest
import tkinter as tk
from types import SimpleNamespace
import boxdlist
from boxdlist import (filter_movies, is_incomplete, load_movies, normalize,
                      pick_random, read_last_path, save_last_path,
                      sort_movies, year_as_int, year_range_error, valid_window_size, valid_sort_column,
                      valid_reverse_order, save_movies, is_newer, find_installer_url, fetch_latest_release, download_file, remove_movies,
                      count_new_movies, count_movies_by_decade, fit_on_screen, filter_recent, insert_movie, filter_complete,
                      initial_sort, count_duplicates, format_runtime, details_line)
import build
from datetime import date, timedelta
import urllib.error
import io


def movie(name, year="2000", date="2024-01-01", url="https://boxd.it/x"):
    # Small helper to build a movie dict with sensible defaults
    return {"name": name, "year": year, "date": date, "url": url}

@pytest.fixture
def movies():
    return [
        movie("Amélie", "2001", "2024-03-01"),
        movie("Blade Runner", "1982", "2024-01-15"),
        movie("Cidade de Deus", "2002", "2024-02-10"),
        movie("No Year", "", "2024-04-01"),
    ]

def write_csv(path, lines, header="Date,Name,Year,Letterboxd URI"):
    # Writes a small CSV file and returns its path as a string
    path.write_text(header + "\n" + "\n".join(lines) + "\n",
                    encoding="utf-8")
    return str(path)

def names(items):
    return [m["name"] for m in items]

def table_names(app):
    # Each row has an id; the first value of a row is the movie name
    return [app.table.item(row, "values")[0]
            for row in app.table.get_children()]
    
def press(app, sequence):
    # Key events only reach a visible window that has focus
    app.deiconify()
    app.update()
    app.focus_force()
    app.update()
    app.event_generate(sequence)
    app.update()

# ---------- normalize ----------
def test_normalize_removes_accents_and_case():
    assert normalize("Amélie") == "amelie"
    assert normalize("AÇÃO") == "acao"

def test_normalize_empty_text():
    assert normalize("") == ""

# ---------- year_as_int ----------
@pytest.mark.parametrize("value, expected", [
    ("1999", 1999), (" 2001 ", 2001), ("", None), ("abc", None), ("19a9", None),
])
def test_year_as_int(value, expected):
    assert year_as_int(value) == expected


# ---------- year_range_error ----------
@pytest.mark.parametrize("low, high", [
    ("", ""),             # no limits at all
    ("1990", ""),         # only a minimum
    ("", "2000"),         # only a maximum
    ("1990", "2000"),     # normal range
    ("2000", "2000"),     # same year is valid
    (" 1990 ", " 2000 "), # outer spaces are ignored
])
def test_year_range_error_valid(low, high):
    assert year_range_error(low, high) is None


@pytest.mark.parametrize("low, high", [
    ("abc", ""),          # not a number
    ("199", ""),          # too short
    ("", "20000"),        # too long
    ("1990", "20a0"),     # mixed letters and digits
])
def test_year_range_error_needs_four_digits(low, high):
    assert year_range_error(low, high) == "Years must have 4 digits"

def test_year_range_error_inverted_range():
    assert year_range_error("2000", "1990") == "'From' is after 'to'"

# ---------- load_movies ----------
def test_load_movies_reads_columns(tmp_path):
    path = write_csv(tmp_path / "w.csv", [
        "2024-01-01,Amélie,2001,https://boxd.it/a",
        "2024-01-02,Blade Runner,1982,https://boxd.it/b",
    ])
    result, ignored = load_movies(path)
    assert ignored == 0
    assert result == [
        {"name": "Amélie", "year": "2001", "date": "2024-01-01",
         "url": "https://boxd.it/a"},
        {"name": "Blade Runner", "year": "1982", "date": "2024-01-02",
         "url": "https://boxd.it/b"},
    ]

def test_load_movies_accepts_bom(tmp_path):
    path = tmp_path / "w.csv"
    path.write_text(
        "Date,Name,Year,Letterboxd URI\n2024-01-01,X,2000,u\n",
        encoding="utf-8-sig")
    assert load_movies(str(path))[0][0]["name"] == "X"

def test_load_movies_short_row_does_not_crash(tmp_path):
    path = write_csv(tmp_path / "w.csv", ["2024-01-01,Only Name"])
    result, ignored = load_movies(path)
    assert ignored == 0
    assert result[0]["name"] == "Only Name"
    assert result[0]["year"] == ""
    assert result[0]["url"] == ""

def test_load_movies_missing_columns(tmp_path):
    path = write_csv(tmp_path / "w.csv", ["a,b"], header="Foo,Bar")
    with pytest.raises(ValueError):
        load_movies(path)

def test_load_movies_empty_file(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        load_movies(str(path))

def test_load_movies_missing_file(tmp_path):
    with pytest.raises(OSError):
        load_movies(str(tmp_path / "does_not_exist.csv"))

def test_load_movies_ignores_row_without_name(tmp_path):
    path = write_csv(tmp_path / "w.csv", [
        "2024-01-01,,2001,https://boxd.it/a",
        "2024-01-02,Blade Runner,1982,https://boxd.it/b",
    ])
    result, ignored = load_movies(path)
    assert names(result) == ["Blade Runner"]
    assert ignored == 1


def test_load_movies_blank_row_is_not_an_error(tmp_path):
    path = write_csv(tmp_path / "w.csv",
                     [",,,", "2024-01-02,X,1982,https://boxd.it/b"])
    result, ignored = load_movies(path)
    assert len(result) == 1
    assert ignored == 0


def test_load_movies_ignores_row_with_extra_columns(tmp_path):
    # The unquoted comma in the name creates an extra column
    path = write_csv(tmp_path / "w.csv", [
        "2024-01-01,Movie, with comma,2001,https://boxd.it/a",
        "2024-01-02,Good,1982,https://boxd.it/b",
    ])
    result, ignored = load_movies(path)
    assert names(result) == ["Good"]
    assert ignored == 1


def test_load_movies_invalid_fields_become_empty(tmp_path):
    path = write_csv(tmp_path / "w.csv", [
        "yesterday,Movie,abcd,weird-link",
    ])
    result, _ = load_movies(path)
    assert result == [{"name": "Movie", "year": "", "date": "", "url": ""}]

def test_is_incomplete():
    assert not is_incomplete(movie("A"))
    assert is_incomplete(movie("A", year=""))
    assert is_incomplete(movie("A", date=""))
    assert is_incomplete(movie("A", url=""))

# ---------- filter_movies ----------
def test_filter_without_criteria_returns_everything(movies):
    assert filter_movies(movies, "", None, None) == movies


def test_filter_by_name_ignores_accents_and_case(movies):
    result = filter_movies(movies, "AMELIE", None, None)
    assert names(result) == ["Amélie"]

def test_filter_by_name_ignores_outer_spaces(movies):
    assert len(filter_movies(movies, "  blade  ", None, None)) == 1


def test_filter_by_year_range(movies):
    result = filter_movies(movies, "", 2000, 2002)
    assert names(result) == ["Amélie", "Cidade de Deus"]

def test_filter_min_year_only(movies):
    result = filter_movies(movies, "", 2002, None)
    assert names(result) == ["Cidade de Deus"]

def test_filter_max_year_only(movies):
    result = filter_movies(movies, "", None, 1990)
    assert names(result) == ["Blade Runner"]

def test_filter_with_range_excludes_movies_without_year(movies):
    result = filter_movies(movies, "", 1900, 2100)
    assert "No Year" not in names(result)

def test_filter_without_range_keeps_movies_without_year(movies):
    assert "No Year" in names(filter_movies(movies, "", None, None))

def test_filter_combines_name_and_year(movies):
    assert filter_movies(movies, "c", 2002, 2002) == [movies[2]]

def test_filter_no_results(movies):
    assert filter_movies(movies, "zzz", None, None) == []

# ---------- sort_movies ----------
def test_sort_without_column_returns_copy_in_same_order(movies):
    result = sort_movies(movies, None, False)
    assert result == movies
    assert result is not movies

def test_sort_does_not_change_original_list(movies):
    original = list(movies)
    sort_movies(movies, "year", True)
    assert movies == original

def test_sort_by_name(movies):
    assert names(sort_movies(movies, "name", False)) == [
        "Amélie", "Blade Runner", "Cidade de Deus", "No Year"]

def test_sort_by_name_reversed(movies):
    assert names(sort_movies(movies, "name", True))[0] == "No Year"

def test_sort_by_year_is_numeric_with_empty_first(movies):
    assert names(sort_movies(movies, "year", False)) == [
        "No Year", "Blade Runner", "Amélie", "Cidade de Deus"]

def test_sort_year_is_not_alphabetical():
    # As text "1000" < "999", but as numbers 999 < 1000
    items = [movie("A", "999"), movie("B", "1000")]
    assert names(sort_movies(items, "year", False)) == ["A", "B"]

def test_sort_by_date(movies):
    assert names(sort_movies(movies, "date", False)) == [
        "Blade Runner", "Cidade de Deus", "Amélie", "No Year"]

# ---------- pick_random ----------
def test_random_never_repeats_the_last_one(movies):
    last = movies[0]
    for _ in range(200):
        assert pick_random(movies, last) is not last

def test_random_with_a_single_movie_returns_it(movies):
    only = [movies[0]]
    assert pick_random(only, movies[0]) is movies[0]

def test_random_without_last(movies):
    assert pick_random(movies, None) in movies

def test_random_uses_random_choice(movies, monkeypatch):
    # Replace random.choice so the result is predictable
    monkeypatch.setattr(boxdlist.random, "choice", lambda seq: seq[-1])
    assert pick_random(movies, None) is movies[-1]

# ---------- config ----------
def test_config_save_and_read(tmp_path, monkeypatch):
    monkeypatch.setattr(boxdlist, "CONFIG_FILE", tmp_path / "cfg.json")
    save_last_path("/tmp/w.csv")
    assert read_last_path() == "/tmp/w.csv"

def test_config_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(boxdlist, "CONFIG_FILE", tmp_path / "missing.json")
    assert read_last_path() is None

def test_config_corrupted_returns_none(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    cfg.write_text("{this is not json", encoding="utf-8")
    monkeypatch.setattr(boxdlist, "CONFIG_FILE", cfg)
    assert read_last_path() is None

def test_config_save_in_missing_folder_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(boxdlist, "CONFIG_FILE",
                        tmp_path / "missing_folder" / "cfg.json")
    save_last_path("/tmp/w.csv")  # should not raise an error
    assert (tmp_path / "missing_folder" / "cfg.json").exists()
    
def test_config_reads_old_key(tmp_path, monkeypatch):
    # Config files created before the rename still work
    cfg = tmp_path / "cfg.json"
    cfg.write_text('{"ultimo_csv": "/tmp/old.csv"}', encoding="utf-8")
    monkeypatch.setattr(boxdlist, "CONFIG_FILE", cfg)
    assert read_last_path() == "/tmp/old.csv"

def test_config_new_key_wins_over_old(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    cfg.write_text('{"last_csv": "/tmp/new.csv", "ultimo_csv": "/tmp/old.csv"}',
                   encoding="utf-8")
    monkeypatch.setattr(boxdlist, "CONFIG_FILE", cfg)
    assert read_last_path() == "/tmp/new.csv"
    
    # ---------- details cache on disk ----------
def cache_file(tmp_path, monkeypatch, name="cache.json"):
    # points the cache to a temp file, so the real one is never touched
    path = tmp_path / name
    monkeypatch.setattr(boxdlist, "DETAILS_CACHE_FILE", path)
    return path

@pytest.mark.parametrize("key, text", [
    (("Amélie", "2001"), "Amélie|2001"),
    (("No Year", ""), "No Year|"),           # movie without a year
    (("A|B", "2000"), "A|B|2000"),           # "|" inside the name
])
def test_key_text_round_trip(key, text):
    assert boxdlist.key_to_text(key) == text
    assert boxdlist.text_to_key(text) == key

def test_details_cache_save_and_read(tmp_path, monkeypatch):
    cache_file(tmp_path, monkeypatch)
    original = {("Amélie", "2001"): {"genres": ["Comedy"], "runtime": 122},
                ("No Year", ""): {"overview": "A story."}}
    boxdlist.write_details_cache(original)
    assert boxdlist.read_details_cache() == original

def test_details_cache_keeps_accents_readable(tmp_path, monkeypatch):
    path = cache_file(tmp_path, monkeypatch)
    boxdlist.write_details_cache({("Amélie", "2001"): {}})
    # ensure_ascii=False, so the file has "Amélie" and not "Am\u00e9lie"
    assert "Amélie" in path.read_text(encoding="utf-8")

def test_details_cache_missing_returns_empty(tmp_path, monkeypatch):
    cache_file(tmp_path, monkeypatch, "missing.json")
    assert boxdlist.read_details_cache() == {}

def test_details_cache_corrupted_returns_empty(tmp_path, monkeypatch):
    path = cache_file(tmp_path, monkeypatch)
    path.write_text("{this is not json", encoding="utf-8")
    assert boxdlist.read_details_cache() == {}

def test_details_cache_not_a_dict_returns_empty(tmp_path, monkeypatch):
    path = cache_file(tmp_path, monkeypatch)
    path.write_text('["not", "a", "dict"]', encoding="utf-8")
    assert boxdlist.read_details_cache() == {}

def test_details_cache_ignores_invalid_entries(tmp_path, monkeypatch):
    path = cache_file(tmp_path, monkeypatch)
    # first has no "|", second has details that are not a dict
    path.write_text('{"no separator": {}, "A|2000": "text"}', encoding="utf-8")
    assert boxdlist.read_details_cache() == {}

def test_details_cache_keeps_valid_entries_next_to_invalid_ones(tmp_path, monkeypatch):
    path = cache_file(tmp_path, monkeypatch)
    path.write_text('{"bad": {}, "A|2000": {"runtime": 90}}', encoding="utf-8")
    assert boxdlist.read_details_cache() == {("A", "2000"): {"runtime": 90}}

def test_details_cache_save_in_missing_folder_creates_it(tmp_path, monkeypatch):
    path = tmp_path / "missing_folder" / "cache.json"
    monkeypatch.setattr(boxdlist, "DETAILS_CACHE_FILE", path)
    boxdlist.write_details_cache({("A", "2000"): {}})   # should not raise an error
    assert path.exists()

def test_details_cache_save_error_does_not_crash(tmp_path, monkeypatch):
    # the "file" is really a folder, so writing to it fails with an OSError
    folder = tmp_path / "cache.json"
    folder.mkdir()
    monkeypatch.setattr(boxdlist, "DETAILS_CACHE_FILE", folder)
    boxdlist.write_details_cache({("A", "2000"): {}})   # must be silent
    
# UI testing

@pytest.fixture(scope="session")
def _window():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(boxdlist, "read_last_path", lambda: None)
        mp.setattr(boxdlist, "read_config", lambda: {})
        mp.setattr(boxdlist, "read_details_cache", lambda: {})
        try:
            window = boxdlist.App()
        except tk.TclError as error:
            pytest.skip(f"Could not create window: {error}")
    window.withdraw()
    yield window
    window.destroy()
    
class FakeExecutor:
    """Pretends to be ThreadPoolExecutor. It only records what it was asked."""
    def __init__(self):
        self.submitted = []        # (function, args) of every submit
        self.shutdown_calls = []   # arguments of every shutdown
    def submit(self, func, *args):
        self.submitted.append((func, args))
    def shutdown(self, **kwargs):
        self.shutdown_calls.append(kwargs)
    
@pytest.fixture
def app(_window, monkeypatch):
    # UI tests must never call the real proxy
    monkeypatch.setattr(boxdlist, "fetch_movie_details", lambda name, year: None)
    monkeypatch.setattr(boxdlist, "fetch_movie_details", lambda name, year: None)
    monkeypatch.setattr(boxdlist, "write_details_cache", lambda cache: None)
    monkeypatch.setattr(_window, "executor", FakeExecutor())
    _window.prefetch_total = 0
    _window.prefetch_done = 0
    # Back to a clean state before each test
    _window.withdraw()
    _window.set_movies([])
    _window.sort_column = None
    _window.reverse_order = False
    _window.details_cache.clear()
    _window.details_target = None
    _window.details_inflight.clear()
    _window.details_failed.clear()
    _window.summary_movies = []
    return _window
    
def test_warning_when_no_csv(app):
    # A fresh app has no movies, so it asks for an import
    assert app.warning.cget("text") == "Import a CSV to begin (Ctrl+O)"
    
def test_counter_shows_total(app, movies):
    app.set_movies(movies)
    # 4 movies in the fixture, all visible at the start
    assert app.counter.cget("text") == "4 / 4"
    
def test_counter_with_search(app, movies):
    app.set_movies(movies)
    app.search.set("blade") # searches to make only one movie appear
    app.update_list()
    assert app.counter.cget("text") == "1 / 4"
    
def test_warning_when_search_has_no_results(app, movies):
    app.set_movies(movies)
    app.search.set("zxjcijsioefho")
    app.update_list()
    assert app.warning.cget("text") == "No movies match the filters"
    
def test_warning_hidden_when_movies_are_visible(app, movies):
    app.set_movies(movies)
    assert app.warning.place_info() == {}
    
def test_table_shows_all_movies(app, movies):
    app.set_movies(movies)
    assert len(table_names(app)) == 4

def test_sort_table_by_name(app, movies):
    app.set_movies(list(reversed(movies)))
    app.sort("name")
    assert table_names(app) == ["Amélie", "Blade Runner", "Cidade de Deus", "No Year"]
    
def test_sort_table_by_double_clicking(app, movies):
    app.set_movies(movies)
    app.sort("name")
    app.sort("name")
    assert table_names(app) == ["No Year", "Cidade de Deus", "Blade Runner", "Amélie"]
    
def test_sort_other_column_resets_direction(app, movies):
    app.set_movies(movies)
    app.sort("name")
    app.sort("name")
    assert app.reverse_order is True
    app.sort("year")
    assert app.reverse_order is False
    assert table_names(app) == ["No Year", "Blade Runner", "Amélie", "Cidade de Deus"]
    
def test_decade_dropdown_lists_decades(app, movies):
    app.set_movies(movies)
    assert list(app.decade.cget("values")) == ["All", "1980s (1)", "2000s (2)"]
    
def test_decade_filters_table(app, movies):
    app.set_movies(movies)
    app.decade.set("2000s")
    app.select_decade()
    app.update_list()
    assert app.year_min.get() == "2000"
    assert app.year_max.get() == "2009"
    assert table_names(app) == ["Amélie", "Cidade de Deus"]
    
def test_random_movie_shows_result(app, movies):
    app.set_movies([movies[0]])
    app.random_movie()
    assert app.result.cget("text") == "Amélie (2001)"
    assert app.table.selection() == ("0",)
    
def test_random_with_no_movies_shows_message(app, monkeypatch):
    shown = []
    # Replace the real popup with a function that only records the call
    monkeypatch.setattr(boxdlist.messagebox, "showinfo",
                        lambda *args, **kwargs: shown.append(args))
    app.random_movie()  # the app is empty, so it should show the popup
    # The popup was called exactly once
    assert len(shown) == 1
    # args[0] is the window title, args[1] is the message
    assert shown[0][1] == "No movies to choose from. Import a CSV first."
    
def test_clear_filters(app, movies):
    app.set_movies(movies)
    app.search.set("blade")
    app.decade.set("2000s")
    app.select_decade()
    app.clear_filters()
    assert app.search.get() == ""
    assert app.year_min.get() == ""
    assert app.year_max.get() == ""
    assert app.decade.get() == "All"
    assert len(table_names(app)) == 4
    
def test_ctrl_r_shortcuts_pick_random_movie(app, monkeypatch):
    called = []
    # Replace the real method with one that only records the call
    monkeypatch.setattr(app, "random_movie", lambda: called.append(1))
    press(app, "<Control-r>")
    assert len(called) == 1
    press(app, "<Control-R>")
    assert len(called) == 2
    
def test_ctrl_f_shortcuts_focus_search(app):
    app.search.set("blade")
    press(app, "<Control-f>")
    assert app.focus_get() is app.search_entry
    assert app.search_entry.selection_present()
    press(app, "<Tab>")
    assert app.focus_get() is not app.search_entry
    press(app, "<Control-F>")
    assert app.focus_get() is app.search_entry
    assert app.search_entry.selection_present()
    
def test_copy_name_puts_movie_in_clipboard(app, movies):
    app.set_movies([movies[0]])
    app.table.selection_set("0")
    app.copy_name()
    assert app.clipboard_get() == "Amélie"
    
@pytest.mark.parametrize("value, expected", [
    ("900x650", "900x650"),    # good value is kept
    ("banana", boxdlist.DEFAULT_SIZE),     # bad text falls back
    (12345, boxdlist.DEFAULT_SIZE),        # not even text
    (None, boxdlist.DEFAULT_SIZE),         # key missing in the file
    ("900x650+120+80", "900x650+120+80"),     # size and position are kept
    ("900x650+-1920+0", "900x650+-1920+0"),   # negative position (second monitor)
    ("900x650+120", boxdlist.DEFAULT_SIZE), # half a position falls back
])
def test_valid_window_size(value, expected):
    assert valid_window_size(value) == expected
    
SCREEN = (0, 0, 1920, 1080)
@pytest.mark.parametrize("size, screen, expected", [
    ("900x650", SCREEN, "900x650"),                          # no position: unchanged
    ("900x650+120+80", SCREEN, "900x650+120+80"),            # inside: kept
    ("900x650+1500+60", SCREEN, "900x650+1500+60"),          # half outside: still kept
    ("900x650+1950+60", SCREEN, "900x650"),                  # monitor is gone: dropped
    ("900x650+-32000+-32000", SCREEN, "900x650"),            # minimized on Windows: dropped
    ("900x650+-1920+0", (-1920, 0, 1920, 1080), "900x650+-1920+0"),  # monitor on the left: kept
    ("900x650+1850+60", SCREEN, "900x650"),   # only 70 px visible: dropped
    ("900x650+120+1050", SCREEN, "900x650"),  # only 30 px visible at the bottom: dropped
    ("900x650+120+-30", SCREEN, "900x650"),   # title bar above the screen: dropped
])
def test_fit_on_screen(size, screen, expected):
    assert fit_on_screen(size, screen) == expected
    
@pytest.mark.parametrize("value, expected", [
    ("name", "name"),    # good value is kept
    ("banana", None),     # bad text falls back
    (12345, None),        # not even text
    (None, None),         # key missing in the file
    (["name"], None),     # a list instead of text
])
def test_valid_sort_column(value,expected):
    assert valid_sort_column(value) == expected
    
@pytest.mark.parametrize("value, expected", [
    (True, True),    # good value is kept
    (False, False),     # bad text falls back
    ("yes", False),        # not even text
    (None, False),         # key missing in the file
])
def test_valid_reverse_order(value,expected):
    assert valid_reverse_order(value) == expected
    
def test_on_close_saves_settings(app, monkeypatch):
    saved = {}
    # Fake config that already has a saved CSV
    monkeypatch.setattr(boxdlist, "read_config", lambda: {"last_csv": "/tmp/w.csv"})
    # Capture what would be written, instead of touching the real file
    monkeypatch.setattr(boxdlist, "write_config", lambda data: saved.update(data))
    # Do not really close the shared test window
    monkeypatch.setattr(app, "destroy", lambda: None)
    app.sort_column = "year"
    app.reverse_order = True
    # show the window so it has a real size
    app.deiconify()
    app.geometry("900x650")
    app.update()
    app.on_close()
    assert saved["sort_column"] == "year"
    assert saved["reverse_order"] is True
    assert saved["last_csv"] == "/tmp/w.csv"   # must still be there
    assert boxdlist.VALID_SIZE.fullmatch(saved["window_size"])
    
def test_on_close_saves_details_cache(app, monkeypatch):
    saved = []
    monkeypatch.setattr(boxdlist, "read_config", lambda: {})
    monkeypatch.setattr(boxdlist, "write_config", lambda data: None)
    # capture what would be written to the cache file
    monkeypatch.setattr(boxdlist, "write_details_cache",
                        lambda cache: saved.append(dict(cache)))
    monkeypatch.setattr(app, "destroy", lambda: None)
    app.details_cache[("Amélie", "2001")] = {"runtime": 122}
    app.on_close()
    assert saved == [{("Amélie", "2001"): {"runtime": 122}}]
    
def test_save_movies_round_trip(tmp_path, movies):
    path = str(tmp_path / "out.csv")
    save_movies(path, movies)
    result, ignored = load_movies(path)
    assert ignored == 0
    assert result == movies
    
def test_save_movies_keeps_commas_in_names(tmp_path):
    path = str(tmp_path / "out.csv")
    original = [movie("Movie, with comma")]
    save_movies(path, original)
    result, ignored = load_movies(path)
    assert result == original
    assert ignored == 0
    
def test_export_csv_saves_only_visible_movies(app, movies, tmp_path, monkeypatch):
    out = tmp_path / "out.csv"
    # Fake "Save as" window: returns a path instead of asking
    monkeypatch.setattr(boxdlist.filedialog, "asksaveasfilename",
                        lambda **kwargs: str(out))
    app.set_movies(movies)
    app.search.set("blade")
    app.update_list()
    app.export_csv()
    result, _ = load_movies(str(out))
    assert names(result) == ["Blade Runner"]
    
def test_export_csv_cancel_does_nothing(app, monkeypatch, movies):
    app.set_movies(movies)
    # Fake "Save as" window: returns "" to simulate user cancelling
    monkeypatch.setattr(boxdlist.filedialog, "asksaveasfilename",
                    lambda **kwargs: "")
    called = []
    # Fake save: only records that it was called
    monkeypatch.setattr(boxdlist, "save_movies",
                    lambda path, movies: called.append(path))
    app.export_csv()
    assert called == []  # save_movies was not called
    
def test_export_csv_with_no_movies_shows_message(app, monkeypatch):
    shown = []
    monkeypatch.setattr(boxdlist.messagebox, "showinfo",
                    lambda *args, **kwargs: shown.append(args))
    app.export_csv()
    assert len(shown) == 1
    assert shown[0][1] == "No movies to export."
    
def test_buttons_hidden_when_no_visible_movies(app, movies):
    # empty app: nothing to pick or export
    assert app.random_button.winfo_manager() == ""
    assert app.export_button.winfo_manager() == ""
    assert app.open_button.winfo_manager() == ""
    app.set_movies(movies)
    assert app.random_button.winfo_manager() == "pack"
    assert app.export_button.winfo_manager() == "pack"
    assert app.open_button.winfo_manager() == "pack"
    # a search with no results hides them again
    app.search.set("zzz")
    app.update_list()
    assert app.random_button.winfo_manager() == ""
    assert app.export_button.winfo_manager() == ""
    assert app.open_button.winfo_manager() == ""
    
@pytest.mark.parametrize("latest, current, expected", [
    ("v1.0.7", "1.0.6", True),
    ("v1.0.10", "1.0.9", True),
    ("v1.0.6", "1.0.6", False),
    ("v1.0.5", "1.0.6", False),
    ("abc", "1.0.6", False)
])
def test_is_newer(latest, current, expected):
    assert is_newer(latest, current) == expected

def test_find_installer_url_finds_the_exe():
    release = {"assets": [
        {"name": "other.zip", "browser_download_url": "https://x/other.zip"},
        {"name": "BoxdListSetup.exe", "browser_download_url": "https://x/setup.exe"},
    ]}
    assert find_installer_url(release) == "https://x/setup.exe"
    
def test_find_installer_url_returns_none_if_not_found():
    release = {"assets": [
        {"name": "other.zip", "browser_download_url": "https://x/other.zip"},
    ]}
    assert find_installer_url(release) is None
    
def test_find_installer_url_returns_none_if_no_assets():
    release = {"assets": []}
    assert find_installer_url(release) is None
    
# ---------- network (no real internet is used) ----------
class FakeResponse:
    """Pretends to be the object that urlopen returns."""
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_urlopen(body: bytes):
    # Returns a replacement for urlopen that always answers with 'body'
    return lambda url, timeout=None: FakeResponse(body)

def test_fetch_latest_release_returns_the_data(monkeypatch):
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen",
                        fake_urlopen(b'{"tag_name": "v1.0.9"}'))
    assert fetch_latest_release() == {"tag_name": "v1.0.9"}

def test_fetch_latest_release_invalid_json_returns_none(monkeypatch):
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen",
                        fake_urlopen(b"this is not json"))
    assert fetch_latest_release() is None


def test_fetch_latest_release_ignores_json_that_is_not_a_dict(monkeypatch):
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen",
                        fake_urlopen(b'["not", "a", "dict"]'))
    assert fetch_latest_release() is None


def test_fetch_latest_release_network_error_returns_none(monkeypatch):
    def no_internet(url, timeout=None):
        raise OSError("no internet")
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen", no_internet)
    assert fetch_latest_release() is None


def test_download_file_success(monkeypatch, tmp_path):
    calls = []
    # Fake download: only records what it was asked to download
    monkeypatch.setattr(boxdlist.urllib.request, "urlretrieve",
                        lambda url, target: calls.append((url, target)))
    target = tmp_path / "setup.exe"
    assert download_file("https://x/setup.exe", target) is True
    assert calls == [("https://x/setup.exe", target)]

def test_download_file_failure_returns_false(monkeypatch, tmp_path):
    def fail(url, target):
        raise OSError("connection lost")
    monkeypatch.setattr(boxdlist.urllib.request, "urlretrieve", fail)
    assert download_file("https://x/setup.exe", tmp_path / "setup.exe") is False
    
def test_fetch_movie_details_returns_the_data(monkeypatch):
    body = b'{"genres": ["Comedy"], "runtime": 122, "overview": "A story."}'
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen", fake_urlopen(body))
    result = boxdlist.fetch_movie_details("Amelie", "2001")
    assert result == {"genres": ["Comedy"], "runtime": 122, "overview": "A story."}
    
def capture_urls(monkeypatch, body=b"{}"):
    # Fake urlopen that remembers every address it was asked for
    seen = []
    def fake(url, timeout=None):
        seen.append(url)
        return FakeResponse(body)
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen", fake)
    return seen


def test_fetch_movie_details_returns_the_data(monkeypatch):
    body = b'{"genres": ["Comedy"], "runtime": 122, "overview": "A story."}'
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen", fake_urlopen(body))
    result = boxdlist.fetch_movie_details("Amelie", "2001")
    assert result == {"genres": ["Comedy"], "runtime": 122, "overview": "A story."}


def test_fetch_movie_details_invalid_json_returns_none(monkeypatch):
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen",
                        fake_urlopen(b"this is not json"))
    assert boxdlist.fetch_movie_details("Amelie", "2001") is None


def test_fetch_movie_details_json_that_is_not_a_dict_returns_none(monkeypatch):
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen",
                        fake_urlopen(b'["not", "a", "dict"]'))
    assert boxdlist.fetch_movie_details("Amelie", "2001") is None


def test_fetch_movie_details_network_error_returns_none(monkeypatch):
    def no_internet(url, timeout=None):
        raise OSError("no internet")
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen", no_internet)
    assert boxdlist.fetch_movie_details("Amelie", "2001") is None


def test_fetch_movie_details_not_found_returns_none(monkeypatch):
    # the proxy answers 404 when TMDB does not know the movie;
    # urlopen raises HTTPError, which is a kind of OSError
    def not_found(url, timeout=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen", not_found)
    assert boxdlist.fetch_movie_details("zzzxqwv", "") is None


def test_fetch_movie_details_asks_the_proxy_with_name_and_year(monkeypatch):
    seen = capture_urls(monkeypatch)
    boxdlist.fetch_movie_details("Amelie", "2001")
    assert len(seen) == 1
    assert seen[0].startswith(boxdlist.DETAILS_API + "?")
    assert "name=Amelie" in seen[0]
    assert "year=2001" in seen[0]


def test_fetch_movie_details_without_year_does_not_send_it(monkeypatch):
    seen = capture_urls(monkeypatch)
    boxdlist.fetch_movie_details("Amelie", "")
    assert "year=" not in seen[0]


def test_fetch_movie_details_encodes_accents(monkeypatch):
    seen = capture_urls(monkeypatch)
    boxdlist.fetch_movie_details("Amélie", "2001")
    assert "Am%C3%A9lie" in seen[0]
    assert "é" not in seen[0]


def test_fetch_movie_details_encodes_ampersand_in_name(monkeypatch):
    # a raw "&" would cut the name in half and "Jerry" would look like a new parameter
    seen = capture_urls(monkeypatch)
    boxdlist.fetch_movie_details("Tom & Jerry", "2021")
    assert "name=Tom+%26+Jerry" in seen[0]

# ---------- build script ----------
def test_set_app_version_changes_only_that_line(tmp_path):
    path = tmp_path / "app.py"
    path.write_text('X = 1\nAPP_VERSION = "1.0.0"\nY = 2\n', encoding="utf-8")
    build.set_app_version("1.0.1", str(path))
    assert path.read_text(encoding="utf-8") == 'X = 1\nAPP_VERSION = "1.0.1"\nY = 2\n'

def test_set_app_version_keeps_windows_line_endings(tmp_path):
    path = tmp_path / "app.py"
    path.write_bytes(b'APP_VERSION = "1.0.0"\r\nY = 2\r\n')
    build.set_app_version("1.0.1", str(path))
    assert path.read_bytes() == b'APP_VERSION = "1.0.1"\r\nY = 2\r\n'

def test_set_app_version_stops_if_line_is_missing(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("Y = 2\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        build.set_app_version("1.0.1", str(path))
    assert path.read_text(encoding="utf-8") == "Y = 2\n"   # file untouched


def test_find_iscc_prefers_the_path(monkeypatch):
    monkeypatch.setattr(build.shutil, "which", lambda name: "C:/tools/ISCC.exe")
    assert build.find_iscc() == "C:/tools/ISCC.exe"

def test_find_iscc_uses_fallback_path(monkeypatch, tmp_path):
    fake = tmp_path / "ISCC.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(build.shutil, "which", lambda name: None)
    monkeypatch.setattr(build, "ISCC_PATH", str(fake))
    assert build.find_iscc() == str(fake)

def test_find_iscc_returns_none_if_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(build.shutil, "which", lambda name: None)
    monkeypatch.setattr(build, "ISCC_PATH", str(tmp_path / "nope.exe"))
    assert build.find_iscc() is None
    
# ---------- remove_movies ----------
def test_remove_movie_removes_only_the_target(movies):
    result = remove_movies(movies, [movies[1]])
    assert names(result) == ["Amélie", "Cidade de Deus", "No Year"]

def test_remove_movie_with_duplicates_removes_only_one():
    # two movies with the same data, but they are different objects
    first, second = movie("Same"), movie("Same")
    result = remove_movies([first, second], [first])
    assert len(result) == 1
    assert result[0] is second


def test_remove_movie_does_not_change_original_list(movies):
    original = list(movies)
    remove_movies(movies, [movies[0]])
    assert movies == original
    
def test_remove_two_movies_at_once():
    a, b, c, d = movie("A"), movie("B"), movie("C"), movie("D")
    result = remove_movies([a, b, c, d], [b, d])
    assert names(result) == ["A", "C"]

# ---------- delete in the interface ----------
def confirm_delete(monkeypatch, answer=True):
    # Fake the "Are you sure?" popup
    monkeypatch.setattr(boxdlist.messagebox, "askyesno",
                        lambda *args, **kwargs: answer)

def test_delete_selected_updates_list_and_csv(app, movies, tmp_path, monkeypatch):
    path = str(tmp_path / "w.csv")
    save_movies(path, movies)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: path)
    confirm_delete(monkeypatch)
    app.set_movies(movies)
    app.table.selection_set("1")   # no sorting, so row 1 is Blade Runner
    app.delete_selected()
    assert "Blade Runner" not in table_names(app)
    assert app.counter.cget("text") == "3 / 3"
    assert "Blade Runner" not in names(load_movies(path)[0])

def test_delete_selected_with_filter_keeps_the_other_movies_in_csv(
        app, movies, tmp_path, monkeypatch):
    # The CSV must keep the hidden movies, not only the visible ones
    path = str(tmp_path / "w.csv")
    save_movies(path, movies)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: path)
    confirm_delete(monkeypatch)
    app.set_movies(movies)
    app.search.set("blade")
    app.update_list()
    app.table.selection_set("0")
    app.delete_selected()
    assert names(load_movies(path)[0]) == [
        "Amélie", "Cidade de Deus", "No Year"]

def test_delete_selected_cancel_keeps_everything(app, movies, monkeypatch):
    confirm_delete(monkeypatch, answer=False)
    saved = []
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: "/tmp/w.csv")
    monkeypatch.setattr(boxdlist, "save_movies",
                        lambda path, items: saved.append(path))
    app.set_movies(movies)
    app.table.selection_set("0")
    app.delete_selected()
    assert saved == []
    assert len(app.movies) == 4


def test_delete_selected_without_selection_does_nothing(app, movies, monkeypatch):
    asked = []
    monkeypatch.setattr(boxdlist.messagebox, "askyesno",
                        lambda *args, **kwargs: asked.append(1))
    app.set_movies(movies)
    app.delete_selected()
    assert asked == []
    assert len(app.movies) == 4


def test_delete_selected_save_error_keeps_the_movie(app, movies, monkeypatch):
    errors = []
    def fail(path, items):
        raise OSError("disk full")
    confirm_delete(monkeypatch)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: "/tmp/w.csv")
    monkeypatch.setattr(boxdlist, "save_movies", fail)
    monkeypatch.setattr(boxdlist.messagebox, "showerror",
                        lambda *args, **kwargs: errors.append(args))
    app.set_movies(movies)
    app.table.selection_set("0")
    app.delete_selected()
    assert len(errors) == 1
    assert len(app.movies) == 4   # the list was not changed


def test_delete_selected_without_csv_still_removes_from_list(
        app, movies, monkeypatch):
    confirm_delete(monkeypatch)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: None)
    app.set_movies(movies)
    app.table.selection_set("0")
    app.delete_selected()
    assert "Amélie" not in table_names(app)

def test_delete_updates_decade_dropdown(app, movies, monkeypatch):
    confirm_delete(monkeypatch)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: None)
    app.set_movies(movies)
    assert "1980s (1)" in app.decade.cget("values")
    app.table.selection_set("1")   # Blade Runner, the only 1980s movie
    app.delete_selected()
    assert list(app.decade.cget("values")) == ["All", "2000s (2)"]

def test_delete_last_movie_of_selected_decade_resets_filter(
        app, movies, monkeypatch):
    confirm_delete(monkeypatch)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: None)
    app.set_movies(movies)
    app.decade.set("1980s")
    app.select_decade()
    app.update_list()
    assert table_names(app) == ["Blade Runner"]
    app.table.selection_set("0")
    app.delete_selected()
    assert app.decade.get() == "All"
    assert app.year_min.get() == ""
    assert len(table_names(app)) == 3


def test_delete_key_is_bound(app):
    assert app.table.bind("<Delete>") != ""
    
# ---------- count_new_movies ----------
def test_count_new_movies_same_lists(movies):
    assert count_new_movies(movies, movies) == 0


def test_count_new_movies_one_extra(movies):
    assert count_new_movies(movies[:3], movies) == 1


def test_count_new_movies_empty_old_list(movies):
    assert count_new_movies([], movies) == 4


def test_count_new_movies_ignores_removed(movies):
    # one removed, one added: only the added one counts
    assert count_new_movies(movies[:2], [movies[0], movies[2]]) == 1

def test_import_shows_new_movies_note(app, movies, tmp_path, monkeypatch):
    path = str(tmp_path / "w.csv")
    save_movies(path, movies)
    monkeypatch.setattr(boxdlist.filedialog, "askopenfilename",
                        lambda **kwargs: path)
    monkeypatch.setattr(boxdlist, "save_last_path", lambda p: None)
    monkeypatch.setattr(boxdlist.messagebox, "showwarning",
                        lambda *a, **k: None)   # "No Year" is incomplete
    app.set_movies(movies[:2])
    app.import_csv()
    assert app.new_label.cget("text") == "2 new movies since last time"

def test_first_import_shows_no_note(app, movies, tmp_path, monkeypatch):
    path = str(tmp_path / "w.csv")
    save_movies(path, movies)
    monkeypatch.setattr(boxdlist.filedialog, "askopenfilename",
                        lambda **kwargs: path)
    monkeypatch.setattr(boxdlist, "save_last_path", lambda p: None)
    monkeypatch.setattr(boxdlist.messagebox, "showwarning",
                        lambda *a, **k: None)
    app.import_csv()   # the app starts empty
    assert app.new_label.cget("text") == ""

def test_set_movies_clears_new_movies_note(app, movies):
    app.new_label.config(text="2 new movies since last time")
    app.set_movies(movies)
    assert app.new_label.cget("text") == ""
    
# ---------- drag and drop and import message ----------
def quiet_import(monkeypatch):
    # No real config write and no warning popups during imports
    monkeypatch.setattr(boxdlist, "save_last_path", lambda p: None)
    monkeypatch.setattr(boxdlist.messagebox, "showwarning",
                        lambda *a, **k: None)

def csv_file(tmp_path, items, name="w.csv"):
    # as_posix gives forward slashes, like tkdnd sends on Windows
    path = tmp_path / name
    save_movies(str(path), items)
    return path.as_posix()

def drop(app, text):
    # on_drop only reads event.data
    app.on_drop(SimpleNamespace(data=text))

def test_drop_csv_loads_movies(app, movies, tmp_path, monkeypatch):
    quiet_import(monkeypatch)
    drop(app, csv_file(tmp_path, movies))
    assert len(table_names(app)) == 4


def test_drop_accepts_uppercase_extension(app, movies, tmp_path, monkeypatch):
    quiet_import(monkeypatch)
    drop(app, csv_file(tmp_path, movies, name="W.CSV"))
    assert len(table_names(app)) == 4


def test_drop_path_with_spaces(app, movies, tmp_path, monkeypatch):
    quiet_import(monkeypatch)
    path = csv_file(tmp_path, movies, name="my watchlist.csv")
    # tkdnd wraps paths with spaces in braces
    drop(app, "{" + path + "}")
    assert len(table_names(app)) == 4


def test_drop_multiple_files_loads_only_the_first(app, movies, tmp_path, monkeypatch):
    quiet_import(monkeypatch)
    first = csv_file(tmp_path, movies[:2], name="a.csv")
    second = csv_file(tmp_path, movies, name="b.csv")
    drop(app, "{" + first + "} {" + second + "}")
    assert app.counter.cget("text") == "2 / 2"

def test_drop_non_csv_shows_message_and_keeps_list(app, movies, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(boxdlist.messagebox, "showinfo",
                        lambda *args, **kwargs: shown.append(args))
    app.set_movies(movies)
    drop(app, (tmp_path / "photo.png").as_posix())
    assert len(shown) == 1
    assert shown[0][1] == "Only CSV files can be dropped."
    assert len(app.movies) == 4


def test_drop_empty_data_does_nothing(app, movies, monkeypatch):
    shown = []
    monkeypatch.setattr(boxdlist.messagebox, "showinfo",
                        lambda *args, **kwargs: shown.append(args))
    app.set_movies(movies)
    drop(app, "")
    assert shown == []
    assert len(app.movies) == 4


def test_drop_invalid_csv_shows_error_and_keeps_list(app, movies, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(boxdlist.messagebox, "showerror",
                        lambda *args, **kwargs: errors.append(args))
    bad = tmp_path / "bad.csv"
    bad.write_text("Foo,Bar\n1,2\n", encoding="utf-8")   # wrong columns
    app.set_movies(movies)
    drop(app, bad.as_posix())
    assert len(errors) == 1
    assert len(app.movies) == 4


def test_load_path_shows_status_message(app, movies, tmp_path, monkeypatch):
    quiet_import(monkeypatch)
    warnings = []
    monkeypatch.setattr(boxdlist.messagebox, "showwarning",
                        lambda *args, **kwargs: warnings.append(args))
    app.status.config(text="")
    # first 3 movies are complete, so there is no warning
    app.load_path(csv_file(tmp_path, movies[:3]))
    assert app.status.cget("text") == "3 movies imported"
    assert warnings == []

def test_load_path_with_warning_has_no_status_message(app, movies, tmp_path, monkeypatch):
    quiet_import(monkeypatch)
    app.status.config(text="")
    # "No Year" is incomplete, so the warning popup is used instead
    app.load_path(csv_file(tmp_path, movies))
    assert app.status.cget("text") == ""

def test_new_movies_note_without_warning(app, movies, tmp_path, monkeypatch):
    quiet_import(monkeypatch)
    app.set_movies(movies[:2])
    app.load_path(csv_file(tmp_path, movies[:3]))   # all complete
    assert app.new_label.cget("text") == "1 new movie since last time"

def test_show_status_clears_itself(app, monkeypatch):
    scheduled = []
    # Capture the timer instead of waiting 4 seconds
    monkeypatch.setattr(app, "after",
                        lambda ms, func: scheduled.append((ms, func)) or "job")
    app._status_job = None
    app.show_status("hello")
    assert app.status.cget("text") == "hello"
    assert scheduled[0][0] == 3000
    scheduled[0][1]()   # run the clear function
    assert app.status.cget("text") == ""
# ---------- count decades ----------
def test_count_movies_by_decade(movies):
    # "No Year" is ignored, 2001 and 2002 share the 2000s
    assert count_movies_by_decade(movies) == {1980: 1, 2000: 2}
    
def test_update_decades_keeps_selected_decade(app, movies):
    app.set_movies(movies)
    app.decade.set("2000s (2)")
    app.select_decade()
    app.movies = remove_movies(app.movies, [movies[1]])
    app.update_decades()
    assert app.decade.get() == "2000s (2)"
    assert app.year_min.get() == "2000"

def test_update_decades_updates_count_of_selected_decade(app, movies):
    app.set_movies(movies)
    app.decade.set("2000s (2)")
    app.select_decade()
    app.movies = remove_movies(app.movies, [movies[2]])
    app.update_decades()
    assert app.decade.get() == "2000s (1)"
    assert app.year_min.get() == "2000"
    
# ---------- filter by dates ----------
TODAY = date(2024, 4, 1)

def test_filter_recent_without_days_returns_everything(movies):
    assert filter_recent(movies, None, TODAY) == movies


def test_filter_recent_30_days(movies):
    # limit is 2024-03-02, so Amélie (03-01) is just outside
    assert names(filter_recent(movies, 30, TODAY)) == ["No Year"]

def test_filter_recent_60_days(movies):
    # limit is 2024-02-01 (2024 is a leap year), only Blade Runner is older
    assert names(filter_recent(movies, 60, TODAY)) == [
        "Amélie", "Cidade de Deus", "No Year"]

def test_filter_recent_includes_the_limit_day():
    items = [movie("Edge", date="2024-03-02")]   # exactly 30 days ago
    assert filter_recent(items, 30, TODAY) == items


def test_filter_recent_ignores_missing_or_invalid_dates():
    items = [movie("Empty", date=""), movie("Broken", date="2024-13-45")]
    assert filter_recent(items, 365, TODAY) == []

def test_filter_recent_does_not_change_original_list(movies):
    original = list(movies)
    filter_recent(movies, 30, TODAY)
    assert movies == original

# ---------- recent filter in the interface ----------
def days_ago(n):
    # a date n days before today, as the CSV writes it
    return (date.today() - timedelta(days=n)).isoformat()

def recent_movies():
    # far from the 7, 30 and 365 day limits, so the tests never depend on the hour
    return [movie("New", date=days_ago(2)),
            movie("Month", date=days_ago(20)),
            movie("Old", date=days_ago(400))]

def test_recent_dropdown_lists_options(app):
    assert list(app.recent.cget("values")) == [
        "All", "Last 7 days", "Last 30 days", "Last year"]

def test_recent_filter_last_7_days(app):
    app.set_movies(recent_movies())
    app.recent.set("Last 7 days")
    app.update_list()
    assert table_names(app) == ["New"]

def test_recent_filter_last_30_days(app):
    app.set_movies(recent_movies())
    app.recent.set("Last 30 days")
    app.update_list()
    assert table_names(app) == ["New", "Month"]

def test_recent_filter_last_year(app):
    app.set_movies(recent_movies())
    app.recent.set("Last year")
    app.update_list()
    assert table_names(app) == ["New", "Month"]

def test_recent_filter_updates_counter(app):
    app.set_movies(recent_movies())
    app.recent.set("Last 7 days")
    app.update_list()
    assert app.counter.cget("text") == "1 / 3"

def test_recent_filter_combines_with_search(app):
    app.set_movies(recent_movies())
    app.recent.set("Last 30 days")
    app.search.set("mon")   # only "Month" has "mon" in the name
    app.update_list()
    assert table_names(app) == ["Month"]

def test_recent_filter_hides_movies_without_date(app):
    app.set_movies([movie("No Date", date=""), *recent_movies()])
    app.recent.set("Last year")
    app.update_list()
    assert "No Date" not in table_names(app)

def test_recent_all_keeps_movies_without_date(app):
    app.set_movies([movie("No Date", date=""), *recent_movies()])
    assert "No Date" in table_names(app)

def test_clear_filters_resets_recent(app):
    app.set_movies(recent_movies())
    app.recent.set("Last 7 days")
    app.update_list()
    app.clear_filters()
    assert app.recent.get() == "All"
    assert len(table_names(app)) == 3

def test_set_movies_resets_recent(app):
    # importing a new CSV starts without the old filter
    app.set_movies(recent_movies())
    app.recent.set("Last 7 days")
    app.set_movies(recent_movies())
    assert app.recent.get() == "All"

def test_recent_dropdown_is_bound(app):
    assert app.recent.bind("<<ComboboxSelected>>") != ""
    
# ---------- insert_movie ----------
def test_insert_movie_puts_it_at_the_position(movies):
    extra = movie("Extra")
    result = insert_movie(movies, extra, 1)
    assert names(result) == ["Amélie", "Extra", "Blade Runner",
                             "Cidade de Deus", "No Year"]

def test_insert_movie_past_the_end_appends(movies):
    extra = movie("Extra")
    assert insert_movie(movies, extra, 99)[-1] is extra

def test_insert_movie_does_not_change_original_list(movies):
    original = list(movies)
    insert_movie(movies, movie("Extra"), 0)
    assert movies == original

# ---------- undo delete in the interface ----------
def test_undo_delete_restores_list_and_csv(app, movies, tmp_path, monkeypatch):
    path = str(tmp_path / "w.csv")
    save_movies(path, movies)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: path)
    confirm_delete(monkeypatch)
    app.set_movies(movies)
    app.table.selection_set("1")   # Blade Runner
    app.delete_selected()
    assert "Blade Runner" not in names(app.movies)
    app.undo_delete()
    # same movies, in the same order as before
    assert names(app.movies) == names(movies)
    assert names(load_movies(path)[0]) == names(movies)
    assert app.status.cget("text") == "Restored 'Blade Runner'"

def test_undo_delete_with_filter_keeps_original_position(
        app, movies, monkeypatch):
    confirm_delete(monkeypatch)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: None)
    app.set_movies(movies)
    app.search.set("cidade")
    app.update_list()
    app.table.selection_set("0")   # the only visible movie
    app.delete_selected()
    app.undo_delete()
    assert names(app.movies) == names(movies)

def test_delete_shows_undo_hint(app, movies, monkeypatch):
    confirm_delete(monkeypatch)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: None)
    app.set_movies(movies)
    app.table.selection_set("0")
    app.delete_selected()
    assert app.status.cget("text") == "Deleted 'Amélie' (Ctrl+Z to undo)"

def test_undo_without_delete_does_nothing(app, movies):
    app.set_movies(movies)
    app.undo_delete()
    assert len(app.movies) == 4

def test_undo_only_works_once(app, movies, monkeypatch):
    confirm_delete(monkeypatch)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: None)
    app.set_movies(movies)
    app.table.selection_set("0")
    app.delete_selected()
    app.undo_delete()
    app.undo_delete()   # nothing left to undo, must not add it twice
    assert len(app.movies) == 4

def test_undo_save_error_keeps_the_undo_available(app, movies, monkeypatch):
    errors = []
    confirm_delete(monkeypatch)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: None)
    app.set_movies(movies)
    app.table.selection_set("0")
    app.delete_selected()
    # now make the CSV fail, only for the undo
    def fail(path, items):
        raise OSError("disk full")
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: "/tmp/w.csv")
    monkeypatch.setattr(boxdlist, "save_movies", fail)
    monkeypatch.setattr(boxdlist.messagebox, "showerror",
                        lambda *args, **kwargs: errors.append(args))
    app.undo_delete()
    assert len(errors) == 1
    assert len(app.movies) == 3          # the movie was not restored
    assert app.last_deleted is not None  # and the user can try again

def test_set_movies_clears_undo(app, movies, monkeypatch):
    confirm_delete(monkeypatch)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: None)
    app.set_movies(movies)
    app.table.selection_set("0")
    app.delete_selected()
    app.set_movies(movies)   # like importing a new CSV
    assert app.last_deleted is None

def test_ctrl_z_shortcuts_undo_delete(app, monkeypatch):
    called = []
    monkeypatch.setattr(app, "undo_delete", lambda: called.append(1))
    # fixed focus, so the test does not depend on earlier tests
    monkeypatch.setattr(app, "focus_get", lambda: app.table)
    press(app, "<Control-z>")
    assert len(called) == 1
    press(app, "<Control-Z>")
    assert len(called) == 2

def test_ctrl_z_in_text_box_does_not_undo(app, monkeypatch):
    called = []
    monkeypatch.setattr(app, "undo_delete", lambda: called.append(1))
    monkeypatch.setattr(app, "focus_get", lambda: app.search_entry)
    app.on_undo_key()
    assert called == []

def test_ctrl_z_in_dropdown_does_not_undo(app, monkeypatch):
    called = []
    monkeypatch.setattr(app, "undo_delete", lambda: called.append(1))
    monkeypatch.setattr(app, "focus_get", lambda: app.decade)
    app.on_undo_key()
    assert called == []
    
def test_delete_several_movies_asks_once_and_removes_all(
        app, movies, tmp_path, monkeypatch):
    path = str(tmp_path / "w.csv")
    save_movies(path, movies)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: path)
    asked = []
    monkeypatch.setattr(boxdlist.messagebox, "askyesno",
                        lambda *args, **kwargs: asked.append(args) or True)
    app.set_movies(movies)
    app.table.selection_set("0", "2")   # Amélie and Cidade de Deus
    app.delete_selected()
    assert len(asked) == 1               # one question for both
    assert names(app.movies) == ["Blade Runner", "No Year"]
    assert names(load_movies(path)[0]) == ["Blade Runner", "No Year"]
    assert app.status.cget("text") == "Deleted 2 movies (Ctrl+Z to undo)"


def test_undo_several_movies_restores_original_order(
        app, movies, tmp_path, monkeypatch):
    path = str(tmp_path / "w.csv")
    save_movies(path, movies)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: path)
    confirm_delete(monkeypatch)
    app.set_movies(movies)
    app.table.selection_set("1", "3")   # Blade Runner and No Year
    app.delete_selected()
    app.undo_delete()
    assert names(app.movies) == names(movies)
    assert names(load_movies(path)[0]) == names(movies)
    assert app.status.cget("text") == "Restored 2 movies"


def test_undo_several_movies_with_filter_keeps_positions(
        app, movies, monkeypatch):
    confirm_delete(monkeypatch)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: None)
    app.set_movies(movies)
    app.search.set("de")   # shows only Blade Runner and Cidade de Deus
    app.update_list()
    app.table.selection_set(*app.table.get_children())
    app.delete_selected()
    app.undo_delete()
    assert names(app.movies) == names(movies)
    
# ---------- drag to select ----------
def mouse(y, state=0):
    # on_press and on_drag only read y and state
    return SimpleNamespace(x=10, y=y, state=state)

def fake_rows(app, monkeypatch):
    # y = 0 is row "0", y = 1 is row "1", and so on; anything else is blank
    monkeypatch.setattr(app.table, "identify_row",
                        lambda y: str(y) if y in (0, 1, 2, 3) else "")

def test_drag_selects_the_rows_between(app, movies, monkeypatch):
    app.set_movies(movies)
    fake_rows(app, monkeypatch)
    app.on_press(mouse(0))
    app.on_drag(mouse(2))
    assert set(app.table.selection()) == {"0", "1", "2"}

def test_drag_upwards_also_works(app, movies, monkeypatch):
    app.set_movies(movies)
    fake_rows(app, monkeypatch)
    app.on_press(mouse(3))
    app.on_drag(mouse(1))
    assert set(app.table.selection()) == {"1", "2", "3"}

def test_drag_back_shrinks_the_selection(app, movies, monkeypatch):
    app.set_movies(movies)
    fake_rows(app, monkeypatch)
    app.on_press(mouse(0))
    app.on_drag(mouse(3))
    app.on_drag(mouse(1))
    assert set(app.table.selection()) == {"0", "1"}

def test_drag_started_outside_the_rows_does_nothing(app, movies, monkeypatch):
    app.set_movies(movies)
    fake_rows(app, monkeypatch)
    app.table.selection_set("3")
    app.on_press(mouse(99))   # blank area or header
    app.on_drag(mouse(1))
    assert app.table.selection() == ("3",)

def test_drag_with_ctrl_is_left_alone(app, movies, monkeypatch):
    app.set_movies(movies)
    fake_rows(app, monkeypatch)
    app.table.selection_set("3")
    app.on_press(mouse(0, state=0x4))   # Ctrl held
    app.on_drag(mouse(1))
    assert app.table.selection() == ("3",)

def test_drag_over_blank_area_keeps_the_selection(app, movies, monkeypatch):
    app.set_movies(movies)
    fake_rows(app, monkeypatch)
    app.on_press(mouse(0))
    app.on_drag(mouse(2))
    app.on_drag(mouse(99))   # mouse went below the last row
    assert set(app.table.selection()) == {"0", "1", "2"}
    
# ---------- open several links ----------
def test_open_link_opens_every_selected_movie(app, monkeypatch):
    opened = []
    monkeypatch.setattr(boxdlist.webbrowser, "open",
                        lambda url: opened.append(url))
    items = [movie("A", url="https://boxd.it/a"),
             movie("B", url="https://boxd.it/b")]
    app.set_movies(items)
    app.table.selection_set("0", "1")
    app.open_link()
    assert opened == ["https://boxd.it/a", "https://boxd.it/b"]
    
def fake_browser(monkeypatch):
    # Replaces webbrowser.open so no real page opens; returns the list of links
    opened = []
    monkeypatch.setattr(boxdlist.webbrowser, "open",
                        lambda url: opened.append(url))
    return opened


def linked_movies(count):
    # Movies M0, M1, ... that all have a link
    return [movie(f"M{i}", url=f"https://boxd.it/{i}") for i in range(count)]


def select_all_rows(app):
    app.table.selection_set(*app.table.get_children())


def test_open_link_skips_movies_without_link(app, monkeypatch):
    opened = fake_browser(monkeypatch)
    app.set_movies([movie("A", url="https://boxd.it/a"),
                    movie("B", url=""),
                    movie("C", url="https://boxd.it/c")])
    select_all_rows(app)
    app.open_link()
    assert opened == ["https://boxd.it/a", "https://boxd.it/c"]


def test_open_link_with_no_links_shows_message(app, monkeypatch):
    opened = fake_browser(monkeypatch)
    shown = []
    monkeypatch.setattr(boxdlist.messagebox, "showinfo",
                        lambda *args, **kwargs: shown.append(args))
    app.set_movies([movie("A", url=""), movie("B", url="")])
    select_all_rows(app)
    app.open_link()
    assert opened == []
    assert len(shown) == 1
    assert shown[0][1] == "None of these movies has a link."


def test_open_link_many_pages_cancelled_opens_nothing(app, monkeypatch):
    opened = fake_browser(monkeypatch)
    asked = []
    monkeypatch.setattr(boxdlist.messagebox, "askyesno",
                        lambda *args, **kwargs: asked.append(args) or False)
    app.set_movies(linked_movies(6))   # one more than the limit
    select_all_rows(app)
    app.open_link()
    assert len(asked) == 1
    assert "6" in asked[0][1]   # the question shows how many pages
    assert opened == []


def test_open_link_many_pages_confirmed_opens_all(app, monkeypatch):
    opened = fake_browser(monkeypatch)
    confirm_delete(monkeypatch, answer=True)   # fakes any yes/no popup
    app.set_movies(linked_movies(6))
    select_all_rows(app)
    app.open_link()
    assert len(opened) == 6


def test_open_link_at_the_limit_does_not_ask(app, monkeypatch):
    opened = fake_browser(monkeypatch)
    asked = []
    monkeypatch.setattr(boxdlist.messagebox, "askyesno",
                        lambda *args, **kwargs: asked.append(args) or True)
    app.set_movies(linked_movies(boxdlist.MAX_OPEN_AT_ONCE))
    select_all_rows(app)
    app.open_link()
    assert asked == []   # exactly 5 pages: no question
    assert len(opened) == boxdlist.MAX_OPEN_AT_ONCE
    
# ---------- escape and ctrl backspace ----------
def test_clear_selection_removes_selection(app, movies):
    app.set_movies(movies)
    app.table.selection_set("1")
    app.clear_selection()
    assert app.table.selection() == ()

def test_clear_selection_keeps_the_filters(app, movies):
    app.set_movies(movies)
    app.search.set("blade")
    app.update_list()
    app.table.selection_set("0")
    app.clear_selection()
    assert app.search.get() == "blade"
    assert len(table_names(app)) == 1

def test_escape_shortcut_clears_selection(app, monkeypatch):
    called = []
    monkeypatch.setattr(app, "clear_selection", lambda: called.append(1))
    press(app, "<Escape>")
    assert len(called) == 1

def test_clear_selection_leaves_the_search_box(app):
    press(app, "<Control-f>")
    assert app.focus_get() is app.search_entry
    app.clear_selection()
    assert app.focus_get() is app.table
    assert not app.search_entry.selection_present()

def test_ctrl_backspace_shortcut_clears_filters(app, monkeypatch):
    called = []
    monkeypatch.setattr(app, "clear_filters", lambda: called.append(1))
    press(app, "<Control-BackSpace>")
    assert len(called) == 1
    
# ---------- selection counter ----------    
def test_counter_shows_selected_count(app, movies):
    app.set_movies(movies)
    app.table.selection_set("0", "1")
    app.update()   # lets Tk process the selection event
    assert app.counter.cget("text") == "4 / 4 (2 selected)"

def test_counter_selected_count_goes_away(app, movies):
    app.set_movies(movies)
    app.table.selection_set("0", "1")
    app.update()
    app.table.selection_remove("0", "1")
    app.update()
    # nothing selected, so the note must disappear
    assert app.counter.cget("text") == "4 / 4"
    
# ---------- filter_complete ----------
def test_filter_complete_keeps_only_complete_movies(movies):
    # "No Year" is the only incomplete movie in the fixture
    assert names(filter_complete(movies)) == [
        "Amélie", "Blade Runner", "Cidade de Deus"]

def test_filter_complete_removes_movies_missing_any_field():
    items = [movie("Full"),
             movie("No Date", date=""),
             movie("No Link", url=""),
             movie("No Year", year="")]
    assert names(filter_complete(items)) == ["Full"]

def test_filter_complete_does_not_change_original_list(movies):
    original = list(movies)
    filter_complete(movies)
    assert movies == original

# ---------- complete filter in the interface ----------
def test_complete_only_hides_incomplete_movies(app, movies):
    app.set_movies(movies)
    app.complete_only.set(True)
    app.update_list()
    assert "No Year" not in table_names(app)
    assert app.counter.cget("text") == "3 / 4"

def test_complete_only_off_shows_everything(app, movies):
    app.set_movies(movies)
    assert "No Year" in table_names(app)

def test_complete_only_combines_with_search(app, movies):
    app.set_movies(movies)
    app.complete_only.set(True)
    app.search.set("de")   # "Blade Runner" and "Cidade de Deus", both complete
    app.update_list()
    assert table_names(app) == ["Blade Runner", "Cidade de Deus"]

def test_clear_filters_unchecks_complete_only(app, movies):
    app.set_movies(movies)
    app.complete_only.set(True)
    app.update_list()
    app.clear_filters()
    assert app.complete_only.get() is False
    assert len(table_names(app)) == 4

def test_set_movies_unchecks_complete_only(app, movies):
    # importing a new CSV starts without the old filter
    app.set_movies(movies)
    app.complete_only.set(True)
    app.set_movies(movies)
    assert app.complete_only.get() is False
    
# ---------- initial_sort ----------
def test_initial_sort_defaults_to_oldest_added_first():
    assert initial_sort({}) == ("date", False)

def test_initial_sort_without_saved_column_ignores_direction():
    assert initial_sort({"reverse_order": True}) == ("date", False)

def test_initial_sort_null_column_uses_default():
    # on_close saves null when the user never clicked a header
    assert initial_sort({"sort_column": None}) == ("date", False)

def test_initial_sort_invalid_column_uses_default():
    assert initial_sort({"sort_column": "banana", "reverse_order": True}) == ("date", False)

def test_initial_sort_keeps_the_saved_choice():
    config = {"sort_column": "name", "reverse_order": True}
    assert initial_sort(config) == ("name", True)
    
# ---------- count_duplicates ----------
def test_count_duplicates_without_repeats(movies):
    assert count_duplicates(movies) == 0

def test_count_duplicates_one_extra_copy():
    assert count_duplicates([movie("A"), movie("A")]) == 1

def test_count_duplicates_counts_only_the_extra_copies():
    # the same movie 3 times means 2 extra copies
    assert count_duplicates([movie("A"), movie("A"), movie("A")]) == 2

def test_count_duplicates_same_name_different_year_is_not_a_duplicate():
    # remakes share a name but are different movies
    assert count_duplicates([movie("A", "2000"), movie("A", "2001")]) == 0

def test_count_duplicates_empty_list():
    assert count_duplicates([]) == 0

# ---------- duplicates in the import ----------
def test_import_with_duplicates_shows_warning(app, tmp_path, monkeypatch):
    quiet_import(monkeypatch)
    warnings = []
    monkeypatch.setattr(boxdlist.messagebox, "showwarning",
                        lambda *args, **kwargs: warnings.append(args))
    app.status.config(text="")
    # both movies are complete, so only the duplicate can trigger the warning
    app.load_path(csv_file(tmp_path, [movie("A"), movie("A")]))
    assert len(warnings) == 1
    assert "1 duplicate" in warnings[0][1]
    assert app.status.cget("text") == ""   # the status message is not used

def test_import_without_duplicates_shows_no_warning(app, tmp_path, monkeypatch):
    quiet_import(monkeypatch)
    warnings = []
    monkeypatch.setattr(boxdlist.messagebox, "showwarning",
                        lambda *args, **kwargs: warnings.append(args))
    app.load_path(csv_file(tmp_path, [movie("A"), movie("B")]))
    assert warnings == []
    
# ---------- format_runtime ----------
@pytest.mark.parametrize("minutes, expected", [
    (122, "2h 02min"),    # hours and minutes, minutes get a leading zero
    (120, "2h 00min"),    # exact hours
    (60, "1h 00min"),     # exactly one hour
    (45, "45min"),        # less than an hour: no "0h"
    (1, "1min"),          # smallest valid value
    (0, ""),              # TMDB sends 0 when it does not know
    (-5, ""),             # negative makes no sense
    (None, ""),           # TMDB sends null when it does not know
    ("122", ""),          # text is not trusted
    (122.0, ""),          # a float is not a whole number of minutes
    (True, ""),           # True counts as 1 in Python, but it is not a duration
])
def test_format_runtime(minutes, expected):
    assert format_runtime(minutes) == expected


# ---------- details_line ----------
@pytest.mark.parametrize("details, expected", [
    ({"genres": ["Comedy", "Romance"], "runtime": 122},
     "Comedy, Romance · 2h 02min"),            # everything is there
    ({"genres": [], "runtime": 122}, "2h 02min"),          # no genres
    ({"genres": ["Drama"], "runtime": None}, "Drama"),     # no runtime
    ({"genres": ["Drama"], "runtime": 0}, "Drama"),        # runtime 0 counts as missing
    ({"genres": [1, 2], "runtime": 60}, "1h 00min"),       # genres that are not text are ignored
    ({"genres": ["Drama", 3], "runtime": 90},
     "Drama · 1h 30min"),                      # only the text genres are kept
    ({"genres": "Drama", "runtime": 90}, "1h 30min"),      # genres should be a list
    ({"runtime": 45}, "45min"),                # genres key missing
    ({"genres": ["Drama"]}, "Drama"),          # runtime key missing
    ({}, ""),                                  # nothing at all
])
def test_details_line(details, expected):
    assert details_line(details) == expected
    
# ---------- details panel ----------
DETAILS = {"genres": ["Comedy"], "runtime": 122, "overview": "A story."}

def fake_threads(monkeypatch):
    # Replaces threading inside boxdlist, so on_select never starts a real
    # thread. Returns the list of threads that would have been started.
    started = []
    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            self.args = args
        def start(self):
            started.append(self)
    monkeypatch.setattr(boxdlist, "threading", SimpleNamespace(Thread=FakeThread))
    return started

def select_row(app, iid="0"):
    app.table.selection_set(iid)
    app.on_select()   # called directly, so the test does not wait for Tk events

def panel_texts(app):
    return (app.details_title.cget("text"),
            app.details_meta.cget("text"),
            app.details_overview.cget("text"))

# ---------- show_details, show_message, clear_details ----------
def test_show_details_fills_the_panel(app):
    app.show_details(movie("Amélie", "2001"),
                     {"genres": ["Comedy", "Romance"], "runtime": 122,
                      "overview": "  A story.  "})
    assert panel_texts(app) == ("Amélie (2001)",
                                "Comedy, Romance · 2h 02min",
                                "A story.")   # outer spaces are removed

def test_show_details_without_year_has_no_parentheses(app):
    app.show_details(movie("Amélie", year=""), DETAILS)
    assert app.details_title.cget("text") == "Amélie"

def test_show_details_without_details_shows_no_synopsis(app):
    app.show_details(movie("Amélie", "2001"), None)
    assert panel_texts(app) == ("Amélie (2001)", "", "No synopsis available.")

@pytest.mark.parametrize("overview", ["", "   ", None, 123])
def test_show_details_empty_or_invalid_synopsis(app, overview):
    app.show_details(movie("Amélie"), {"genres": ["Comedy"], "overview": overview})
    assert app.details_overview.cget("text") == "No synopsis available."

def test_show_message_replaces_the_old_details(app):
    app.show_details(movie("Amélie", "2001"), DETAILS)
    app.show_message(movie("Blade Runner", "1982"), "Loading...")
    # the genres of the old movie must not stay under the new title
    assert panel_texts(app) == ("Blade Runner (1982)", "", "Loading...")

def test_clear_details_empties_everything(app):
    app.show_details(movie("Amélie", "2001"), DETAILS)
    app.clear_details()
    assert panel_texts(app) == ("", "", "")

# ---------- on_select ----------
def test_select_one_movie_shows_loading_and_starts_a_request(app, movies, monkeypatch):
    started = fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "0")
    assert panel_texts(app) == ("Amélie (2001)", "", "Loading...")
    assert app.details_target is app.visible[0]
    assert len(started) == 1
    assert started[0].target == app._details_worker
    assert started[0].args[0] is app.visible[0]

def test_select_a_cached_movie_skips_the_request(app, movies, monkeypatch):
    started = fake_threads(monkeypatch)
    app.set_movies(movies)
    app.details_cache[boxdlist.movie_key(app.visible[0])] = DETAILS
    select_row(app, "0")
    assert panel_texts(app) == ("Amélie (2001)", "Comedy · 2h 02min", "A story.")
    assert started == []   # no network needed

def test_select_several_movies_empties_the_panel(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "0")
    app.table.selection_set("0", "1")
    app.on_select()
    assert app.details_target is None
    assert panel_texts(app) == ("", "", "")

def test_deselecting_empties_the_panel(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "0")
    app.clear_selection()
    app.on_select()
    assert app.details_target is None
    assert panel_texts(app) == ("", "", "")

def test_select_still_updates_the_counter(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "0")
    assert app.counter.cget("text") == "4 / 4 (1 selected)"

# ---------- _on_details_result ----------
def test_result_success_shows_details_and_caches_them(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "0")
    app._on_details_result(app.visible[0], DETAILS)
    assert panel_texts(app) == ("Amélie (2001)", "Comedy · 2h 02min", "A story.")
    assert app.details_cache[boxdlist.movie_key(app.visible[0])] == DETAILS

def test_result_failure_shows_a_message_and_is_not_cached(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "0")
    app._on_details_result(app.visible[0], None)
    assert panel_texts(app) == ("Amélie (2001)", "", "Could not load details.")
    assert app.details_cache == {}   # a failure must be tried again next time

def test_late_success_keeps_the_panel_but_is_cached(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "1")   # the user is now looking at Blade Runner
    app._on_details_result(app.visible[0], DETAILS)   # Amélie answers late
    assert panel_texts(app) == ("Blade Runner (1982)", "", "Loading...")
    # the answer is not lost: selecting Amélie later is instant
    assert boxdlist.movie_key(app.visible[0]) in app.details_cache

def test_late_failure_does_not_touch_the_panel(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "1")
    app._on_details_result(app.visible[0], None)
    assert panel_texts(app) == ("Blade Runner (1982)", "", "Loading...")
    assert app.details_cache == {}

def test_result_with_nothing_selected_keeps_the_panel_empty(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "0")
    app.clear_selection()
    app.on_select()   # the user deselected before the answer came
    app._on_details_result(app.visible[0], DETAILS)
    assert panel_texts(app) == ("", "", "")
    assert boxdlist.movie_key(app.visible[0]) in app.details_cache

# ---------- _details_worker ----------
def test_details_worker_hands_the_result_to_the_main_thread(app, movies, monkeypatch):
    asked = []
    def fake_fetch(name, year):
        asked.append((name, year))
        return DETAILS
    monkeypatch.setattr(boxdlist, "fetch_movie_details", fake_fetch)
    scheduled = []
    # capture after() instead of waiting for Tk to run it
    monkeypatch.setattr(app, "after",
                        lambda ms, func, *args: scheduled.append((ms, func, args)))
    app._details_worker(movies[0])
    assert asked == [("Amélie", "2001")]
    assert scheduled == [(0, app._on_details_result, (movies[0], DETAILS))]
    
# ---------- panel visibility ----------
def test_panel_is_hidden_when_nothing_is_selected(app, movies):
    app.set_movies(movies)
    assert app.details.winfo_manager() == ""

def test_selecting_one_movie_shows_the_panel(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "0")
    assert app.details.winfo_manager() == "pack"

def test_deselecting_hides_the_panel(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "0")
    app.clear_selection()
    app.on_select()
    assert app.details.winfo_manager() == ""

def test_rebuilding_the_table_hides_the_panel(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "0")
    app.update_list()   # same as typing in the search box: the selection is lost
    assert app.details.winfo_manager() == ""
    assert app.details_target is None

def test_panel_comes_back_on_the_right_of_the_scrollbar(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "0")
    app.clear_selection()
    app.on_select()
    select_row(app, "1")
    # pack order decides the position: panel, scrollbar, then the table
    assert app.table.master.pack_slaves() == [app.details, app.scrollbar, app.table]
    
# ---------- plan_summary ----------
@pytest.mark.parametrize("total, lines, expected", [
    (2, 20, (True, 2)),     # plenty of space: all movies, with details
    (2, 12, (True, 2)),     # exact fit (2 movies x 5 lines + 2 header lines)
    (3, 17, (True, 3)),     # exact fit again
    (10, 22, (True, 3)),    # only 3 fit with details, the rest is "+ N more"
    (4, 17, (False, 4)),    # fewer than 3 would fit with details: names only
    (2, 8, (False, 2)),     # small panel: names only
    (10, 12, (False, 10)),  # names only, all of them fit
    (30, 12, (False, 9)),   # names only, one line is kept for "+ N more"
    (5, 1, (False, 0)),     # almost no space at all
])
def test_plan_summary(total, lines, expected):
    assert boxdlist.plan_summary(total, lines) == expected


# ---------- build_summary ----------
def test_build_summary_names_only(movies):
    lines = boxdlist.build_summary(movies[:3], 3, False, 30, {}, set())
    assert lines == [("3 movies selected", "header"), ("", ""),
                     ("Amélie (2001)", "title"),
                     ("Blade Runner (1982)", "title"),
                     ("Cidade de Deus (2002)", "title")]

def test_build_summary_without_year_has_no_parentheses():
    lines = boxdlist.build_summary([movie("Amélie", year="")], 1, False, 30, {}, set())
    assert lines[2] == ("Amélie", "title")

def test_build_summary_with_details(movies):
    cache = {boxdlist.movie_key(movies[0]):
             {"genres": ["Comedy"], "runtime": 122, "overview": "A story."}}
    lines = boxdlist.build_summary(movies[:2], 2, True, 30, cache, set())
    assert lines[2:7] == [("Amélie (2001)", "title"),
                          ("Comedy · 2h 02min", "meta"),
                          ("A story.", "overview"), ("", "overview"),
                          ("", "")]
    # the second movie has no details yet, but takes the same number of lines
    assert lines[7:] == [("Blade Runner (1982)", "title"), ("", "meta"),
                         ("", "overview"), ("", "overview"), ("", "")]

def test_build_summary_shows_loading_for_movies_in_progress(movies):
    loading = {boxdlist.movie_key(movies[0])}
    lines = boxdlist.build_summary(movies[:1], 1, True, 30, {}, loading)
    assert lines[3] == ("Loading...", "meta")

def test_build_summary_adds_a_more_line(movies):
    lines = boxdlist.build_summary(movies, 2, False, 30, {}, set())
    assert lines[-1] == ("+ 2 more", "meta")
    assert len([style for _, style in lines if style == "title"]) == 2

def test_build_summary_cuts_a_long_synopsis_to_two_lines(movies):
    cache = {boxdlist.movie_key(movies[0]): {"overview": "word " * 40}}
    lines = boxdlist.build_summary(movies[:1], 1, True, 20, cache, set())
    overview = [text for text, style in lines if style == "overview"]
    assert len(overview) == 2
    assert overview[1].endswith("...")
    assert all(len(text) <= 20 for text in overview)

def test_build_summary_shortens_a_long_title():
    long_movie = movie("The Extraordinarily Long Movie Title Of All Time", "2001")
    lines = boxdlist.build_summary([long_movie], 1, False, 20, {}, set())
    assert lines[2][0].endswith("...")
    assert len(lines[2][0]) <= 20


# ---------- several movies selected, in the interface ----------
def set_space(app, monkeypatch, lines=40, width=30):
    # the test window is hidden, so it has no real size: fake it
    monkeypatch.setattr(app, "summary_capacity", lambda: lines)
    monkeypatch.setattr(app, "summary_width", lambda: width)

def summary_text(app):
    return app.summary.get("1.0", "end")

def select_two(app):
    app.table.selection_set("0", "1")
    app.on_select()

def test_selecting_several_movies_keeps_the_panel(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    set_space(app, monkeypatch)
    app.set_movies(movies)
    select_two(app)
    assert app.details.winfo_manager() == "pack"

def test_select_several_movies_shows_the_list(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    set_space(app, monkeypatch)
    app.set_movies(movies)
    select_two(app)
    assert app.summary.winfo_manager() == "pack"
    assert app.single_view.winfo_manager() == ""
    assert app.summary_movies == [app.visible[0], app.visible[1]]
    assert app.details_target is None
    text = summary_text(app)
    assert "2 movies selected" in text
    assert "Amélie (2001)" in text
    assert "Blade Runner (1982)" in text

def test_list_follows_the_order_of_the_table(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    set_space(app, monkeypatch)
    app.set_movies(movies)
    app.table.selection_set("2", "0")   # selected in the "wrong" order
    app.on_select()
    assert app.summary_movies == [app.visible[0], app.visible[2]]

def test_list_requests_details_once_per_movie(app, movies, monkeypatch):
    started = fake_threads(monkeypatch)
    set_space(app, monkeypatch, lines=40)
    app.set_movies(movies)
    select_two(app)
    assert len(started) == 2
    assert len(app.details_inflight) == 2
    assert "Loading..." in summary_text(app)
    app.render_summary()   # drawing again must not ask again
    assert len(started) == 2

def test_list_with_little_space_asks_for_nothing(app, movies, monkeypatch):
    started = fake_threads(monkeypatch)
    set_space(app, monkeypatch, lines=6)   # names only
    app.set_movies(movies)
    select_two(app)
    assert started == []
    assert "Loading..." not in summary_text(app)
    assert "Amélie (2001)" in summary_text(app)

def test_list_skips_movies_that_are_already_cached(app, movies, monkeypatch):
    started = fake_threads(monkeypatch)
    set_space(app, monkeypatch, lines=40)
    app.set_movies(movies)
    app.details_cache[boxdlist.movie_key(app.visible[0])] = DETAILS
    select_two(app)
    assert len(started) == 1   # only the second movie needs a request
    assert "Comedy · 2h 02min" in summary_text(app)

def test_answer_refreshes_the_list(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    set_space(app, monkeypatch, lines=40)
    app.set_movies(movies)
    select_two(app)
    app._on_details_result(app.visible[0], DETAILS)
    assert "Comedy · 2h 02min" in summary_text(app)
    assert boxdlist.movie_key(app.visible[0]) not in app.details_inflight

def test_failed_details_are_not_asked_again_while_drawing(app, movies, monkeypatch):
    started = fake_threads(monkeypatch)
    set_space(app, monkeypatch, lines=40)
    app.set_movies(movies)
    select_two(app)
    app._on_details_result(app.visible[0], None)
    assert boxdlist.movie_key(app.visible[0]) in app.details_failed
    assert len(started) == 2   # the failed movie was not requested again

def test_a_new_selection_retries_failed_details(app, movies, monkeypatch):
    started = fake_threads(monkeypatch)
    set_space(app, monkeypatch, lines=40)
    app.set_movies(movies)
    select_two(app)
    app._on_details_result(app.visible[0], None)
    app.on_select()   # the selection changed (even to the same movies)
    assert len(started) == 3

def test_going_back_to_one_movie_shows_the_single_view(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    set_space(app, monkeypatch)
    app.set_movies(movies)
    select_two(app)
    select_row(app, "0")
    assert app.single_view.winfo_manager() == "pack"
    assert app.summary.winfo_manager() == ""
    assert app.summary_movies == []

def test_deselecting_everything_hides_the_panel_and_the_list(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    set_space(app, monkeypatch)
    app.set_movies(movies)
    select_two(app)
    app.clear_selection()
    app.on_select()
    assert app.details.winfo_manager() == ""
    assert app.summary_movies == []

# ---------- movies_to_fetch ----------
def test_movies_to_fetch_skips_movies_in_the_cache(movies):
    cache = {boxdlist.movie_key(movies[0]): {"runtime": 90}}
    result = boxdlist.movies_to_fetch(movies, cache, set())
    assert names(result) == ["Blade Runner", "Cidade de Deus", "No Year"]

def test_movies_to_fetch_skips_movies_already_on_their_way(movies):
    skip = {boxdlist.movie_key(movies[1])}
    result = boxdlist.movies_to_fetch(movies, {}, skip)
    assert "Blade Runner" not in names(result)
    assert len(result) == 3

def test_movies_to_fetch_returns_duplicates_only_once():
    first, second = movie("Same"), movie("Same")
    result = boxdlist.movies_to_fetch([first, second], {}, set())
    assert len(result) == 1
    assert result[0] is first   # the first copy is the one kept

def test_movies_to_fetch_keeps_remakes():
    # same name, different year: they are different movies
    items = [movie("A", "2000"), movie("A", "2001")]
    assert len(boxdlist.movies_to_fetch(items, {}, set())) == 2

def test_movies_to_fetch_does_not_change_its_inputs(movies):
    cache = {boxdlist.movie_key(movies[0]): {}}
    skip = {boxdlist.movie_key(movies[1])}
    original = list(movies)
    boxdlist.movies_to_fetch(movies, cache, skip)
    assert movies == original
    assert len(cache) == 1
    assert len(skip) == 1

def test_movies_to_fetch_with_no_movies():
    assert boxdlist.movies_to_fetch([], {}, set()) == []

def http_error(code, body=b""):
    # urlopen raises HTTPError, which is a kind of OSError
    return urllib.error.HTTPError("https://x", code, "msg", None, io.BytesIO(body))

def raises(error):
    # Fake urlopen that always fails with 'error'
    def fake(url, timeout=None):
        raise error
    return fake

def test_fetch_movie_details_not_found_returns_the_marker(monkeypatch):
    error = http_error(404, b'{"error": "Not found"}')
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen", raises(error))
    result = boxdlist.fetch_movie_details("zzzxqwv", "")
    assert result == boxdlist.NOT_FOUND

def test_fetch_movie_details_not_found_is_a_copy(monkeypatch):
    error = http_error(404, b'{"error": "Not found"}')
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen", raises(error))
    result = boxdlist.fetch_movie_details("zzzxqwv", "")
    result["extra"] = 1   # changing the result must not change the constant
    assert boxdlist.NOT_FOUND == {"not_found": True}

def test_fetch_movie_details_wrong_address_is_not_marked(monkeypatch):
    # Vercel answers a wrong address with its own 404 page
    error = http_error(404, b"<html>The page could not be found</html>")
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen", raises(error))
    assert boxdlist.fetch_movie_details("Amelie", "2001") is None

def test_fetch_movie_details_404_without_body_returns_none(monkeypatch):
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen",
                        raises(http_error(404)))
    assert boxdlist.fetch_movie_details("Amelie", "2001") is None

@pytest.mark.parametrize("code", [400, 500, 502])
def test_fetch_movie_details_other_http_errors_are_not_marked(monkeypatch, code):
    # even with the same body, only a 404 means "not found"
    error = http_error(code, b'{"error": "Not found"}')
    monkeypatch.setattr(boxdlist.urllib.request, "urlopen", raises(error))
    assert boxdlist.fetch_movie_details("Amelie", "2001") is None

@pytest.mark.parametrize("value, expected", [
    ({"not_found": True}, True),
    ({"not_found": False}, False),
    ({"not_found": 1}, False),        # only a real True counts
    ({"genres": ["Comedy"]}, False),
    ({}, False),
    (None, False),
    ("text", False),
])
def test_is_not_found(value, expected):
    assert boxdlist.is_not_found(value) is expected

def test_details_cache_keeps_the_not_found_marker(tmp_path, monkeypatch):
    cache_file(tmp_path, monkeypatch)
    original = {("Ghost", "2020"): dict(boxdlist.NOT_FOUND)}
    boxdlist.write_details_cache(original)
    assert boxdlist.read_details_cache() == original

def test_not_found_is_cached_and_not_marked_as_failed(app, movies, monkeypatch):
    fake_threads(monkeypatch)
    app.set_movies(movies)
    select_row(app, "0")
    key = boxdlist.movie_key(app.visible[0])
    app._on_details_result(app.visible[0], dict(boxdlist.NOT_FOUND))
    assert key in app.details_cache
    assert key not in app.details_failed
    assert panel_texts(app) == ("Amélie (2001)", "",
                                "No details found for this movie.")

def test_select_a_not_found_movie_skips_the_request(app, movies, monkeypatch):
    started = fake_threads(monkeypatch)
    app.set_movies(movies)
    app.details_cache[boxdlist.movie_key(app.visible[0])] = dict(boxdlist.NOT_FOUND)
    select_row(app, "0")
    assert started == []   # no network needed
    assert panel_texts(app)[2] == "No details found for this movie."

def test_prefetch_skips_movies_known_to_be_not_found(app, movies):
    app.set_movies(movies)
    app.details_cache[boxdlist.movie_key(movies[0])] = dict(boxdlist.NOT_FOUND)
    app.prefetch_details()
    assert "Amélie" not in names(submitted_movies(app))
    assert len(app.executor.submitted) == 3

def test_build_summary_with_a_not_found_marker(movies):
    cache = {boxdlist.movie_key(movies[0]): dict(boxdlist.NOT_FOUND)}
    lines = boxdlist.build_summary(movies[:1], 1, True, 30, cache, set())
    assert lines[3] == ("", "meta")   # no genres or runtime, and no crash

# ---------- prefetch_details ----------
def submitted_movies(app):
    # the movie of every request that was sent to the executor
    return [args[0] for _, args in app.executor.submitted]

def test_prefetch_sends_one_request_per_movie(app, movies):
    app.set_movies(movies)
    app.prefetch_details()
    assert names(submitted_movies(app)) == names(movies)
    # every request goes to the background worker
    assert all(func == app._prefetch_worker for func, _ in app.executor.submitted)

def test_prefetch_skips_cached_movies(app, movies):
    app.set_movies(movies)
    app.details_cache[boxdlist.movie_key(movies[0])] = DETAILS
    app.prefetch_details()
    assert "Amélie" not in names(submitted_movies(app))
    assert len(app.executor.submitted) == 3

def test_prefetch_skips_movies_already_on_their_way(app, movies):
    app.set_movies(movies)
    app.details_inflight.add(boxdlist.movie_key(movies[1]))
    app.prefetch_details()
    assert "Blade Runner" not in names(submitted_movies(app))

def test_prefetch_marks_every_movie_as_on_its_way(app, movies):
    app.set_movies(movies)
    app.prefetch_details()
    assert app.details_inflight == {boxdlist.movie_key(m) for m in movies}

def test_prefetch_sets_the_counters(app, movies):
    app.set_movies(movies)
    app.prefetch_done = 5   # a leftover from an older round
    app.prefetch_details()
    assert app.prefetch_total == 4
    assert app.prefetch_done == 0

def test_prefetch_with_everything_cached_sends_nothing(app, movies):
    app.set_movies(movies)
    for m in movies:
        app.details_cache[boxdlist.movie_key(m)] = DETAILS
    app.prefetch_details()
    assert app.executor.submitted == []
    assert app.details_inflight == set()

def test_prefetch_asks_once_for_duplicate_movies(app):
    app.set_movies([movie("Same"), movie("Same")])
    app.prefetch_details()
    assert len(app.executor.submitted) == 1

def test_prefetch_twice_does_not_repeat_requests(app, movies):
    app.set_movies(movies)
    app.prefetch_details()
    app.prefetch_details()   # everything is already on its way
    assert len(app.executor.submitted) == 4

# ---------- _prefetch_worker ----------
def test_prefetch_worker_hands_the_result_to_the_main_thread(app, movies, monkeypatch):
    monkeypatch.setattr(boxdlist, "fetch_movie_details",
                        lambda name, year: DETAILS)
    scheduled = []
    # capture after() instead of waiting for Tk to run it
    monkeypatch.setattr(app, "after",
                        lambda ms, func, *args: scheduled.append((ms, func, args)))
    app._prefetch_worker(movies[0])
    assert scheduled == [(0, app._on_prefetch_result, (movies[0], DETAILS))]

def test_prefetch_worker_passes_failures_on(app, movies, monkeypatch):
    # the fixture already makes fetch_movie_details return None
    scheduled = []
    monkeypatch.setattr(app, "after",
                        lambda ms, func, *args: scheduled.append(args))
    app._prefetch_worker(movies[0])
    assert scheduled == [(movies[0], None)]

@pytest.mark.parametrize("error", [RuntimeError, tk.TclError])
def test_prefetch_worker_ignores_a_closed_window(app, movies, monkeypatch, error):
    def window_is_gone(*args, **kwargs):
        raise error("window closed")
    monkeypatch.setattr(app, "after", window_is_gone)
    app._prefetch_worker(movies[0])   # must not raise

# ---------- _on_prefetch_result ----------
def test_prefetch_result_stores_the_details(app, movies):
    app.prefetch_total = 2
    app._on_prefetch_result(movies[0], DETAILS)
    assert app.details_cache[boxdlist.movie_key(movies[0])] == DETAILS

def test_prefetch_result_takes_the_movie_off_the_waiting_list(app, movies):
    key = boxdlist.movie_key(movies[0])
    app.details_inflight.add(key)
    app.prefetch_total = 2
    app._on_prefetch_result(movies[0], DETAILS)
    assert key not in app.details_inflight

def test_prefetch_result_shows_the_progress(app, movies):
    app.prefetch_total = 3
    app.prefetch_done = 0
    app._on_prefetch_result(movies[0], DETAILS)
    assert app.prefetch_done == 1
    assert app.status.cget("text") == "Details 1/3"

def test_prefetch_last_result_shows_ready(app, movies):
    app.prefetch_total = 2
    app.prefetch_done = 0
    app._on_prefetch_result(movies[0], DETAILS)
    app._on_prefetch_result(movies[1], DETAILS)
    assert app.status.cget("text") == "Details ready"

def test_prefetch_failed_result_still_counts(app, movies):
    app.prefetch_total = 2
    app.prefetch_done = 0
    app._on_prefetch_result(movies[0], None)
    assert app.prefetch_done == 1
    assert app.status.cget("text") == "Details 1/2"
    assert app.details_cache == {}   # a failure is not saved
    assert boxdlist.movie_key(movies[0]) in app.details_failed

def test_prefetch_result_past_the_total_still_shows_ready(app, movies):
    # a late answer from an older round can push the count over the total
    app.prefetch_total = 1
    app.prefetch_done = 1
    app._on_prefetch_result(movies[0], DETAILS)
    assert app.prefetch_done == 2
    assert app.status.cget("text") == "Details ready"

# ---------- prefetch is started by the imports ----------
def test_load_path_starts_the_prefetch(app, movies, tmp_path, monkeypatch):
    quiet_import(monkeypatch)
    app.load_path(csv_file(tmp_path, movies))
    assert len(app.executor.submitted) == 4

def test_load_path_with_an_invalid_csv_starts_no_prefetch(app, tmp_path, monkeypatch):
    monkeypatch.setattr(boxdlist.messagebox, "showerror",
                        lambda *args, **kwargs: None)
    bad = tmp_path / "bad.csv"
    bad.write_text("Foo,Bar\n1,2\n", encoding="utf-8")   # wrong columns
    app.load_path(bad.as_posix())
    assert app.executor.submitted == []

def test_load_last_csv_starts_the_prefetch(app, movies, tmp_path, monkeypatch):
    path = csv_file(tmp_path, movies)
    monkeypatch.setattr(boxdlist, "read_last_path", lambda: path)
    app.load_last_csv()
    assert len(app.executor.submitted) == 4

def test_load_last_csv_without_a_file_starts_no_prefetch(app, tmp_path, monkeypatch):
    monkeypatch.setattr(boxdlist, "read_last_path",
                        lambda: str(tmp_path / "missing.csv"))
    app.load_last_csv()
    assert app.executor.submitted == []

# ---------- closing the app ----------
def test_on_close_drops_the_queued_requests(app, monkeypatch):
    monkeypatch.setattr(boxdlist, "read_config", lambda: {})
    monkeypatch.setattr(boxdlist, "write_config", lambda data: None)
    monkeypatch.setattr(app, "destroy", lambda: None)
    app.on_close()
    assert app.executor.shutdown_calls == [{"wait": False, "cancel_futures": True}]