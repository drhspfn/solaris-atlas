"""Typed data models for the Wuthering Waves Fandom MediaWiki integration."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FandomEntityKind(StrEnum):
    CHARACTER = "character"
    NPC = "npc"
    SPEAKER = "speaker"
    QUEST = "quest"
    ITEM = "item"
    LOCATION = "location"
    EVENT = "event"


class LicenseFilter(StrEnum):
    """Controls whether Fandom assets are filtered by CC-BY-SA compatibility."""

    CC_BY_SA_ONLY = "cc_by_sa_only"
    IGNORE = "ignore"


class FandomLicenseInfo(BaseModel):
    text: str | None = None
    url: str | None = None
    is_cc_by_sa: bool = False
    file_templates: list[str] = Field(default_factory=list)
    non_free_detected: bool = False


class FandomSearchResult(BaseModel):
    page_id: int
    title: str
    snippet: str = ""
    url: str


class FandomImageAsset(BaseModel):
    title: str
    filename: str
    url: str
    description_url: str
    width: int | None = None
    height: int | None = None
    size: int | None = None
    mime: str | None = None
    source_page: str
    caption: str | None = None
    license: FandomLicenseInfo


class FandomRelationHint(BaseModel):
    relation_type: str
    target_name: str
    note: str | None = None
    source_field: str


class FandomInfobox(BaseModel):
    template: str
    fields: dict[str, str] = Field(default_factory=dict)
    raw_fields: dict[str, str] = Field(default_factory=dict)


class FandomParsedPage(BaseModel):
    title: str
    display_title: str | None = None
    url: str
    wikitext: str = ""
    images: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)


class FandomEntityBundle(BaseModel):
    entity_kind: FandomEntityKind
    query_name: str
    page_title: str
    page_url: str
    gallery_url: str | None = None
    license: FandomLicenseInfo
    infobox: FandomInfobox | None = None
    summary: str | None = None
    categories: list[str] = Field(default_factory=list)
    relations: list[FandomRelationHint] = Field(default_factory=list)
    images: list[FandomImageAsset] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
