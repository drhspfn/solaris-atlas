from types import SimpleNamespace

from wuwa_story.api.item_acquisition import group_item_markers


def marker(identity, item=None, drops=None, name="Enemy", hidden=False):
    return SimpleNamespace(
        id=identity,
        category="resource" if item else "monster",
        blueprint_type="BP",
        metadata_json={
            "item_id": item,
            "drop_item_ids": drops or [],
            "names": {"en": name},
            "hidden": hidden,
        },
    )


def atlas(identity):
    return SimpleNamespace(id=identity, game_version="3.7.0", metadata_json={})


def test_only_exact_item_sources_are_grouped_and_links_remain_short():
    a = atlas(5)
    result = group_item_markers(
        [
            (marker(10, drops=[7]), a),
            (marker(11, drops=[7], hidden=True), a),
            (marker(12, drops=[8]), a),
            (marker(13, item=7, name="Plant"), a),
        ],
        7,
    )
    assert len(result) == 2
    assert result[0]["kind"] == "loot_preview"
    assert result[0]["count"] == 2
    assert result[0]["hidden_count"] == 1
    assert result[0]["url"] == "/map?map=5&item=7&source=10"
    assert result[1]["kind"] == "gathering"


def test_worlds_are_not_merged_and_empty_sources_stay_empty():
    assert (
        len(
            group_item_markers(
                [(marker(10, drops=[7]), atlas(5)), (marker(11, drops=[7]), atlas(6))], 7
            )
        )
        == 2
    )
    assert group_item_markers([(marker(10, drops=[8]), atlas(5))], 7) == []
