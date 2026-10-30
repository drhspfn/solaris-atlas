"""Keep background source builders independent of the public projection readers."""

from contextlib import contextmanager
from contextvars import ContextVar

from wuwa_story.config.settings import get_settings

SOURCE_BUILD = ContextVar("read_projection_source_build", default=False)


def should_route_to_projection():
    return get_settings().read_projections_enabled and not SOURCE_BUILD.get()


@contextmanager
def source_build():
    token = SOURCE_BUILD.set(True)
    try:
        yield
    finally:
        SOURCE_BUILD.reset(token)
