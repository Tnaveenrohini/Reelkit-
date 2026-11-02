"""Create or update the bug fixer tables:  python -m bugfixer.migrate"""
import sys

from .config import load
from .db import DB, DBError

if __name__ == "__main__":
    cfg = load()
    try:
        DB(cfg.database_url).migrate()
    except DBError as e:
        print(f"migration failed: {e}", file=sys.stderr)
        sys.exit(1)
    print("bug fixer tables are ready")
