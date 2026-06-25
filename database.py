from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import build_database_url, env_int


class Base(DeclarativeBase):
    pass


def load_dotenv_file(filename: str = ".env") -> None:
    dotenv_path = Path(__file__).resolve().parent / filename
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue

        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
            cleaned = cleaned[1:-1]
        os.environ[key] = cleaned


load_dotenv_file()

DATABASE_URL = build_database_url()

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=Session,
    expire_on_commit=False,
) if engine is not None else None


def is_database_configured() -> bool:
    return engine is not None and SessionLocal is not None


def init_db() -> None:
    if not is_database_configured():
        return

    with schema_migration_lock():
        rename_legacy_user_table()

        from model import Base as ModelBase

        ModelBase.metadata.create_all(bind=engine)
        ensure_schema_updates()


@contextmanager
def schema_migration_lock() -> Iterator[None]:
    if engine is None or engine.dialect.name != "mysql":
        yield
        return

    with engine.connect() as connection:
        acquired = connection.execute(text("SELECT GET_LOCK('smartlocker_schema_migration', 30)")).scalar()
        if acquired != 1:
            print("[smartlocker] Schema warning: could not acquire migration lock within 30 seconds.")
            yield
            return

        try:
            yield
        finally:
            connection.execute(text("SELECT RELEASE_LOCK('smartlocker_schema_migration')"))


def default_locker_count() -> int:
    return max(1, env_int("SMARTLOCKER_LOCKER_COUNT", 8))


def rename_legacy_user_table() -> None:
    if engine is None:
        return

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "user_accounts" not in tables or "users" in tables:
        return

    statement = "RENAME TABLE user_accounts TO users"
    if engine.dialect.name != "mysql":
        statement = "ALTER TABLE user_accounts RENAME TO users"

    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    except SQLAlchemyError as exc:
        print(f"[smartlocker] Schema warning: could not rename user_accounts to users: {exc}")


def table_exists(table_name: str) -> bool:
    if engine is None:
        return False
    return table_name in inspect(engine).get_table_names()


def column_names(table_name: str) -> set[str]:
    if engine is None or not table_exists(table_name):
        return set()
    return {column["name"] for column in inspect(engine).get_columns(table_name)}


def index_names(table_name: str) -> set[str]:
    if engine is None or not table_exists(table_name):
        return set()
    return {index["name"] for index in inspect(engine).get_indexes(table_name)}


def foreign_key_names(table_name: str) -> set[str]:
    if engine is None or not table_exists(table_name):
        return set()
    return {foreign_key["name"] for foreign_key in inspect(engine).get_foreign_keys(table_name)}


def execute_schema_statement(statement: str) -> None:
    if engine is None:
        return
    with engine.begin() as connection:
        connection.execute(text(statement))


def add_column_if_missing(table_name: str, column_name: str, definition: str) -> None:
    if column_name in column_names(table_name):
        return
    try:
        execute_schema_statement(f"ALTER TABLE {table_name} ADD COLUMN {definition}")
    except SQLAlchemyError as exc:
        print(f"[smartlocker] Schema warning: could not add {table_name}.{column_name}: {exc}")


def add_index_if_missing(table_name: str, index_name: str, columns: str) -> None:
    if index_name in index_names(table_name):
        return
    try:
        execute_schema_statement(f"CREATE INDEX {index_name} ON {table_name} ({columns})")
    except SQLAlchemyError as exc:
        print(f"[smartlocker] Schema warning: could not add {index_name}: {exc}")


def add_foreign_key_if_missing(table_name: str, constraint_name: str, definition: str) -> None:
    if constraint_name in foreign_key_names(table_name):
        return
    try:
        execute_schema_statement(f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} {definition}")
    except SQLAlchemyError as exc:
        print(f"[smartlocker] Schema warning: could not add {constraint_name}: {exc}")


def ensure_users_table_compatibility() -> None:
    if engine is None or not table_exists("users"):
        return

    if engine.dialect.name == "mysql":
        execute_schema_statement("ALTER TABLE users MODIFY COLUMN email VARCHAR(255) NULL")

    if not table_exists("user_accounts"):
        return

    if engine.dialect.name == "mysql":
        execute_schema_statement(
            """
            INSERT INTO users (phone, email, created_at, updated_at)
            SELECT phone, email, created_at, updated_at
            FROM user_accounts
            ON DUPLICATE KEY UPDATE
                email = IF(users.email IS NULL, VALUES(email), users.email),
                updated_at = GREATEST(users.updated_at, VALUES(updated_at))
            """
        )


