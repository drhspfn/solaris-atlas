import pytest

from wuwa_story.agents.contracts import AnalysisResult
from wuwa_story.agents.narrative import validate_narrative_links


def result(text):
    citation = {"node_id": 42, "quote": "The crossing is destroyed."}
    return AnalysisResult.model_validate(
        {
            "title": "A blocked crossing",
            "blocks": [{"title": "The route", "text": text, "citations": [citation]}],
            "links": [
                {
                    "from_node_id": 42,
                    "to_node_id": 43,
                    "relation": "EXPLAINS",
                    "explanation": "The destruction forces a detour.",
                    "confidence": 0.9,
                    "citations": [citation],
                }
            ],
            "events": [
                {
                    "title": "Destruction",
                    "description": "The crossing is destroyed.",
                    "citations": [citation],
                }
            ],
        }
    )


def test_narrative_links_refer_to_sourced_result_objects():
    validate_narrative_links(
        result(
            "Because of [the destruction](connection:0), take another route. [Source](record:42) and [event](event:0)."
        )
    )
    validate_narrative_links(result("Legacy plain-text explanation without invented hyperlinks."))


@pytest.mark.parametrize(
    "target",
    [
        "connection:1",
        "event:1",
        "record:43",
        "connection:-1",
        "javascript:alert",
        "https://example.com",
        "/nodes/42",
    ],
)
def test_narrative_rejects_unknown_or_external_targets(target):
    with pytest.raises(ValueError):
        validate_narrative_links(result(f"[A connection]({target})"))
