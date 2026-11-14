"""Shared visual identity types for validation, MCP schemas and admin choices."""

from enum import StrEnum


class VisualKind(StrEnum):
    PLAYABLE = "playable"
    STORY = "story"
    NPC = "npc"
    TECHNICAL = "technical"
    CHARACTER = "character"
    LOCATION = "location"
    ITEM = "item"
    CREATURE = "creature"
    OBJECT = "object"
    OTHER = "other"


KIND_LABELS = {
    VisualKind.PLAYABLE: "Playable character",
    VisualKind.STORY: "Story / event character",
    VisualKind.NPC: "NPC",
    VisualKind.TECHNICAL: "Technical role",
    VisualKind.CHARACTER: "Character",
    VisualKind.LOCATION: "Location",
    VisualKind.ITEM: "Item",
    VisualKind.CREATURE: "Creature",
    VisualKind.OBJECT: "Object",
    VisualKind.OTHER: "Other",
}
