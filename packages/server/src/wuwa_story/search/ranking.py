from typing import Any

MATCH_PRECEDENCE = {
    "canonical": 0,
    "alias_exact": 1,
    "normalized_alias": 2,
    "trigram": 3,
    "full_text": 4,
    "vector": 5,
}


def rank_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            MATCH_PRECEDENCE.get(row.get("match_kind", "full_text"), 99),
            -float(row.get("score", 0.0)),
        ),
    )
