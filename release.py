import os
import subprocess
import sys
from pathlib import Path
from boxdlist import APP_VERSION

INSTALLER = Path("installer_output/BoxdListSetup.exe")

def main() -> None:
    os.chdir(Path(__file__).parent)   # always work from the project folder
    tag = APP_VERSION
    if not INSTALLER.is_file():
        sys.exit(f"Installer not found at {INSTALLER}")
    if INSTALLER.stat().st_mtime < Path("boxdlist.py").stat().st_mtime:
        sys.exit("The installer is older than boxdlist.py. Run build.py again.")
    result = subprocess.run(["git", "status", "--porcelain"],
            capture_output=True, text=True)
    if result.stdout.strip():
        sys.exit("Git working directory is not clean. Please commit or stash changes before releasing.")
    result = subprocess.run(["gh", "release", "view", tag],
            capture_output=True, text=True)
    if result.returncode == 0:
        sys.exit(f"Release {tag} already exists. Delete it first if you want to recreate it.")
    if "release not found" not in result.stderr.lower():
        sys.exit(f"Could not check the release: {result.stderr.strip()}")
    print(f"Ready to release BoxdList v{tag}.")
    print("Release description (one line per item, empty line to finish):")
    lines = []
    while True:
        line = input()
        if not line:          # an empty line ends the description
            break
        lines.append(line)
    description = "\n".join(lines)
    if not description:
        sys.exit("Release description cannot be empty.")
    title = f"BoxdList v{tag}"
    print(f"Creating release: \nTitle: '{title}'\nTag: {tag}\nDescription:\n{description}\nFile: {INSTALLER}")
    d = input("Push to GitHub and publish this release? (y/n): ").strip().lower()
    if d != "y":
        sys.exit("Release cancelled.")
    print("Pushing to GitHub...")
    result = subprocess.run(["git", "push"])
    if result.returncode != 0:
        sys.exit("git push failed. No release was created.")
    commit = subprocess.run(["git", "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()
    release = subprocess.run(["gh", "release", "create", tag,
                str(INSTALLER),             # file to attach
                "--title", title,
                "--notes", description,
                "--target", commit])        # the commit the tag points to
    if release.returncode != 0:
        sys.exit(f"gh release create failed with exit code {release.returncode}.")
    print(f"Release {tag} created successfully. You can view it at https://github.com/goncaloo07/BoxdList/releases/tag/{tag}")

if __name__ == "__main__":
    main()