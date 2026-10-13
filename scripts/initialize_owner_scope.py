import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import get_user_store
from owner_scope import initialize_owner_scope


def main():
    store = get_user_store()
    db = getattr(store, "db", None)
    if db is None:
        raise RuntimeError("Owner Scope initialization requires Firestore.")

    result = initialize_owner_scope(db)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
