"""Provision and assume the fixed, passwordless source-reader role."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from wuwa_story.agents.settings import AgentSettings

READER_ROLE = "solaris_agent_readonly"
logger = logging.getLogger(__name__)


async def check_reader(connection: AsyncConnection) -> None:
    unsafe = await connection.scalar(text("""
        SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=current_user
            AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls))
        OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema')
            AND c.relkind IN ('r','p','v','m','f')
            AND has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE'))
    """))
    if unsafe:
        raise ValueError(
            "SQL reader must not have privileged or write capabilities")


async def provision_reader(
    connection: AsyncConnection, settings: AgentSettings, *, application_role: str | None = None
) -> None:
    """Idempotent; caller commits before opening the read-only transaction.

    A database owner with CREATEROLE can bootstrap it automatically. Restricted
    deployments run scripts/setup_agent_reader.py once using their migration login.
    Existing correct grants require no role-administration privilege at runtime.
    """
    from wuwa_story.agents.database import source_tables

    await connection.execute(text("SET LOCAL lock_timeout='5s'"))
    await connection.execute(text("SET LOCAL statement_timeout='10s'"))
    await connection.execute(text("SELECT pg_advisory_xact_lock(705,81)"))
    role = (await connection.execute(text("""
        SELECT oid, rolcanlogin, rolsuper OR rolcreatedb OR rolcreaterole OR
            rolreplication OR rolbypassrls AS elevated
        FROM pg_roles WHERE rolname=:role
    """), {"role": READER_ROLE})).mappings().one_or_none()
    if role is None:
        # Cluster-global CREATE ROLE can race with bootstrap in another database.
        await connection.execute(text(f"""
            DO $$ BEGIN
                CREATE ROLE {READER_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB
                    NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
            EXCEPTION WHEN duplicate_object THEN NULL; END $$
        """))
        logger.info("agent.reader_created role=%s", READER_ROLE)
        role = (await connection.execute(text("""
            SELECT oid, rolcanlogin, rolsuper OR rolcreatedb OR rolcreaterole OR
                rolreplication OR rolbypassrls AS elevated
            FROM pg_roles WHERE rolname=:role
        """), {"role": READER_ROLE})).mappings().one()
    if role["rolcanlogin"] or role["elevated"]:
        raise ValueError(
            "Managed reader role has unsafe attributes; repair its database grants")
    if await connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=:oid)"),
                               {"oid": role["oid"]}):
        raise ValueError("Managed reader must not be a member of other roles")
    quote = connection.dialect.identifier_preparer.quote
    # INHERIT FALSE keeps these grants explicit and transaction-local.
    login = application_role or await connection.scalar(text("SELECT session_user"))
    if await connection.scalar(text("""
        SELECT EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
            WHERE m.roleid=:oid AND r.rolname=:login AND m.inherit_option)
    """), {"oid": role["oid"], "login": login}):
        raise ValueError("Managed reader membership must use INHERIT FALSE")
    if not await connection.scalar(text("SELECT pg_has_role(:login,:role,'SET')"),
                                   {"login": login, "role": READER_ROLE}):
        await connection.execute(text(
            f"GRANT {READER_ROLE} TO {quote(login)} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"))
    schemas = sorted({str(table.schema)
                     for table in source_tables(settings).values()})
    for schema in schemas:
        if not await connection.scalar(text("SELECT has_schema_privilege(:role,:schema,'USAGE')"),
                                       {"role": READER_ROLE, "schema": schema}):
            await connection.execute(text(f"GRANT USAGE ON SCHEMA {quote(schema)} TO {READER_ROLE}"))
    missing = (await connection.execute(text("""
        SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY(CAST(:schemas AS text[])) AND c.relkind IN ('r','p','v','m','f')
            AND NOT has_table_privilege(:role,c.oid,'SELECT')
    """), {"schemas": schemas, "role": READER_ROLE})).all()
    for schema_name, table_name in missing:
        await connection.execute(text(
            f"GRANT SELECT ON TABLE {quote(str(schema_name))}.{quote(str(table_name))} TO {READER_ROLE}"))
    if missing:
        logger.info("agent.reader_grants_refreshed role=%s tables=%s",
                    READER_ROLE, len(missing))


@asynccontextmanager
async def reader_connection(
    settings: AgentSettings, bind: AsyncEngine | AsyncConnection | None = None
) -> AsyncIterator[AsyncConnection]:
    url = settings.read_database_url.get_secret_value()
    owned_engine = None
    if url:
        engine = owned_engine = create_async_engine(url, poolclass=NullPool)
    elif bind is not None:
        engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    else:
        from wuwa_story.db.session import engine
    try:
        if not url:
            async with engine.begin() as setup:
                await provision_reader(setup, settings)
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            if not url:
                await connection.execute(text(f"SET LOCAL ROLE {READER_ROLE}"))
            await connection.execute(text("SET LOCAL statement_timeout='5s'"))
            await connection.execute(text("SET LOCAL lock_timeout='1s'"))
            await check_reader(connection)
            yield connection
    finally:
        if owned_engine is not None:
            await owned_engine.dispose()
