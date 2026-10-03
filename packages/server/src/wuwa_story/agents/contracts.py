"""Validated model/tool output, not arbitrary URLs, SQL or graph mutations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROMPT_VERSION = "story-v3"

# Reading order is independent of the language used to write the explanation.
SOURCE_LOCALE_PRIORITY = ("en", "zh-Hans", "ja", "zh-Hant")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(StrictModel):
    node_id: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=1500)
    locale: str | None = Field(default=None, min_length=2, max_length=16)


class RelatedRecord(StrictModel):
    node_id: int = Field(gt=0)
    label: str = Field(min_length=1, max_length=300)


class QuestChronology(StrictModel):
    order: int = Field(
        ge=0, description="Authored encounter order, not world time or a single playthrough"
    )
    anchor_node_id: int = Field(
        gt=0, description="Read quest passage where the player encounters this claim"
    )
    label: str = Field(min_length=1, max_length=300)


class WorldChronology(StrictModel):
    placement: Literal["before_quest", "during_quest", "after_quest", "unknown"]
    explanation: str = Field(min_length=1, max_length=1000)


class LaterResolution(StrictModel):
    status: Literal["resolved", "partial", "contradicted"]
    text: str = Field(min_length=1, max_length=2000)
    revealed_in_node_id: int = Field(gt=0)
    citations: list[Citation] = Field(min_length=1, max_length=12)


class StoryAssertion(StrictModel):
    text: str = Field(min_length=1, max_length=2000, description="One atomic claim")
    status: Literal["confirmed", "observed_anomaly", "inferred", "unresolved"]
    citations: list[Citation] = Field(min_length=1, max_length=12)
    chronology_in_quest: QuestChronology
    world_chronology: WorldChronology
    knowledge_state: str = Field(
        min_length=1, max_length=2000, description="Only what the player knows at this encounter"
    )
    later_resolution: list[LaterResolution] = Field(
        default_factory=list,
        max_length=12,
        description="Separate sourced later revelations; empty means no established resolution",
    )


class ExplanationBlock(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=5000)
    kind: str = Field(default="explanation", pattern="^(summary|explanation|annotation)$")
    citations: list[Citation] = Field(min_length=1, max_length=12)
    related_node_ids: list[int] = Field(default_factory=list, max_length=12)
    related_records: list[RelatedRecord] = Field(default_factory=list, max_length=12)
    assertions: list[StoryAssertion] = Field(default_factory=list, max_length=30)

    def search_text(self) -> str:
        return "\n".join(
            [
                self.title,
                self.text,
                *(
                    "\n".join(
                        [
                            item.text,
                            item.knowledge_state,
                            item.world_chronology.explanation,
                            *(resolution.text for resolution in item.later_resolution),
                        ]
                    )
                    for item in self.assertions
                ),
            ]
        )


class InferredLink(StrictModel):
    from_node_id: int = Field(gt=0)
    to_node_id: int = Field(gt=0)
    relation: str = Field(min_length=1, max_length=64)
    explanation: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    citations: list[Citation] = Field(min_length=1, max_length=12)
    relation_label: str | None = Field(default=None, min_length=1, max_length=120)


class InferredEvent(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    citations: list[Citation] = Field(min_length=1, max_length=12)
    participant_node_ids: list[int] = Field(default_factory=list, max_length=20)


class AnalysisResult(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    blocks: list[ExplanationBlock] = Field(min_length=1, max_length=60)
    links: list[InferredLink] = Field(default_factory=list, max_length=60)
    events: list[InferredEvent] = Field(default_factory=list, max_length=20)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=30)

    def validate_temporal_structure(self) -> None:
        if any(not block.assertions for block in self.blocks):
            raise ValueError(
                "Every story-v3 block requires atomic assertions with chronology and knowledge state"
            )
        if any(not link.relation_label for link in self.links):
            raise ValueError("Every story-v3 link requires a human-readable relation_label")


class AnalysisRequest(StrictModel):
    quest_id: int = Field(gt=0)
    game_version: str = Field(min_length=1, max_length=64)
    locale: str = Field(default="en", min_length=2, max_length=16)
    generation: str = Field(default="", max_length=64)


class NoteRequest(StrictModel):
    text: str = Field(min_length=1, max_length=3000)
    kind: str = Field(default="note", pattern="^(note|missing_data|contradiction|bug)$")
    citations: list[Citation] = Field(default_factory=list, max_length=12)


def citation_groups(
    result: AnalysisResult | NoteRequest,
) -> list[
    NoteRequest | ExplanationBlock | InferredLink | InferredEvent | StoryAssertion | LaterResolution
]:
    groups: list[
        NoteRequest
        | ExplanationBlock
        | InferredLink
        | InferredEvent
        | StoryAssertion
        | LaterResolution
    ] = (
        [result]
        if isinstance(result, NoteRequest)
        else [*result.blocks, *result.links, *result.events]
    )
    if isinstance(result, AnalysisResult):
        for block in result.blocks:
            groups.extend(block.assertions)
            groups.extend(
                resolution
                for assertion in block.assertions
                for resolution in assertion.later_resolution
            )
    return groups


def validate_citations(result: AnalysisResult | NoteRequest, evidence: dict[int, str]) -> None:
    for group in citation_groups(result):
        for citation in group.citations:
            source = evidence.get(citation.node_id)
            if source is None or citation.quote not in source:
                raise ValueError("Citation must quote evidence read in this run exactly")
