"""Build a plugin archive from Git-visible source, excluding local deployment data."""

import subprocess
import zipfile
from pathlib import Path


def release_files(root: Path) -> list[Path]:
    # Include new source files before committing, but never ignored local files.
    output = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root
    )
    names = sorted(set(output.decode("utf-8").split("\0")) - {""})
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "-z", "--stdin"],
        input="\0".join(names).encode("utf-8") + b"\0", cwd=root, capture_output=True,
    )
    if ignored.returncode not in (0, 1):
        raise RuntimeError("Unable to verify release exclusions")
    excluded = set(ignored.stdout.decode("utf-8").split("\0"))
    root = root.resolve()
    files = []
    for name in names:
        path = root / name
        if name in excluded or not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Release file resolves outside source or is a symlink: {name}")
        files.append(path)
    return files


def build(root: Path, target: Path) -> int:
    root = root.resolve()
    files = release_files(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if path.resolve() != target.resolve():
                archive.write(path, path.relative_to(root).as_posix())
    return len(files)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    target = root / "runtime" / "shrink-release.zip"
    print(f"{target} ({build(root, target)} files)")
