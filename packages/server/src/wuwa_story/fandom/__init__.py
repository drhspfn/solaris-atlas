"""Typed Wuthering Waves Fandom integration (pages, galleries, infobox relations, licensing)."""

from wuwa_story.fandom.client import FandomClient, wiki_url
from wuwa_story.fandom.models import (
    FandomEntityBundle,
    FandomEntityKind,
    FandomImageAsset,
    FandomInfobox,
    FandomLicenseInfo,
    FandomParsedPage,
    FandomRelationHint,
    FandomSearchResult,
    LicenseFilter,
)
from wuwa_story.fandom.parser import (
    extract_gallery_entries,
    extract_infobox,
    extract_relations,
    is_decorative_wiki_asset,
    strip_wikitext,
)
from wuwa_story.fandom.sync import sync_fandom_bundle

__all__ = [
    "FandomClient",
    "FandomEntityBundle",
    "FandomEntityKind",
    "FandomImageAsset",
    "FandomInfobox",
    "FandomLicenseInfo",
    "FandomParsedPage",
    "FandomRelationHint",
    "FandomSearchResult",
    "LicenseFilter",
    "extract_gallery_entries",
    "extract_infobox",
    "extract_relations",
    "is_decorative_wiki_asset",
    "strip_wikitext",
    "sync_fandom_bundle",
    "wiki_url",
]
