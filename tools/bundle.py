import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / "runtime" / "shrink-release.zip"
target.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
    for entry in root.rglob("*"):
        relative = entry.relative_to(root)
        if any(
            p
            in {
                ".venv",
                ".git",
                "node_modules",
                "__pycache__",
                ".pytest_cache",
                ".ruff_cache",
                "runtime",
                ".planning",
            }
            for p in relative.parts
        ):
            continue
        if entry.is_file():
            archive.write(entry, str(relative))
print(target)
