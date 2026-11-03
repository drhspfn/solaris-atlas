"""Optional deployment bootstrap for a runtime login without CREATEROLE."""

import argparse
import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from wuwa_story.agents.reader import READER_ROLE, provision_reader
from wuwa_story.agents.settings import get_agent_settings
from wuwa_story.config.settings import get_settings


async def setup(application_role: str | None) -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.begin() as connection:
            await provision_reader(connection, get_agent_settings(), application_role=application_role)
        print(f"Ready: {READER_ROLE} (NOLOGIN; source SELECT only)")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--application-role", help="Runtime database login, if different from migration login")
    asyncio.run(setup(parser.parse_args().application_role))
