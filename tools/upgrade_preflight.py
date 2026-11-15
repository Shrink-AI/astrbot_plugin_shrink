"""Offline database upgrade preflight. Never imports AstrBot or invokes providers."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shrink.services.upgrades import upgrade_database  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("database must be an existing offline file")
    if args.database.resolve() == args.report.resolve():
        parser.error("report must not overwrite the database")
    try:
        report = upgrade_database(args.database)
    except Exception as exc:
        # Exceptions can contain user data. Only the error class belongs in reports.
        report = {**getattr(exc, "upgrade_report", {"status": "failed"}),
                  "error_type": type(exc).__name__}
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(report))
        return 1
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": report["status"], "report": str(args.report)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
