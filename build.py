from boxdlist import APP_VERSION, is_newer, parse_version
import sys
import subprocess
import shutil
from pathlib import Path
import os
import re

# Fallback if ISCC is not found in the PATH
ISCC_PATH = r"C:\Program Files\Inno Setup 7\ISCC.exe"

def find_iscc() -> str | None:
    """Finds the Inno Setup compiler, or returns None."""
    found = shutil.which("ISCC")   # works if Inno Setup is in the PATH
    if found:
        return found
    if Path(ISCC_PATH).is_file():
        return ISCC_PATH
    return None

def main() -> None:
    os.chdir(Path(__file__).parent)
    # shows the current version as an example; Enter keeps it
    answer = input(f"Version to build (current: {APP_VERSION}, Enter to keep): ").strip().lstrip("v")
    version = answer or APP_VERSION
    try:
        parse_version(version)
    except ValueError as e:
        print(f"Invalid version: {e}", file=sys.stderr)
        sys.exit(1)
    if answer and not is_newer(version, APP_VERSION):
        print(f"Version {version} is not newer than current version {APP_VERSION}.", file=sys.stderr)
        sys.exit(1)
    iscc = find_iscc()
    if iscc is None:
        sys.exit("Inno Setup not found. Install it or change ISCC_PATH in build.py")
    print(f"Building BoxdList v{version}")
    run_step("Running tests", [sys.executable, "-m", "pytest"])
    if version != APP_VERSION:
        set_app_version(version)   # only after the tests pass
    pyinstaller_command = [
        sys.executable, "-m", "PyInstaller",   # same Python that runs the script
        "--windowed",
        "--name", "BoxdList",
        "--icon", "assets/boxdlist.ico",
        "--add-data", "assets/boxdlist.ico;assets",
        "--noconfirm", "--clean", "boxdlist.py",
    ]
    run_step("Building the app", pyinstaller_command)
    shutil.rmtree("build", ignore_errors=True)       # deletes the folder, no error if missing
    Path("BoxdList.spec").unlink(missing_ok=True)    # deletes the file, no error if missing
    iscc_command = [
        iscc,
        f"/DAppVersion={version}",   # sends the version to the .iss file
        "boxdlist.iss",
    ]
    run_step("Building the installer", iscc_command)

def run_step(name: str, command: list[str]) -> None:
    """Runs one build step and stops the whole build if it fails."""
    print(f"Running step: {name}")
    result = subprocess.run(command)
    if result.returncode != 0:
        print(f"Step '{name}' failed with exit code {result.returncode}. Stopping build.", file=sys.stderr)
        sys.exit(result.returncode)

def set_app_version(version: str, path: str = "boxdlist.py") -> None:
    """Writes the new version into the given file (boxdlist.py by default)."""
    with open(path, encoding="utf-8", newline="") as f:
        content = f.read()
    pattern = r'^APP_VERSION = "[^"]*"'
    new_content, count = re.subn(pattern, f'APP_VERSION = "{version}"', content, flags=re.MULTILINE)
    if count != 1:
        sys.exit(f"Could not find the APP_VERSION line in {path}")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new_content)

if __name__ == "__main__":
    main()