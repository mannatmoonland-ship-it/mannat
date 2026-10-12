import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import get_user_store
from owner_scope_migration import migrate_owner_account


def main():
    parser = argparse.ArgumentParser(
        description="Migrate the legacy OWNERS ACCOUNT trade into Owner Scope."
    )
    parser.add_argument(
        "--baseline",
        required=True,
        type=Path,
        help="Path to the read-only Owner Account baseline JSON captured before migration.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the migration atomically. Without this flag, only preflight and verify.",
    )
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    store = get_user_store()
    db = getattr(store, "db", None)
    if db is None:
        raise RuntimeError("Owner Scope migration requires the Firestore Admin client.")

    result = migrate_owner_account(db, baseline, apply=args.apply)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
