import os
import shutil
import subprocess
import sys
from pathlib import Path
from boxdlist import APP_VERSION

INSTALLER = Path("installer_output/BoxdListSetup.exe")
REPO_URL = "https://github.com/goncaloo07/BoxdList"
RELEASE_BRANCH = "main"

def ask(question: str) -> bool:
    """Asks a yes/no question. Only 'y' counts as yes."""
    return input(f"{question} (y/n): ").strip().lower() == "y"

def run(command: list[str]) -> subprocess.CompletedProcess:
    """Runs a command and captures its output as text."""
    return subprocess.run(command, capture_output=True, text=True)

def push() -> None:
    """Pushes the current branch to GitHub."""
    print("Pushing to GitHub...")
    # a brand new branch has no upstream yet, so it needs --set-upstream
    has_upstream = run(["git", "rev-parse", "--abbrev-ref", "@{u}"]).returncode == 0
    command = ["git", "push"]
    if not has_upstream:
        branch = run(["git", "branch", "--show-current"]).stdout.strip()
        command = ["git", "push", "--set-upstream", "origin", branch]
    if subprocess.run(command).returncode != 0:
        sys.exit("git push failed.")
    print("Push done.")

def release(tag: str) -> None:
    """Checks everything, asks for a description and publishes the release."""
    title = f"BoxdList v{tag}"
    # ---------- Checks ----------
    if shutil.which("gh") is None:
        sys.exit("GitHub CLI (gh) is not available.")
    # the release must come from the main branch
    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    if branch != RELEASE_BRANCH:
        sys.exit(f"You are on '{branch}', not '{RELEASE_BRANCH}'. Switch to it first.")
    if not INSTALLER.is_file():
        sys.exit(f"Installer not found at {INSTALLER}")
    # the installer must be newer than the code, or build.py was not run
    if INSTALLER.stat().st_mtime < Path("boxdlist.py").stat().st_mtime:
        sys.exit("The installer is older than boxdlist.py. Run build.py again.")
    # empty output means everything is committed
    if run(["git", "status", "--porcelain"]).stdout.strip():
        sys.exit("There are uncommitted changes. Commit them before releasing.")
    # the commit must be online, so the tag can point to it
    unpushed = run(["git", "rev-list", "--count", "@{u}..HEAD"])
    if unpushed.returncode != 0 or unpushed.stdout.strip() != "0":
        sys.exit("There are commits that were not pushed. "
                 "Run this script again and answer y to the push question.")
    # exit code 0 means the release already exists
    existing = run(["gh", "release", "view", tag])
    if existing.returncode == 0:
        sys.exit(f"Release {tag} already exists. Delete it first to recreate it.")
    if "release not found" not in existing.stderr.lower():
        sys.exit(f"Could not check the release: {existing.stderr.strip()}")
    # ---------- Description ----------
    print("Release description (one line per item, empty line to finish):")
    lines = []
    while True:
        line = input()
        if not line:          # an empty line ends the description
            break
        lines.append(line)
    description = "\n".join(lines)
    if not description:
        sys.exit("The description cannot be empty.")
    # ---------- Final confirmation ----------
    print(f"\nTitle: {title}")
    print(f"Tag:   {tag}")
    print(f"File:  {INSTALLER}")
    print(f"Notes:\n{description}\n")
    if not ask("Publish this release?"):
        sys.exit("Cancelled. Nothing was published.")
    # ---------- Create the release ----------
    commit = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    print("Creating the release...")
    created = subprocess.run(["gh", "release", "create", tag,
                              str(INSTALLER),          # file to attach
                              "--title", title,
                              "--notes", description,
                              "--target", commit])     # commit the tag points to
    if created.returncode != 0:
        sys.exit(f"gh release create failed (code {created.returncode}).")
    print(f"Released {title}: {REPO_URL}/releases/tag/{tag}")

def main() -> None:
    os.chdir(Path(__file__).parent)
    if shutil.which("git") is None:
        sys.exit("Git is not available.")
    if ask("Push to GitHub?"):
        push()
    if ask(f"Create a GitHub release for v{APP_VERSION}?"):
        release(APP_VERSION)

if __name__ == "__main__":
    main()