from wuwa_story.events.catalog import catalog_rows


def test_source_aliases_form_one_event_with_all_real_periods():
    rows = [
        {"id": 7, "canonical_event_id": 7, "occurrence_id": 1, "starts_at": "1900-01-01"},
        {"id": 8, "canonical_event_id": 7, "occurrence_id": 2, "starts_at": None},
        {"id": 8, "canonical_event_id": 7, "occurrence_id": 3, "starts_at": None},
        {"id": 7, "canonical_event_id": 7, "occurrence_id": 4, "_catalog_placeholder": True},
    ]
    result = catalog_rows(rows)
    assert {row["id"] for row in result} == {7}
    assert [row["occurrence_id"] for row in result] == [1, 2, 3]
    assert [row["source_event_id"] for row in result] == [7, 8, 8]
    assert rows[-1]["_catalog_placeholder"] is True


def test_catalog_without_periods_has_one_presentation_and_no_invented_period():
    rows = [{"id": 7, "occurrence_id": i, "_catalog_placeholder": True,
             "catalog_version": version} for i, version in [(1, "3.8.0"), (2, "3.9.0")]]
    result = catalog_rows(rows)
    assert len(result) == 1
    assert result[0]["catalog_version"] == "3.9.0"
    assert result[0]["occurrence_id"] is None


def test_equal_names_never_bind_unrelated_events():
    assert [row["id"] for row in catalog_rows([
        {"id": 1, "occurrence_id": 10, "title": "Same"},
        {"id": 2, "occurrence_id": 20, "title": "Same"},
    ])] == [1, 2]
