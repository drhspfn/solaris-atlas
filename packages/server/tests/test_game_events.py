from wuwa_story.ingestion.game_events import _deduplicate_occurrences, _title


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


def test_known_recurring_modes_have_reader_facing_names():
    assert _title("events/matrix", None, "10077000206") == "Endstate Matrix"
    assert _title("events/whimperingwastes", None, "10039000102") == "Whimpering Wastes"


def test_unnamed_activity_uses_a_reader_label_instead_of_an_internal_id():
    assert _title(None, None, "2") == "Limited-time event"
