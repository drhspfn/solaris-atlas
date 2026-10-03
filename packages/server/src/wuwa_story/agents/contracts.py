"""Validated model/tool output, not arbitrary URLs, SQL or graph mutations."""

from pydantic import BaseModel, ConfigDict, Field

PROMPT_VERSION = "story-v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(StrictModel):
    node_id: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=1500)


class ExplanationBlock(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=5000)
    kind: str = Field(default="explanation", pattern="^(summary|explanation|annotation)$")
    citations: list[Citation] = Field(min_length=1, max_length=12)
    related_node_ids: list[int] = Field(default_factory=list, max_length=12)


class InferredLink(StrictModel):
    from_node_id: int = Field(gt=0)
    to_node_id: int = Field(gt=0)
    relation: str = Field(min_length=1, max_length=64)
    explanation: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    citations: list[Citation] = Field(min_length=1, max_length=12)


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


class AnalysisRequest(StrictModel):
    quest_id: int = Field(gt=0)
    game_version: str = Field(min_length=1, max_length=64)
    locale: str = Field(default="en", min_length=2, max_length=16)


class NoteRequest(StrictModel):
    text: str = Field(min_length=1, max_length=3000)
    kind: str = Field(default="note", pattern="^(note|missing_data|contradiction|bug)$")
    citations: list[Citation] = Field(default_factory=list, max_length=12)


def validate_citations(result: AnalysisResult | NoteRequest, evidence: dict[int, str]) -> None:
    groups: list[NoteRequest | ExplanationBlock | InferredLink | InferredEvent] = (
        [result]
        if isinstance(result, NoteRequest)
        else [*result.blocks, *result.links, *result.events]
    )
    for group in groups:
        for citation in group.citations:
            source = evidence.get(citation.node_id)
            if source is None or citation.quote not in source:
                raise ValueError("Citation must quote evidence read in this run exactly")
