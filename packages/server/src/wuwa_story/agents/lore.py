"""Server-owned depth and evidence policy; model suggestions are not authority."""

import re
from typing import Any

from wuwa_story.agents.contracts import AnalysisResult, Depth, QuestAssessment

PROFILES: dict[Depth, dict[str, int]] = {
    "very_short": {"words": 200, "blocks": 2, "research_steps": 4, "output_tokens": 4096},
    "short": {"words": 400, "blocks": 4, "research_steps": 8, "output_tokens": 8192},
    "medium": {"words": 1200, "blocks": 8, "research_steps": 16, "output_tokens": 16384},
    "full": {"words": 3000, "blocks": 16, "research_steps": 32, "output_tokens": 32000},
}
BASE_DEPTH: dict[str, Depth] = {
    "main_plot": "full",
    "character_arc": "full",
    "region_lore": "medium",
    "worldbuilding": "short",
    "side_hook": "medium",
    "side_flavor": "short",
    "tutorial_activity": "very_short",
    "service_repeatable": "very_short",
}
GENERIC_TERMS = frozenset(
    {"star", "dream", "hero", "light", "darkness", "memory", "time", "echo", "rover"}
)


def distinctive_term(value: str) -> bool:
    text = " ".join(value.casefold().split())
    return 3 <= len(text) <= 200 and text not in GENERIC_TERMS


def assessment_policy(assessment: QuestAssessment) -> dict[str, Any]:
    depth = BASE_DEPTH[assessment.narrative_weight]
    if assessment.hook_priority in ("high", "critical") and not assessment.signals:
        raise ValueError(
            "High/critical importance needs a cited substantive signal, not a namedrop"
        )
    if depth in ("medium", "full") and not assessment.signals:
        raise ValueError("Medium/full analysis needs a cited substantive lore signal")
    escalation = assessment.hook_priority in ("high", "critical") or any(
        s.kind in ("rover_anomaly", "regional_system", "time_memory") for s in assessment.signals
    )
    if escalation and depth in ("very_short", "short"):
        if not assessment.upgrade_reason:
            raise ValueError("Explain why this small quest requires medium analysis")
        depth = "medium"
    return {"depth": depth, **PROFILES[depth]}


def prose_words(value: Any, key: str = "") -> int:
    # Count visible prose, including redundant summaries, but not source quotations,
    # technical IDs, enums or the model's self-check booleans.
    if key in {"citations", "review", "related_node_ids", "participant_node_ids", "search_terms"}:
        return 0
    if isinstance(value, dict):
        return sum(prose_words(v, k) for k, v in value.items())
    if isinstance(value, list):
        return sum(prose_words(v, key) for v in value)
    if isinstance(value, str) and key in {
        "title",
        "text",
        "description",
        "explanation",
        "label",
        "knowledge_state",
        "question",
        "condition",
        "narrative_function",
        "known",
        "unknown",
        "cannot_conclude",
        "revisit_reason",
        "unresolved_questions",
        "relation_label",
    }:
        return len(re.findall(r"\b[\w'-]+\b", value))
    return 0


def validate_lore_result(result: AnalysisResult, assessment: QuestAssessment) -> None:
    policy = assessment_policy(assessment)
    result.validate_temporal_structure()
    if (
        not result.narrative_function
        or result.review is None
        or not all(result.review.model_dump().values())
    ):
        raise ValueError("Provide narrative_function and complete all seven self-review checks")
    if policy["depth"] in ("medium", "full") and result.knowledge_boundary is None:
        raise ValueError("Medium/full analysis requires known, unknown and cannot_conclude notes")
    if len(result.blocks) > policy["blocks"] or prose_words(result.model_dump()) > policy["words"]:
        raise ValueError(
            f"Analysis exceeds {policy['depth']} limit: {policy['words']} prose words / {policy['blocks']} blocks"
        )
    if len({hook.key for hook in result.hooks}) != len(result.hooks):
        raise ValueError("Hook keys must be unique and stable across reviews")
    for hook in result.hooks:
        if any(not distinctive_term(term) for term in hook.search_terms):
            raise ValueError("Hook searches need distinctive names/phrases, not generic terms")
        if hook.kind == "mundane_outcome" and hook.priority not in ("low", "flavor"):
            raise ValueError("An ordinary unreported outcome is a low-priority hook")
    for block in result.blocks:
        for assertion in block.assertions:
            if (
                assertion.occurrence in ("conditional", "optional", "player_choice")
                and not assertion.condition
            ):
                raise ValueError("Choices and conditional assertions must state their condition")
    for link in result.links:
        kinds = {signal.kind for signal in link.signals}
        if not kinds or any(not distinctive_term(signal.value) for signal in link.signals):
            raise ValueError("Graph links require distinctive sourced signals")
        if link.confidence >= 0.75 and "direct_reference" not in kinds and len(kinds) < 2:
            raise ValueError("Strong links need a direct reference or two independent signal types")
