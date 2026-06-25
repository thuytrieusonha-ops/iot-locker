from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analytics_queries import run_query_benchmarks
from database import SessionLocal, init_db, is_database_configured


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run analytics benchmarks on MySQL/TiDB/TiFlash.")
    parser.add_argument("--days", type=int, default=30, help="Window of historical data to query.")
    parser.add_argument("--repeat", type=int, default=3, help="Number of times to run each query.")
    parser.add_argument("--use-tidb-mpp", action="store_true", help="Attempt to enable TiDB MPP mode in this session.")
    parser.add_argument("--enforce-mpp", action="store_true", help="Force TiDB to use MPP when possible.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON file to write benchmark results to.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not is_database_configured() or SessionLocal is None:
        raise RuntimeError("SMARTLOCKER_DATABASE_URL is not configured.")

    init_db()
    with SessionLocal() as session:
        results = run_query_benchmarks(
            session,
            days=args.days,
            repeat=args.repeat,
            use_tidb_mpp=args.use_tidb_mpp,
            enforce_mpp=args.enforce_mpp,
        )

    output = json.dumps(results, indent=2, ensure_ascii=False)
    print(output)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
