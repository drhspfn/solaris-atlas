from wuwa_story.ingestion.game_events import _deduplicate_occurrences


def test_deduplicate_occurrences_keeps_latest_duplicate_and_distinct_servers():
    rows = [
        {
            "event_id": 1,
            "source_occurrence_id": "100",
            "server": "asia",
            "source_data": {"revision": 1},
        },
        {
            "event_id": 1,
            "source_occurrence_id": "100",
            "server": "europe",
            "source_data": {"revision": 1},
        },
        {
            "event_id": 1,
            "source_occurrence_id": "100",
            "server": "asia",
            "source_data": {"revision": 2},
        },
        {
            "event_id": 2,
            "source_occurrence_id": "100",
            "server": "asia",
            "source_data": {"revision": 1},
        },
    ]

    result = _deduplicate_occurrences(rows)

    assert result == [rows[2], rows[1], rows[3]]
