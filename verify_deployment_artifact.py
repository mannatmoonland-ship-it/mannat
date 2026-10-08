"""Reject local-only files and directories from a staged cloud deployment artifact."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


EXCLUDED_DIRECTORY_NAMES = frozenset({
    ".git",
    ".pytest_cache",
    ".venv",
    ".devin",
    ".qodo",
    "$base",
    "__pycache__",
    "old code",
    "backups",
    "build",
    "dist",
    "google_photos_image_test",
    "instance",
    "node_modules",
    "ledgerpro_flask_accounting",
})
EXCLUDED_FILE_PATTERNS = (
    re.compile(r"^\.env(?:\..+)?$", re.IGNORECASE),
    re.compile(r".*service.?account.*\.json$", re.IGNORECASE),
    re.compile(r".*firebase-adminsdk.*\.json$", re.IGNORECASE),
    re.compile(r".*(?:credential|credentials|private[-_]key).*\.(?:json|pem|key|p12|pfx)$", re.IGNORECASE),
    re.compile(r".*\.(?:pem|key|p12|pfx)$", re.IGNORECASE),
    re.compile(r".*\.(?:log|db|sqlite|sqlite3)$", re.IGNORECASE),
    re.compile(r"^secret(?:s)?(?:\..*)?$", re.IGNORECASE),
    re.compile(r".*\.(?:tmp|temp|bak|old|pyc|pyo)$", re.IGNORECASE),
)


def find_excluded_artifact_paths(root: Path) -> list[str]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("The deployment artifact path must be a directory.")

    excluded: list[str] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            path = current_path / name
            if (
                name.lower() in EXCLUDED_DIRECTORY_NAMES
                or name.lower().startswith(".venv")
                or path.is_symlink()
            ):
                excluded.append(path.relative_to(root).as_posix())
                directories.remove(name)
        for name in filenames:
            path = current_path / name
            if path.is_symlink() or any(pattern.fullmatch(name) for pattern in EXCLUDED_FILE_PATTERNS):
                excluded.append(path.relative_to(root).as_posix())
    return sorted(excluded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_directory", type=Path)
    args = parser.parse_args()

    try:
        excluded = find_excluded_artifact_paths(args.artifact_directory)
    except (OSError, ValueError) as error:
        print(f"Artifact verification failed: {error}", file=sys.stderr)
        return 2
    if excluded:
        print("Deployment artifact contains excluded local-only paths:", file=sys.stderr)
        for path in excluded:
            print(f"- {path}", file=sys.stderr)
        return 1
    print("Deployment artifact contains no detected local-only secret or persistence paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
