"""Validated model/tool output, not arbitrary URLs, SQL or graph mutations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROMPT_VERSION = "story-v5"

# Reading order is independent of the language used to write the explanation.
SOURCE_LOCALE_PRIORITY = ("en", "zh-Hans", "ja", "zh-Hant")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(StrictModel):
    node_id: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=1500)
    locale: str | None = Field(default=None, min_length=2, max_length=16)
    snapshot_id: int | None = Field(default=None, gt=0)


Priority = Literal["critical", "high", "medium", "low", "flavor"]
Depth = Literal["very_short", "short", "medium", "full"]


class LoreSignal(StrictModel):
    kind: Literal[
        "rover_anomaly",
        "regional_system",
        "time_memory",
        "historical_event",
        "character_change",
        "unique_lore",
        "direct_reference",
    ]
    explanation: str = Field(min_length=1, max_length=500)
    citations: list[Citation] = Field(min_length=1, max_length=3)


class QuestAssessment(StrictModel):
    narrative_weight: Literal[
        "main_plot",
        "character_arc",
        "region_lore",
        "worldbuilding",
        "side_hook",
        "side_flavor",
        "tutorial_activity",
        "service_repeatable",
    ]
    hook_priority: Priority
    reason: str = Field(min_length=1, max_length=600)
    signals: list[LoreSignal] = Field(default_factory=list, max_length=8)
    citations: list[Citation] = Field(min_length=1, max_length=4)
    upgrade_reason: str | None = Field(default=None, min_length=1, max_length=600)


class OpenHook(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    question: str = Field(min_length=10, max_length=500)
    priority: Priority
    kind: Literal["mystery", "mundane_outcome", "character", "regional_system", "time_memory"]
    revisit_on_new_versions: bool
    revisit_reason: str = Field(min_length=1, max_length=500)
    search_terms: list[str] = Field(min_length=1, max_length=6)
    citations: list[Citation] = Field(min_length=1, max_length=4)


class LinkSignal(StrictModel):
    kind: Literal[
        "direct_reference",
        "named_character",
        "named_term",
        "location",
        "unique_item",
        "specific_phrase",
        "anomaly_mechanism",
    ]
    value: str = Field(min_length=2, max_length=200)
    citations: list[Citation] = Field(min_length=1, max_length=4)


class AnalysisReview(StrictModel):
    choices_labeled: bool
    future_knowledge_separated: bool
    proportional_depth: bool
    unresolved_preserved: bool
    speculation_labeled: bool
    revisit_checked: bool
    branches_separated: bool


class KnowledgeBoundary(StrictModel):
    known: list[str] = Field(min_length=1, max_length=8)
    unknown: list[str] = Field(min_length=1, max_length=8)
    cannot_conclude: list[str] = Field(min_length=1, max_length=8)


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
    status: Literal["resolved", "partial", "contradicted", "suggested"]
    text: str = Field(min_length=1, max_length=2000)
    revealed_in_node_id: int = Field(gt=0)
    citations: list[Citation] = Field(min_length=1, max_length=12)


class StoryAssertion(StrictModel):
    text: str = Field(min_length=1, max_length=2000, description="One atomic claim")
    status: Literal[
        "confirmed",
        "observed_anomaly",
        "inferred",
        "unresolved",
        "suggested",
        "character_speculation",
        "theory",
    ]
    occurrence: Literal["mandatory", "player_choice", "conditional", "optional", "unknown"] = (
        "unknown"
    )
    condition: str | None = Field(default=None, min_length=1, max_length=500)
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
    scene_importance: Priority = "medium"

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
    certainty: Literal["confirmed", "suggested", "inferred", "theory"] = "inferred"
    signals: list[LinkSignal] = Field(default_factory=list, max_length=6)


class InferredEvent(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    citations: list[Citation] = Field(min_length=1, max_length=12)
    participant_node_ids: list[int] = Field(default_factory=list, max_length=20)


class HookReview(StrictModel):
    hook_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    status: Literal[
        "resolved", "partial", "suggested", "contradicted", "unresolved_in_loaded_corpus"
    ]
    explanation: str = Field(min_length=1, max_length=1000)
    citations: list[Citation] = Field(min_length=1, max_length=8)


class AnalysisResult(StrictModel):
    revisited_hooks: list[HookReview] = Field(default_factory=list, max_length=12)
    assessment: QuestAssessment | None = None
    title: str = Field(min_length=1, max_length=200)
    blocks: list[ExplanationBlock] = Field(min_length=1, max_length=60)
    links: list[InferredLink] = Field(default_factory=list, max_length=60)
    events: list[InferredEvent] = Field(default_factory=list, max_length=20)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=30)
    narrative_function: str | None = Field(default=None, min_length=1, max_length=1000)
    knowledge_boundary: KnowledgeBoundary | None = None
    hooks: list[OpenHook] = Field(default_factory=list, max_length=12)
    review: AnalysisReview | None = None

    def validate_temporal_structure(self) -> None:
        if any(not block.assertions for block in self.blocks):
            raise ValueError(
                "Every story-v3 block requires atomic assertions with chronology and knowledge state"
            )
        if any(not link.relation_label for link in self.links):
            raise ValueError("Every story-v3 link requires a human-readable relation_label")


class AnalysisRequest(StrictModel):
    quest_id: int = Field(gt=0)
    game_version: str | None = Field(default=None, min_length=1, max_length=64)
    locale: str = Field(default="en", min_length=2, max_length=16)
    generation: str = Field(default="", max_length=64)


class NoteRequest(StrictModel):
    text: str = Field(min_length=1, max_length=3000)
    kind: str = Field(default="note", pattern="^(note|missing_data|contradiction|bug)$")
    citations: list[Citation] = Field(default_factory=list, max_length=12)


def citation_groups(
    result: AnalysisResult | NoteRequest,
) -> list[
    NoteRequest
    | ExplanationBlock
    | InferredLink
    | InferredEvent
    | StoryAssertion
    | LaterResolution
    | OpenHook
    | LinkSignal
    | QuestAssessment
    | LoreSignal
    | HookReview
]:
    groups: list[
        NoteRequest
        | ExplanationBlock
        | InferredLink
        | InferredEvent
        | StoryAssertion
        | LaterResolution
        | OpenHook
        | LinkSignal
        | QuestAssessment
        | LoreSignal
        | HookReview
    ] = (
        [result]
        if isinstance(result, NoteRequest)
        else [*result.blocks, *result.links, *result.events]
    )
    if isinstance(result, AnalysisResult):
        if result.assessment:
            groups.extend([result.assessment, *result.assessment.signals])
        groups.extend(result.hooks)
        groups.extend(result.revisited_hooks)
        groups.extend(signal for link in result.links for signal in link.signals)
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
