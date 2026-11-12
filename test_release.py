import os
from types import SimpleNamespace
import pytest
import release

TAG = "1.0.0"

def result(stdout="", stderr="", code=0):
    # Looks like what subprocess.run returns
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)

class FakeGit:
    """Pretends to be git and gh. It starts in a state where a release is allowed."""
    def __init__(self):
        self.calls = []        # every command that was run
        self.push_code = 0     # exit code of "git push"
        self.create_code = 0   # exit code of "gh release create"
        # command as text -> answer
        self.answers = {
            "git branch --show-current": result("main\n"),
            "git status --porcelain": result(""),                       # all committed
            "git rev-list --count @{u}..HEAD": result("0\n"),           # all pushed
            "gh release view " + TAG: result("", "release not found", 1),
            "git rev-parse HEAD": result("abc123\n"),
            "git rev-parse --abbrev-ref @{u}": result("origin/main\n"), # has upstream
        }

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        text = " ".join(command)
        if command[:3] == ["gh", "release", "create"]:
            return result(code=self.create_code)
        if command[:2] == ["git", "push"]:
            return result(code=self.push_code)
        if text in self.answers:
            return self.answers[text]
        raise AssertionError(f"Unexpected command: {text}")

    def created(self):
        # the "gh release create" calls
        return [c for c in self.calls if c[:3] == ["gh", "release", "create"]]

def no_input(prompt=""):
    raise AssertionError("input() was called, but it should not be")

def type_in(monkeypatch, *lines):
    # The next input() calls return these lines, one by one
    answers = iter(lines)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

def value_after(command, flag):
    # ["--title", "X"] -> "X"
    return command[command.index(flag) + 1]

@pytest.fixture
def git(tmp_path, monkeypatch):
    # Work in a temp folder, so the real boxdlist.py and installer are never touched
    monkeypatch.chdir(tmp_path)
    code = tmp_path / "boxdlist.py"
    installer = tmp_path / "BoxdListSetup.exe"
    code.write_text("", encoding="utf-8")
    installer.write_text("", encoding="utf-8")
    os.utime(code, (1000, 1000))
    os.utime(installer, (2000, 2000))   # newer than the code
    monkeypatch.setattr(release, "INSTALLER", installer)
    monkeypatch.setattr(release.shutil, "which", lambda name: name)
    monkeypatch.setattr("builtins.input", no_input)
    fake = FakeGit()
    monkeypatch.setattr(release.subprocess, "run", fake)
    fake.installer = installer
    return fake

# ---------- ask ----------
@pytest.mark.parametrize("answer, expected", [
    ("y", True), ("Y", True), (" y ", True),
    ("yes", False), ("n", False), ("", False),
])
def test_ask_only_accepts_y(monkeypatch, answer, expected):
    type_in(monkeypatch, answer)
    assert release.ask("Continue?") is expected

# ---------- release: happy path ----------
def test_release_creates_it_with_the_right_values(git, monkeypatch):
    type_in(monkeypatch, "Fixed a bug", "", "y")
    release.release(TAG)
    assert len(git.created()) == 1
    command = git.created()[0]
    assert command[3] == TAG
    assert str(git.installer) in command
    assert value_after(command, "--title") == "BoxdList v1.0.0"
    assert value_after(command, "--notes") == "Fixed a bug"
    assert value_after(command, "--target") == "abc123"

def test_release_joins_description_lines(git, monkeypatch):
    type_in(monkeypatch, "Line one", "Line two", "", "y")
    release.release(TAG)
    assert value_after(git.created()[0], "--notes") == "Line one\nLine two"

# ---------- release: stops before publishing ----------
def test_release_without_gh_stops(git, monkeypatch):
    monkeypatch.setattr(release.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="GitHub CLI"):
        release.release(TAG)
    assert git.created() == []

def test_release_outside_main_stops(git):
    git.answers["git branch --show-current"] = result("dev\n")
    with pytest.raises(SystemExit, match="You are on 'dev'"):
        release.release(TAG)
    assert git.created() == []

def test_release_without_installer_stops(git):
    git.installer.unlink()
    with pytest.raises(SystemExit, match="Installer not found"):
        release.release(TAG)
    assert git.created() == []

