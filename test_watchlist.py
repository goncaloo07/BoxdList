import pytest

import boxdlist
from boxdlist import (filter_movies, is_incomplete, load_movies, normalize,
                      pick_random, read_last_path, save_last_path,
                      sort_movies, year_as_int)


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