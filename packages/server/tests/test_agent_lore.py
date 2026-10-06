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
    for term in [
        "star",
        " DREAM ",
        "Rover",
        "time",
        "dream star",
        "the dream",
        "Rover's dream",
        "a",
        "x" * 201,
    ]:
        assert not distinctive_term(term)
    assert distinctive_term("The Seven Swords of Qingren")


def test_strong_links_require_independent_sourced_signals():
    from wuwa_story.agents.contracts import InferredLink, LinkSignal

    result = minimal_result()
    citation = result.blocks[0].citations
    link = InferredLink(
        from_node_id=1,
        to_node_id=2,
        relation="related_to",
        relation_label="Possibly shares a site",
        explanation="A possible shared site",
        confidence=0.8,
        citations=citation,
        signals=[LinkSignal(kind="location", value="Qingren Bridge", citations=citation)],
    )
    result.links = [link]
    with pytest.raises(ValueError, match="independent"):
        validate_lore_result(result, assessment())
    link.signals.append(LinkSignal(kind="specific_phrase", value="Jump here", citations=citation))
    validate_lore_result(result, assessment())


def test_revisit_requires_both_old_and_new_evidence_and_every_hook():
    from wuwa_story.agents.contracts import Citation, HookReview
    from wuwa_story.agents.revisits import validate_revisit

    result = minimal_result()
    context = {
        "release_id": 2,
        "hooks": [{"key": "bridge-route", "citations": [{"snapshot_id": 1, "node_id": 1}]}],
    }
    with pytest.raises(ValueError, match="every requested"):
        validate_revisit(result, context)
    result.revisited_hooks = [
        HookReview(
            hook_key="bridge-route",
            priority="low",
            status="suggested",
            explanation="A possible connection",
            citations=[Citation(snapshot_id=1, node_id=1, quote="Jump here")],
        )
    ]
    with pytest.raises(ValueError, match="new-import"):
        validate_revisit(result, context)
    result.revisited_hooks[0].citations.append(
        Citation(snapshot_id=2, node_id=2, quote="The bridge")
    )
    validate_revisit(result, context)


def test_hook_errors_identify_keys_and_invalid_terms():
    candidate = minimal_result()
    from wuwa_story.agents.contracts import OpenHook
    candidate.hooks = [OpenHook(key="muyu_core", question="What happened to Muyu's core?",
        priority="low", kind="mystery", revisit_on_new_versions=True,
        revisit_reason="Later dialogue may explain it", search_terms=["Muyu", "core", "Rover"],
        citations=candidate.blocks[0].citations)]
    with pytest.raises(ValueError) as error:
        validate_lore_result(candidate, assessment())
    assert "muyu_core" in str(error.value)
    assert "'core'" not in str(error.value)
    assert "Rover" in str(error.value)


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
