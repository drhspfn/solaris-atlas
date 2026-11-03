"""Internal-only metadata discovery and bounded, independently read-only queries."""

import json
from typing import Any

import sqlglot
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlglot import exp

from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.base import Base

SAFE_FUNCTIONS = {
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "COALESCE",
    "NULLIF",
    "LOWER",
    "UPPER",
    "LENGTH",
    "SUBSTRING",
    "TRIM",
    "CAST",
    "TRY_CAST",
    "ROUND",
    "ABS",
    "CONCAT",
    "JSON_EXTRACT",
    "JSON_EXTRACT_SCALAR",
    "JSONB_EXTRACT",
    "ARRAY_SIZE",
    "DATE_TRUNC",
    "ROW_NUMBER",
    "RANK",
    "DENSE_RANK",
    "EXTRACT",
    "CURRENT_DATE",
    "CURRENT_TIMESTAMP",
}


def source_tables(settings: AgentSettings) -> dict[str, Any]:
    # Import the registry, not a hand-maintained list of models.
    from wuwa_story.db import models as _models  # noqa: F401

    schemas = set(settings.database_schemas.split(",")) - {
        "auth",
        "ops",
        "public",
        "pg_catalog",
        "information_schema",
    }
    return {key: table for key, table in Base.metadata.tables.items() if table.schema in schemas}


def read_schema(
    settings: AgentSettings,
    schema: str | None = None,
    table: str = "",
    offset: int = 0,
    limit: int = 20,
) -> dict:
    if offset < 0 or not 1 <= limit <= 30 or len(table) > 128:
        raise ValueError("Invalid schema pagination")
    tables = [
        value
        for key, value in sorted(source_tables(settings).items())
        if (not schema or value.schema == schema) and table.casefold() in key.casefold()
    ]
    return {
        "tables": [
            {
                "schema": item.schema,
                "name": item.name,
                "columns": [
                    {
                        "name": column.name,
                        "type": str(column.type),
                        "nullable": column.nullable,
                        "primary_key": column.primary_key,
                        "foreign_keys": sorted(fk.target_fullname for fk in column.foreign_keys),
                    }
                    for column in item.columns
                ],
            }
            for item in tables[offset : offset + limit]
        ],
        "next_offset": offset + limit if offset + limit < len(tables) else None,
        "sql_available": bool(settings.read_database_url.get_secret_value()),
    }


def validate_sql(sql: str, settings: AgentSettings) -> str:
    if not 1 <= len(sql) <= 12000:
        raise ValueError("SQL must contain 1..12000 characters")
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as error:
        raise ValueError("Invalid PostgreSQL SELECT") from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ValueError("Exactly one SELECT query is allowed")
    query = statements[0]
    forbidden = (exp.DDL, exp.DML, exp.Into, exp.Lock, exp.Command)
    if any(isinstance(node, forbidden) for node in query.walk()):
        raise ValueError("Mutations, SELECT INTO and locking queries are prohibited")
    ctes = {cte.alias_or_name for cte in query.find_all(exp.CTE)}
    tables = source_tables(settings)
    for item in query.find_all(exp.Table):
        if not item.db and item.name in ctes:
            continue
        if item.catalog or f"{item.db}.{item.name}" not in tables:
            raise ValueError("Use schema-qualified tables returned by read_schema")
    for function in query.find_all(exp.Func):
        name = function.name.upper() if isinstance(function, exp.Anonymous) else function.sql_name()
        if name not in SAFE_FUNCTIONS or isinstance(function.parent, exp.Dot):
            raise ValueError("Only approved pure SQL functions are allowed")
    return query.sql(dialect="postgres")


def bounded_rows(rows, max_chars: int = 16000) -> dict:
    output, size = [], 0
    for row in rows:
        row = dict(row)
        encoded = json.dumps(row, default=str, ensure_ascii=False)
        if size + len(encoded) > max_chars:
            return {"rows": output, "truncated": True}
        output.append(json.loads(encoded))
        size += len(encoded)
    return {"rows": output, "truncated": False}


async def query_database(settings: AgentSettings, sql: str, limit: int = 50) -> dict:
    sql = validate_sql(sql, settings)
    if not 1 <= limit <= 100:
        raise ValueError("Query limit must be 1..100")
    url = settings.read_database_url.get_secret_value()
    if not url:
        raise ValueError(
            "Configure AGENT_READ_DATABASE_URL with a restricted read-only database login"
        )
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout='5s'"))
            await connection.execute(text("SET LOCAL lock_timeout='1s'"))
            unsafe = await connection.scalar(
                text("""
                SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=current_user
                    AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls))
                OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname NOT IN ('pg_catalog','information_schema')
                    AND c.relkind IN ('r','p','v','m','f')
                    AND has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE'))
            """)
            )
            if unsafe:
                raise ValueError("SQL reader must not have privileged or write capabilities")
            result = await connection.execute(
                text(f"SELECT * FROM ({sql}) AS agent_read LIMIT :_agent_limit"),
                {"_agent_limit": limit + 1},
            )
            rows = result.mappings().all()
            response = bounded_rows(rows[:limit])
            response["truncated"] |= len(rows) > limit
            return response
    finally:
        await engine.dispose()


async def read_records(
    session,
    settings: AgentSettings,
    table: str,
    columns: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    source = source_tables(settings).get(table)
    if source is None or not 1 <= limit <= 100 or offset < 0:
        raise ValueError("Unknown source table or invalid pagination")
    columns = columns or [column.name for column in source.columns]
    if (
        len(columns) > 60
        or any(key not in source.c for key in columns)
        or any(key not in source.c for key in (filters or {}))
    ):
        raise ValueError("Unknown columns; inspect read_schema first")
    query = select(*(source.c[key] for key in columns))
    for key, value in (filters or {}).items():
        query = query.where(source.c[key] == value)
    query = query.order_by(*source.primary_key.columns).limit(limit + 1).offset(offset)
    rows = (await session.execute(query)).mappings().all()
    response = bounded_rows(rows[:limit])
    response["truncated"] |= len(rows) > limit
    response["next_offset"] = offset + limit if len(rows) > limit else None
    return response