def test_release_with_old_installer_stops(git):
    os.utime(git.installer, (500, 500))   # older than boxdlist.py
    with pytest.raises(SystemExit, match="older than boxdlist.py"):
        release.release(TAG)
    assert git.created() == []

def test_release_with_uncommitted_changes_stops(git):
    git.answers["git status --porcelain"] = result(" M boxdlist.py\n")
    with pytest.raises(SystemExit, match="uncommitted"):
        release.release(TAG)
    assert git.created() == []

def test_release_with_unpushed_commits_stops(git):
    git.answers["git rev-list --count @{u}..HEAD"] = result("2\n")
    with pytest.raises(SystemExit, match="not pushed"):
        release.release(TAG)
    assert git.created() == []

def test_release_without_upstream_stops(git):
    # a branch that was never pushed makes this command fail
    git.answers["git rev-list --count @{u}..HEAD"] = result("", "no upstream", 128)
    with pytest.raises(SystemExit, match="not pushed"):
        release.release(TAG)
    assert git.created() == []

def test_release_that_already_exists_stops(git):
    git.answers["gh release view " + TAG] = result("", "", 0)
    with pytest.raises(SystemExit, match="already exists"):
        release.release(TAG)
    assert git.created() == []

def test_release_check_error_stops(git):
    # gh failed for another reason (for example, no internet)
    git.answers["gh release view " + TAG] = result("", "network error", 1)
    with pytest.raises(SystemExit, match="Could not check the release"):
        release.release(TAG)
    assert git.created() == []

def test_release_with_empty_description_stops(git, monkeypatch):
    type_in(monkeypatch, "")   # the first line is already empty
    with pytest.raises(SystemExit, match="cannot be empty"):
        release.release(TAG)
    assert git.created() == []

def test_release_cancelled_at_the_end_publishes_nothing(git, monkeypatch):
    type_in(monkeypatch, "Some notes", "", "n")
    with pytest.raises(SystemExit, match="Cancelled"):
        release.release(TAG)
    assert git.created() == []

def test_release_answer_other_than_y_cancels(git, monkeypatch):
    type_in(monkeypatch, "Some notes", "", "yes")   # only a plain "y" counts
    with pytest.raises(SystemExit, match="Cancelled"):
        release.release(TAG)
    assert git.created() == []

def test_release_create_failure_is_reported(git, monkeypatch):
    git.create_code = 1
    type_in(monkeypatch, "Some notes", "", "y")
    with pytest.raises(SystemExit, match="gh release create failed"):
        release.release(TAG)

# ---------- push ----------
def test_push_with_upstream_is_a_plain_push(git):
    release.push()
    assert ["git", "push"] in git.calls

def test_push_new_branch_sets_upstream(git):
    # no upstream yet, so the first push needs --set-upstream
    git.answers["git rev-parse --abbrev-ref @{u}"] = result("", "fatal", 128)
    git.answers["git branch --show-current"] = result("feature\n")
    release.push()
    assert ["git", "push", "--set-upstream", "origin", "feature"] in git.calls

def test_push_failure_stops(git):
    git.push_code = 1
    with pytest.raises(SystemExit, match="git push failed"):
        release.push()

# ---------- main ----------
@pytest.fixture
def main_env(monkeypatch):
    # main() changes folder and checks for git: fake both, and record the calls
    monkeypatch.setattr(release.os, "chdir", lambda path: None)
    monkeypatch.setattr(release.shutil, "which", lambda name: name)
    calls = []
    monkeypatch.setattr(release, "push", lambda: calls.append("push"))
    monkeypatch.setattr(release, "release", lambda tag: calls.append(("release", tag)))
    return calls

def test_main_answering_no_to_both_does_nothing(main_env, monkeypatch):
    type_in(monkeypatch, "n", "n")
    release.main()
    assert main_env == []

def test_main_can_push_without_releasing(main_env, monkeypatch):
    type_in(monkeypatch, "y", "n")
    release.main()
    assert main_env == ["push"]

def test_main_push_comes_before_the_release(main_env, monkeypatch):
    type_in(monkeypatch, "y", "y")
    release.main()
    assert main_env == ["push", ("release", release.APP_VERSION)]

def test_main_without_git_stops(main_env, monkeypatch):
    monkeypatch.setattr(release.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="Git is not available"):
        release.main()