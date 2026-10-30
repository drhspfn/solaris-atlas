"""Snapshot selection supported by the public source records."""

from wuwa_story.db.models.read_refresh import HISTORICAL_READ_PROJECTION_KINDS

HISTORICAL_KINDS = frozenset(HISTORICAL_READ_PROJECTION_KINDS)
