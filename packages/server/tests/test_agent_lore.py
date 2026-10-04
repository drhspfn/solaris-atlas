import pytest

from wuwa_story.agents.contracts import AnalysisResult, QuestAssessment
from wuwa_story.agents.lore import (
    assessment_policy,
    distinctive_term,
    prose_words,
    validate_lore_result,
)


def assessment(**changes):
    return QuestAssessment.model_validate(
        {
            "narrative_weight": "tutorial_activity",
            "hook_priority": "low",
            "reason": "A traversal lesson",
            "citations": [{"node_id": 1, "quote": "Jump here"}],
            **changes,
        }
    )


def test_small_quests_remain_small_and_cited_anomalies_upgrade():
    assert assessment_policy(assessment())["depth"] == "very_short"
    with pytest.raises(ValueError, match="cited"):
        assessment_policy(assessment(hook_priority="critical"))
    signal = {
        "kind": "time_memory",
        "explanation": "Two contradictory memories",
        "citations": [{"node_id": 2, "quote": "I remember it differently"}],
    }
    with pytest.raises(ValueError, match="Explain"):
        assessment_policy(assessment(signals=[signal]))
    upgraded = assessment(
        signals=[signal], upgrade_reason="The memory contradiction is substantive"
    )
    assert assessment_policy(upgraded)["depth"] == "medium"
    with pytest.raises(ValueError, match="cited"):
        assessment_policy(assessment(narrative_weight="main_plot"))


def test_word_budget_excludes_quotes_but_includes_duplicate_visible_prose():
    assert (
        prose_words(
            {
                "text": "one two",
                "citations": [{"quote": "many " * 1000}],
                "assertions": [{"text": "one two", "knowledge_state": "three four"}],
            }
        )
        == 6
    )


def test_generic_terms_are_not_distinctive_signals():
    for term in ["star", " DREAM ", "Rover", "time", "a", "x" * 201]:
        assert not distinctive_term(term)
    assert distinctive_term("The Seven Swords of Qingren")


def minimal_result():
    citation = {"node_id": 1, "quote": "Jump here"}
    return AnalysisResult.model_validate(
        {
            "title": "Traversal lesson",
            "narrative_function": "Teaches traversal.",
            "review": dict.fromkeys(
                [
                    "choices_labeled",
                    "future_knowledge_separated",
                    "proportional_depth",
                    "unresolved_preserved",
                    "speculation_labeled",
                    "revisit_checked",
                    "branches_separated",
                ],
                True,
            ),
            "blocks": [
                {
                    "title": "Lesson",
                    "text": "A movement tutorial.",
                    "citations": [citation],
                    "assertions": [
                        {
                            "text": "The instructor invites a jump.",
                            "status": "confirmed",
                            "occurrence": "mandatory",
                            "citations": [citation],
                            "chronology_in_quest": {
                                "order": 0,
                                "anchor_node_id": 1,
                                "label": "Lesson start",
                            },
                            "world_chronology": {
                                "placement": "during_quest",
                                "explanation": "The current lesson.",
                            },
                            "knowledge_state": "A jumping challenge is available.",
                        }
                    ],
                }
            ],
        }
    )


def test_publication_requires_proportional_reviewed_branch_safe_output():
    result = minimal_result()
    validate_lore_result(result, assessment())
    result.blocks[0].text = "filler " * 201
    with pytest.raises(ValueError, match="exceeds"):
        validate_lore_result(result, assessment())
    result = minimal_result()
    result.blocks[0].assertions[0].occurrence = "player_choice"
    with pytest.raises(ValueError, match="condition"):
        validate_lore_result(result, assessment())
    result.blocks[0].assertions[0].condition = "Only when the player chooses this reply"
    validate_lore_result(result, assessment())
    result.review.choices_labeled = False
    with pytest.raises(ValueError, match="self-review"):
        validate_lore_result(result, assessment())
