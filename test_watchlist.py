import pytest
import tkinter as tk

import boxdlist
from boxdlist import (filter_movies, is_incomplete, load_movies, normalize,
                      pick_random, read_last_path, save_last_path,
                      sort_movies, year_as_int, year_range_error, valid_window_size, valid_sort_column,
                      valid_reverse_order, save_movies, is_newer, find_installer_url, fetch_latest_release, download_file, remove_movie)
import build


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
    
# UI testing

@pytest.fixture(scope="session")
def _window():
    # monkeypatch only works per test, so use its context manager here
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(boxdlist, "read_last_path", lambda: None)
        mp.setattr(boxdlist, "read_config", lambda: {})
        try:
            window = boxdlist.App()
        except tk.TclError as error:
            pytest.skip(f"Could not create window: {error}")
    window.withdraw()
    yield window
    window.destroy()
    
@pytest.fixture
def app(_window):
    # Back to a clean state before each test
    _window.withdraw()
    _window.set_movies([])
    _window.sort_column = None
    _window.reverse_order = False
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
    assert list(app.decade.cget("values")) == ["All", "1980s", "2000s"]
    
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
    ("banana", "760x600"),     # bad text falls back
    (12345, "760x600"),        # not even text
    (None, "760x600"),         # key missing in the file
])
def test_valid_window_size(value, expected):
    assert valid_window_size(value) == expected
    
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
    
# ---------- remove_movie ----------
def test_remove_movie_removes_only_the_target(movies):
    result = remove_movie(movies, movies[1])
    assert names(result) == ["Amélie", "Cidade de Deus", "No Year"]


def test_remove_movie_with_duplicates_removes_only_one():
    # two movies with the same data, but they are different objects
    first, second = movie("Same"), movie("Same")
    result = remove_movie([first, second], first)
    assert len(result) == 1
    assert result[0] is second


def test_remove_movie_does_not_change_original_list(movies):
    original = list(movies)
    remove_movie(movies, movies[0])
    assert movies == original


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
    assert "1980s" in app.decade.cget("values")
    app.table.selection_set("1")   # Blade Runner, the only 1980s movie
    app.delete_selected()
    assert list(app.decade.cget("values")) == ["All", "2000s"]


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