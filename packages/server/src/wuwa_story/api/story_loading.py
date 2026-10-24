"""Compatibility imports for the story browsing batch loading service."""

from wuwa_story.story.domain import localization_payload, version_key
from wuwa_story.story.service import (
    prefetch_dialogue,
    prefetch_localizations,
    prefetch_node_labels,
    releases,
)

__all__ = ["localization_payload", "version_key", "prefetch_dialogue",
           "prefetch_localizations", "prefetch_node_labels", "releases"]
