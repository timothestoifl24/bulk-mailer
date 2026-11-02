"""Additive schema repair for databases created by an earlier version.

`Base.metadata.create_all` creates missing *tables* but never touches a table
that already exists, so a new column would silently be absent on an upgraded
deployment. This walks the models and issues `ALTER TABLE ... ADD COLUMN` for
anything missing.

Deliberately narrow: it only ever adds nullable-or-defaulted columns and the
indexes the models declare. It does not drop, rename, retype, or add other
constraints - a column added here carries no foreign key even when the model
declares one. That is the point at which this should be replaced by Alembic;
see the README.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import Engine
from sqlalchemy.schema import Column

from .db import Base

logger = logging.getLogger("mailer.migrations")


def _default_literal(column: Column, dialect) -> str | None:
    """SQL literal for a column's Python-side scalar default, if it has one."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def add_missing_columns(engine: Engine) -> list[str]:
    """Add columns present in the models but missing in the database."""
    inspector = inspect(engine)
    applied: list[str] = []

    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue  # create_all will have made it, or will next time
            existing = {col["name"] for col in inspector.get_columns(table.name)}

            for column in table.columns:
                if column.name in existing:
                    continue

                column_type = column.type.compile(engine.dialect)
                literal = _default_literal(column, engine.dialect)
                clause = f"{column.name} {column_type}"
                if literal is not None:
                    clause += f" DEFAULT {literal}"
                    # Existing rows need a value before NOT NULL can hold.
                    if not column.nullable:
                        clause += " NOT NULL"
                elif not column.nullable:
                    logger.warning(
                        "Cannot add %s.%s: it is NOT NULL with no default. "
                        "Migrate this one by hand.",
                        table.name,
                        column.name,
                    )
                    continue

                statement = f"ALTER TABLE {table.name} ADD COLUMN {clause}"
                connection.execute(text(statement))
                applied.append(f"{table.name}.{column.name}")
                logger.info("Schema updated: %s", statement)

    return applied


def add_missing_indexes(engine: Engine) -> list[str]:
    """Create indexes present in the models but missing in the database.

    A unique index that existing rows already violate cannot be created; that
    is logged and skipped, so startup goes on and the others still apply.
    """
    inspector = inspect(engine)
    applied: list[str] = []

    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {index["name"] for index in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name in existing:
                continue
            try:
                # One transaction each: on PostgreSQL a failed statement
                # would abort every index after it.
                with engine.begin() as connection:
                    index.create(connection)
            except DBAPIError as exc:
                logger.warning(
                    "Cannot create index %s on %s: %s. Resolve the conflicting rows by hand.",
                    index.name,
                    table.name,
                    exc.orig,
                )
                continue
            applied.append(index.name)
            logger.info("Schema updated: created index %s on %s", index.name, table.name)

    return applied
