from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from config import build_database_url, env_str
from database import load_dotenv_file


def main() -> None:
    load_dotenv_file()
    database_url = build_database_url()
    if not database_url:
        raise RuntimeError("SMARTLOCKER_DATABASE_URL or SMARTLOCKER_DATABASE_HOST/NAME/USER is not configured.")

    url = make_url(database_url)
    database_name = url.database or env_str("SMARTLOCKER_DATABASE_NAME")
    if not database_name:
        raise RuntimeError("No target database name was found in the current configuration.")

    admin_url = url.set(database=None)
    engine = create_engine(admin_url, pool_pre_ping=True)

    with engine.begin() as connection:
        connection.execute(text(f"CREATE DATABASE IF NOT EXISTS `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))

    print(f"Prepared benchmark database: {database_name}")
    print(f"Connection target: {admin_url.render_as_string(hide_password=True)}")


if __name__ == "__main__":
    main()
