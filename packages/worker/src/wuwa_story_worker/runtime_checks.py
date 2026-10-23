"""Fail before claiming deliveries if the shared database runtime is unusable."""

from sqlalchemy import literal, select
from sqlalchemy.dialects.postgresql import JSONB


async def check_database_codecs(engine):
    expected = {"worker_startup": {"jsonb": True}, "values": [1, "two"]}
    async with engine.connect() as connection:
        actual = await connection.scalar(select(literal(expected, type_=JSONB)))
    if not isinstance(actual, dict) or actual != expected:
        raise RuntimeError("Worker database JSONB codecs are not initialized")