def seed_default_lockers() -> None:
    if engine is None or not table_exists("locker_sites") or not table_exists("lockers"):
        return

    with engine.begin() as connection:
        if engine.dialect.name == "mysql":
            connection.execute(
                text(
                    """
                    INSERT INTO locker_sites (id, code, name, created_at, updated_at)
                    VALUES (1, 'default', 'Default Locker Site', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON DUPLICATE KEY UPDATE
                        name = VALUES(name),
                        updated_at = VALUES(updated_at)
                    """
                )
            )
        else:
            connection.execute(
                text(
                    """
                    INSERT OR IGNORE INTO locker_sites (id, code, name, created_at, updated_at)
                    VALUES (1, 'default', 'Default Locker Site', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """
                )
            )

        locker_ids = set(range(1, default_locker_count() + 1))
        for table_name, column_name in (
            ("locker_orders", "locker_id"),
            ("locker_access_tokens", "locker_id"),
            ("admin_command_lockers", "locker_id"),
        ):
            if not table_exists(table_name) or column_name not in column_names(table_name):
                continue
            values = connection.execute(
                text(f"SELECT DISTINCT {column_name} FROM {table_name} WHERE {column_name} IS NOT NULL")
            ).scalars()
            locker_ids.update(int(value) for value in values if int(value) > 0)

        for locker_id in sorted(locker_ids):
            params = {
                "id": locker_id,
                "locker_number": locker_id,
                "code": f"default-{locker_id:04d}",
                "display_name": f"Tu {locker_id}",
            }
            if engine.dialect.name == "mysql":
                connection.execute(
                    text(
                        """
                        INSERT INTO lockers (
                            id, site_id, locker_number, code, display_name, status, created_at, updated_at
                        )
                        VALUES (
                            :id, 1, :locker_number, :code, :display_name, 'active',
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        ON DUPLICATE KEY UPDATE
                            site_id = VALUES(site_id),
                            locker_number = VALUES(locker_number),
                            display_name = VALUES(display_name),
                            status = VALUES(status),
                            updated_at = VALUES(updated_at)
                        """
                    ),
                    params,
                )
            else:
                connection.execute(
                    text(
                        """
                        INSERT OR IGNORE INTO lockers (
                            id, site_id, locker_number, code, display_name, status, created_at, updated_at
                        )
                        VALUES (
                            :id, 1, :locker_number, :code, :display_name, 'active',
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    params,
                )


def backfill_user_relationships() -> None:
    if engine is None or not table_exists("users") or not table_exists("locker_orders"):
        return

    if engine.dialect.name == "mysql":
        execute_schema_statement(
            """
            INSERT IGNORE INTO users (phone, created_at, updated_at)
            SELECT locker_orders.phone, MIN(locker_orders.created_at), CURRENT_TIMESTAMP
            FROM locker_orders
            LEFT JOIN users ON users.phone = locker_orders.phone
            WHERE users.id IS NULL
                AND locker_orders.phone IS NOT NULL
                AND locker_orders.phone <> ''
            GROUP BY locker_orders.phone
            """
        )
        if "user_id" in column_names("locker_orders"):
            execute_schema_statement(
                """
                UPDATE locker_orders
                JOIN users ON users.phone = locker_orders.phone
                SET locker_orders.user_id = users.id
                WHERE locker_orders.user_id IS NULL
                """
            )
        return

    with engine.begin() as connection:
        phones = connection.execute(
            text(
                """
                SELECT locker_orders.phone, MIN(locker_orders.created_at) AS created_at
                FROM locker_orders
                LEFT JOIN users ON users.phone = locker_orders.phone
                WHERE users.id IS NULL
                    AND locker_orders.phone IS NOT NULL
                    AND locker_orders.phone <> ''
                GROUP BY locker_orders.phone
                """
            )
        ).mappings()
        for row in phones:
            connection.execute(
                text(
                    """
                    INSERT INTO users (phone, created_at, updated_at)
                    VALUES (:phone, :created_at, CURRENT_TIMESTAMP)
                    """
                ),
                {"phone": row["phone"], "created_at": row["created_at"]},
            )
        if "user_id" in column_names("locker_orders"):
            connection.execute(
                text(
                    """
                    UPDATE locker_orders
                    SET user_id = (
                        SELECT users.id FROM users WHERE users.phone = locker_orders.phone
                    )
                    WHERE user_id IS NULL
                    """
                )
            )


def ensure_schema_updates() -> None:
    if engine is None:
        return

    ensure_users_table_compatibility()
    seed_default_lockers()

    if table_exists("locker_orders"):
        add_column_if_missing("locker_orders", "user_id", "user_id INT NULL")
        add_column_if_missing("locker_orders", "recipient_email", "recipient_email VARCHAR(255) NULL")
        add_column_if_missing("locker_orders", "email_delivery_status", "email_delivery_status VARCHAR(20) NULL")
        add_column_if_missing("locker_orders", "email_delivery_note", "email_delivery_note VARCHAR(255) NULL")
        add_column_if_missing("locker_orders", "email_link_base_url", "email_link_base_url VARCHAR(255) NULL")
        add_column_if_missing("locker_orders", "email_sent_at", "email_sent_at DATETIME NULL")
        add_index_if_missing("locker_orders", "ix_locker_orders_user_id", "user_id")
        add_index_if_missing("locker_orders", "ix_locker_orders_user_created_at", "user_id, created_at")
        add_index_if_missing("locker_orders", "ix_locker_orders_locker_status", "locker_id, status")
        add_index_if_missing("locker_orders", "ix_locker_orders_phone_created_at", "phone, created_at")

    seed_default_lockers()
    backfill_user_relationships()

    if table_exists("locker_orders"):
        add_foreign_key_if_missing(
            "locker_orders",
            "fk_locker_orders_user",
            "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL ON UPDATE CASCADE",
        )
        add_foreign_key_if_missing(
            "locker_orders",
            "fk_locker_orders_locker",
            "FOREIGN KEY (locker_id) REFERENCES lockers(id) ON DELETE RESTRICT ON UPDATE CASCADE",
        )

    if table_exists("locker_access_tokens"):
        add_index_if_missing("locker_access_tokens", "ix_locker_access_tokens_order_status", "order_id, status")
        add_foreign_key_if_missing(
            "locker_access_tokens",
            "fk_locker_access_tokens_order",
            "FOREIGN KEY (order_id) REFERENCES locker_orders(id) ON DELETE CASCADE ON UPDATE CASCADE",
        )
        add_foreign_key_if_missing(
            "locker_access_tokens",
            "fk_locker_access_tokens_locker",
            "FOREIGN KEY (locker_id) REFERENCES lockers(id) ON DELETE RESTRICT ON UPDATE CASCADE",
        )

    if table_exists("admin_commands"):
        add_index_if_missing("admin_commands", "ix_admin_commands_status_created_at", "status, created_at")


def get_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        raise RuntimeError("SMARTLOCKER_DATABASE_URL is not configured.")

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
