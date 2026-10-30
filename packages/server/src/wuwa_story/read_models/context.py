"""Keep background source builders independent of the public projection readers."""

from contextlib import contextmanager
from contextvars import ContextVar

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.read_refresh import READ_PROJECTION_KINDS

SOURCE_BUILD = ContextVar("read_projection_source_build", default=False)


def should_route_to_projection(
    kind=None, snapshot=None, *, locale="en", locale_id=None, settings=None
):
    """Choose configured slices before lookup; a selected miss never uses source data.

    An iterable kind requires every contributing kind. Unspecified kind is the
    conservative all-kinds decision, not permission to bypass rollout selectors.
    """
    settings = settings if settings is not None else get_settings()
    if not settings.read_projections_enabled or SOURCE_BUILD.get():
        return False
    locales = getattr(settings, "read_projection_locales", ())
    if locales and (locale_id is not None or locale not in locales):
        return False
    snapshots = getattr(settings, "read_projection_snapshots", ())
    if snapshots and (snapshot or "current") not in snapshots:
        return False
    selected = getattr(settings, "read_projection_kinds", ())
    required = (kind,) if isinstance(kind, str) else kind
    required = READ_PROJECTION_KINDS if required is None else required
    return not selected or bool(required) and set(required) <= set(selected)


@contextmanager
def source_build():
    token = SOURCE_BUILD.set(True)
    try:
        yield
    finally:
        SOURCE_BUILD.reset(token)
