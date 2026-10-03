from decimal import Decimal

import pytest
from pydantic import ValidationError

from wuwa_story.agents.budget import price
from wuwa_story.agents.contracts import AnalysisResult, StoryAssertion, validate_citations
from wuwa_story.agents.settings import AgentSettings


def result(**changes):
    return AnalysisResult.model_validate(
        {
            "title": "Quest",
            "blocks": [
                {
                    "title": "Why",
                    "text": "An interpretation",
                    "citations": [{"node_id": 1, "quote": "exact line"}],
                }
            ],
            **changes,
        }
    )


def test_analysis_requires_exact_read_evidence_and_bounded_structure():
    validate_citations(result(), {1: "An exact line in the game"})
    with pytest.raises(ValueError):
        validate_citations(result(), {2: "exact line"})
    with pytest.raises(ValueError):
        validate_citations(result(), {1: "paraphrased line"})
    with pytest.raises(ValidationError):
        result(blocks=[])
    with pytest.raises(ValidationError):
        result(arbitrary_sql="DROP TABLE graph.node")


def test_decimal_prices_round_conservatively_and_reject_negative_usage():
    assert price(1000, 100, Decimal("0.10"), Decimal("0.50"), Decimal("1.25")) == Decimal(
        "0.00018750"
    )
    assert price(1, 0, Decimal("0.001"), Decimal(0), Decimal(1)) == Decimal("0.00000001")
    with pytest.raises(ValueError):
        price(-1, 0, Decimal(1), Decimal(1), Decimal(1))


def test_paid_execution_is_opt_in_and_credentials_never_checkpointed():
    settings = AgentSettings(_env_file=None, api_key="never-persist-this")
    with pytest.raises(ValueError):
        settings.require_enabled()
    assert "api_key" not in settings.public_config()
    assert "never-persist-this" not in str(settings.public_config())


def assertion():
    return {
        "text": "A flashback is shown.",
        "status": "unresolved",
        "citations": [{"node_id": 1, "quote": "exact line"}],
        "chronology_in_quest": {"order": 0, "anchor_node_id": 1, "label": "Opening flashback"},
        "world_chronology": {"placement": "unknown", "explanation": "Its date is not established."},
        "knowledge_state": "The player has seen the clue, but does not know its meaning.",
        "later_resolution": [
            {
                "status": "partial",
                "text": "A later source adds context.",
                "revealed_in_node_id": 2,
                "citations": [{"node_id": 2, "quote": "later line"}],
            }
        ],
    }


def test_temporal_assertions_preserve_unknown_time_and_validate_later_evidence():
    old = result()
    with pytest.raises(ValueError, match="assertions"):
        old.validate_temporal_structure()
    old.blocks[0].assertions = [StoryAssertion.model_validate(assertion())]
    old.validate_temporal_structure()
    validate_citations(old, {1: "exact line", 2: "later line"})
    with pytest.raises(ValueError, match="Citation"):
        validate_citations(old, {1: "exact line"})
    assert old.blocks[0].assertions[0].status == "unresolved"
    assert "does not know" in old.blocks[0].assertions[0].knowledge_state
    assert "later source" in old.blocks[0].search_text()


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "certain"},
        {"citations": []},
        {"chronology_in_quest": {"order": -1, "anchor_node_id": 1, "label": "Bad"}},
    ],
)
def test_atomic_claims_reject_missing_evidence_and_invalid_certainty(changes):
    with pytest.raises(ValidationError):
        StoryAssertion.model_validate({**assertion(), **changes})
