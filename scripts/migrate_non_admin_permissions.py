import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import get_user_store, invalidate_user_sessions, iso_now
from permission_defaults_migration import migrate_non_admin_permission_defaults


def main():
    parser = argparse.ArgumentParser(
        description="Set the approved shared permissions for all non-admin accounts."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the permission defaults. Without this flag, only report the changes.",
    )
    args = parser.parse_args()
    result = migrate_non_admin_permission_defaults(
        get_user_store(),
        apply=args.apply,
        invalidate_sessions=invalidate_user_sessions,
        timestamp=iso_now(),
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
