from decimal import Decimal

import pytest
from pydantic import ValidationError

from wuwa_story.agents.budget import price
from wuwa_story.agents.contracts import AnalysisResult, validate_citations
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
