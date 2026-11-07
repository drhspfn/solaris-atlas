from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from wuwa_story.api.routes.story import npc_profile


@pytest.mark.parametrize("localized", [None, {"content": None, "resolution": "missing_key"}])
def test_dialogue_inline_text_uses_localized_content_shape(localized):
    text = npc_profile.dialogue_text(localized, "Authored subtitle")
    assert text["content"] == text["inline_text"] == "Authored subtitle"
    assert text["resolution"] == "inline"


@pytest.mark.asyncio
async def test_christoforo_scene_reports_unavailable_text_without_fabricating_it(monkeypatch):
    # Release 21 flow 剧情_2_5_隐海试验场_失忘彼岸_2_1 has 13 TidTalk keys,
    # including Main_Linaxita_FuLuoLuoCE_70_11, but no retained localization or inline text.
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(SimpleNamespace(id=1), None, {}, 21)))
    monkeypatch.setattr(npc_profile, "npc_flow_state_ids", AsyncMock(return_value=[2499781]))
    monkeypatch.setattr(npc_profile, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(npc_profile, "prefetch_localizations", AsyncMock())
    monkeypatch.setattr(npc_profile, "_localized", AsyncMock(return_value=None))
    monkeypatch.setattr(npc_profile, "_node_label", AsyncMock(return_value={"label": "Phrolova"}))
    state = SimpleNamespace(node_id=2499781, state_key="剧情_2_5_隐海试验场_失忘彼岸_2_1")
    page = [(SimpleNamespace(localization_key_id=None, inline_text=None),
             SimpleNamespace(canonical_key=f"dialogue:{index}"), state, 560, False)
            for index in range(13)]
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[1, 13]),
                              execute=AsyncMock(side_effect=[page, []]))
    result = await npc_profile.npc_dialogue.__wrapped__(
        "character:npc:15697", locale="en", q="", limit=20, offset=0,
        state_id=2499781, session=session)
    assert result["total"] == 13
    assert all(row["text"] == {"content": None, "inline_text": None, "resolution": "unavailable"}
               for row in result["results"])


def test_shop_price_decoder_accepts_native_key_value_rows():
    assert npc_profile.price_pairs([{"Key": 1, "Value": 50}, {"Key": 2, "Value": 0}]) == [(1, 50), (2, 0)]
    assert npc_profile.price_pairs({"1": 50}) == [(1, 50)]


@pytest.mark.asyncio
async def test_profile_keeps_flows_separate_and_has_no_guessed_portrait(monkeypatch):
    node = SimpleNamespace(id=1, canonical_key="character:npc:123", slug=None)
    character = SimpleNamespace(
        canonical_name="Trader", name_key_id=None, description=None)
    monkeypatch.setattr(npc_profile, "_localized",
                        AsyncMock(return_value=None))
    metadata = {"blueprint_id": 123, "speakers": ["speaker:9"],
                "flow_references": [{"flow": ["A", 1, 2], "basis": "explicit_flow_triple"}]}
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(first=lambda: None)))
    result = await npc_profile.npc_profile(session, node, character, metadata, 5, "en")
    assert result["npc"]["speakers"] == []
    assert result["npc"]["flow_reference_count"] == 1
    assert "flow_references" not in result["npc"]
    assert result["media"]["image_url"] is None


@pytest.mark.asyncio
async def test_profile_caps_large_instance_list(monkeypatch):
    node = SimpleNamespace(id=1, canonical_key="character:npc:123", slug=None)
    character = SimpleNamespace(canonical_name=None, name_key_id=None, description=None)
    monkeypatch.setattr(npc_profile, "_localized", AsyncMock(return_value=None))
    metadata = {"instances": [{"entity_id": i} for i in range(1000)]}
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(first=lambda: None)))
    result = await npc_profile.npc_profile(session, node, character, metadata, 5, "en")
    assert result["npc"]["instance_count"] == 1000
    assert len(result["npc"]["instances"]) == 10


@pytest.mark.asyncio
async def test_profile_reuses_scoped_search_title_and_preserves_variants(monkeypatch):
    node = SimpleNamespace(id=1, canonical_key="character:npc:17290", slug=None)
    character = SimpleNamespace(canonical_name=None, name_key_id=None, description=None)
    indexed = SimpleNamespace(title="Zahira")
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(first=lambda: indexed)),
                              scalars=AsyncMock(return_value=[2]))
    monkeypatch.setattr(npc_profile, "prefetch_localizations", AsyncMock())
    monkeypatch.setattr(npc_profile, "_localized", AsyncMock(side_effect=[None, {"content": "I.R.I.S."}]))
    result = await npc_profile.npc_profile(session, node, character, {"name_variants": [{"name": {"key": "iris"}}]}, 5, "en")
    assert result["character"]["name"]["content"] == "Zahira"
    assert result["npc"]["name_scope"] == "instance"
    assert result["npc"]["name_variants"] == ["Zahira", "I.R.I.S."]
    assert result["character"]["aliases"] == []


@pytest.mark.asyncio
async def test_shop_offer_page_batches_items_and_preserves_conditions(monkeypatch):
    offers = [{"Id": i, "ItemId": 100, "ItemNum": 2, "Price": [{"Key": 1, "Value": 50}],
               "CondId": 42, "VisableCondition": 7, "DisableCondition": 8, "Show": True} for i in range(60)]
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(None, None,
        {"shops": [{"id": 9, "shopfixed": offers}]}, 5)))
    class Rows:
        def all(self):
            return [(100, 10), (1, 11)]
    session = SimpleNamespace(execute=AsyncMock(return_value=Rows()))
    monkeypatch.setattr(npc_profile, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(npc_profile, "_node_label", AsyncMock(side_effect=lambda s, node, locale: {"label": f"Item {node}", "href": f"/items/{node}"}))
    result = await npc_profile.npc_shop_offers("character:npc:123", 9, locale="en", limit=50, offset=50, session=session)
    assert result["total"] == 60 and len(result["results"]) == 10
    offer = result["results"][0]
    assert offer["price"] == [{"item": {"label": "Item 11", "href": "/items/11"}, "amount": 50}]
    assert offer["item"]["href"] == "/items/10" and offer["count"] == 2
    assert offer["conditions"] == {"CondId": 42, "VisableCondition": 7, "DisableCondition": 8, "Show": True}
    assert result["reset"] is None
    session.execute.assert_awaited_once()
    npc_profile.prefetch_node_labels.assert_awaited_once()


@pytest.mark.asyncio
async def test_shop_page_localizes_titles_in_one_batch(monkeypatch):
    shops = [{"id": i, "shopinfo": [{"ShopName": "Shop_Name"}], "shopfixed": []}
             for i in range(51)]
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(None, None, {"shops": shops}, 5)))
    class Rows:
        def all(self):
            return [("Shop_Name", 10)]
    session = SimpleNamespace(execute=AsyncMock(return_value=Rows()))
    monkeypatch.setattr(npc_profile, "prefetch_localizations", AsyncMock())
    monkeypatch.setattr(npc_profile, "_localized", AsyncMock(return_value={"content": "Market"}))
    result = await npc_profile.npc_shops("character:npc:123", locale="en", limit=50, offset=50, session=session)
    assert result["total"] == 51 and len(result["results"]) == 1
    assert result["results"][0]["name"]["content"] == "Market"
    npc_profile.prefetch_localizations.assert_awaited_once_with(session, [10], "en", 5)
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_current_metadata_is_not_served_for_historical_snapshot(monkeypatch):
    node = SimpleNamespace(id=1, canonical_key="character:npc:123")
    character = SimpleNamespace(
        metadata_json={"role_type": 6, "npc": {"blueprint_id": 123}})
    session = SimpleNamespace(get=AsyncMock(return_value=character))
    monkeypatch.setattr(npc_profile, "get_node", AsyncMock(return_value=node))
    monkeypatch.setattr(npc_profile, "_release_id", AsyncMock(
        side_effect=lambda s, version: 4 if version else 5))
    with pytest.raises(HTTPException) as error:
        await npc_profile.npc_record(session, node.canonical_key, "old")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_no_speaker_does_not_assume_flow_participants(monkeypatch):
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(SimpleNamespace(id=1), None, {
        "speakers": [], "flow_references": []}, 5)))
    monkeypatch.setattr(npc_profile, "npc_flow_state_ids", AsyncMock(return_value=[]))
    session = SimpleNamespace(scalar=AsyncMock(
        return_value=1), scalars=AsyncMock())
    result = await npc_profile.npc_dialogue.__wrapped__("character:npc:123", locale="en", q="", limit=20, offset=0, session=session)
    assert result["results"] == [] and result["total"] == 0
    session.scalars.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_speaker_does_not_add_unrelated_dialogue_or_quests(monkeypatch):
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(
        SimpleNamespace(id=1), None, {"speakers": ["speaker:9"]}, 5)))
    monkeypatch.setattr(npc_profile, "npc_flow_state_ids", AsyncMock(return_value=[]))
    session = SimpleNamespace(scalar=AsyncMock(return_value=1), scalars=AsyncMock())
    dialogue = await npc_profile.npc_dialogue.__wrapped__(
        "character:npc:1", locale="en", q="", limit=20, offset=0, session=session)
    quests = await npc_profile.npc_quests.__wrapped__(
        "character:npc:1", locale="en", limit=20, offset=0, session=session)
    assert dialogue["results"] == [] and quests["results"] == []
    session.scalars.assert_not_awaited()


@pytest.mark.asyncio
async def test_dialogue_is_paged_and_ignores_stale_name_based_speaker(monkeypatch):
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(SimpleNamespace(id=1), None,
                                                                           {"speakers": ["speaker:9"], "flow_references": []}, 5)))
    monkeypatch.setattr(npc_profile, "npc_flow_state_ids", AsyncMock(return_value=[21]))
    line = SimpleNamespace(localization_key_id=12, inline_text="fallback")
    node = SimpleNamespace(canonical_key="dialogue:1")
    state = SimpleNamespace(node_id=21, state_key="A/1/2")
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[1, 25]),
                              scalars=AsyncMock(return_value=[9]), execute=AsyncMock(side_effect=[[(line, node, state, 9, False)], []]))
    monkeypatch.setattr(npc_profile, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(npc_profile, "prefetch_localizations", AsyncMock())
    monkeypatch.setattr(npc_profile, "_node_label", AsyncMock(return_value={"canonical_key": "speaker:9"}))
    monkeypatch.setattr(npc_profile, "_localized", AsyncMock(
        return_value={"content": "Hello"}))
    result = await npc_profile.npc_dialogue.__wrapped__("character:npc:123", locale="en",
                                                        q="hello", limit=20, offset=20, session=session)
    assert result["total"] == 25 and result["results"][0]["text"]["content"] == "Hello"
    sql = str(session.execute.call_args_list[0].args[0])
    assert "participation_spoken" not in sql and "LIMIT" in sql and "OFFSET" in sql
    assert result["results"][0]["speaker_relation"] == "interaction_participant"
    assert "localization_value" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Iris", "Zahira", "Rosemary"])
async def test_interaction_flow_provides_dialogue_without_personal_speaker(monkeypatch, name):
    node = SimpleNamespace(id=1)
    metadata = {"speakers": [], "flow_references": [{"flow": [name, 1, 2], "basis": "explicit_flow_triple"}]}
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(node, None, metadata, 5)))
    monkeypatch.setattr(npc_profile, "npc_flow_state_ids", AsyncMock(return_value=[21]))
    line = SimpleNamespace(localization_key_id=12, inline_text="fallback")
    state = SimpleNamespace(node_id=21, state_key=f"{name}/1/2")
    page = [(line, SimpleNamespace(canonical_key="dialogue:1"), state, 9, False),
            (line, SimpleNamespace(canonical_key="dialogue:2"), state, 10, True)]
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[1, 2]), execute=AsyncMock(side_effect=[page, []]))
    monkeypatch.setattr(npc_profile, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(npc_profile, "prefetch_localizations", AsyncMock())
    monkeypatch.setattr(npc_profile, "_node_label", AsyncMock(side_effect=lambda s, i, loc: {"canonical_key": f"speaker:{i}"}))
    monkeypatch.setattr(npc_profile, "_localized", AsyncMock(return_value={"content": "Hello"}))
    result = await npc_profile.npc_dialogue.__wrapped__("character:npc:123", locale="en", q="", limit=20, offset=0, session=session)
    assert [line["speaker_relation"] for line in result["results"]] == ["interaction_participant", "npc_actor_context"]
    assert result["total"] == 2
    assert session.execute.await_count == 2  # one page query and one batched quest-state lookup


@pytest.mark.asyncio
async def test_quest_browser_uses_interaction_state_without_personal_speaker(monkeypatch):
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(SimpleNamespace(id=1), None,
        {"speakers": []}, 5)))
    monkeypatch.setattr(npc_profile, "npc_flow_state_ids", AsyncMock(return_value=[21]))
    quest = SimpleNamespace(node_id=100, game_quest_id=200)
    session = SimpleNamespace(scalar=AsyncMock(return_value=1), scalars=AsyncMock(return_value=[quest]))
    monkeypatch.setattr(npc_profile, "_quest_info", AsyncMock(return_value={"game_quest_id": 200}))
    result = await npc_profile.npc_quests.__wrapped__("character:npc:123", locale="en", limit=20, offset=0, session=session)
    assert result["results"] == [{"game_quest_id": 200}]
    assert "state_owners" in str(session.scalars.call_args.args[0])


@pytest.mark.asyncio
async def test_dialogue_resolves_shared_quest_once_for_multiple_states(monkeypatch):
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(SimpleNamespace(id=1), None, {"speakers": []}, 5)))
    monkeypatch.setattr(npc_profile, "npc_flow_state_ids", AsyncMock(return_value=[21, 22]))
    line = SimpleNamespace(localization_key_id=12, inline_text="hello")
    page = [(line, SimpleNamespace(canonical_key=f"dialogue:{i}"),
             SimpleNamespace(node_id=i, state_key=f"state:{i}"), 9, False) for i in (21, 22)]
    quest = SimpleNamespace(node_id=100)
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[1, 2]),
                              execute=AsyncMock(side_effect=[page, [(100, 21), (100, 22)]]),
                              scalars=AsyncMock(return_value=[quest]))
    monkeypatch.setattr(npc_profile, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(npc_profile, "prefetch_localizations", AsyncMock())
    monkeypatch.setattr(npc_profile, "_node_label", AsyncMock(return_value={"canonical_key": "speaker:9"}))
    monkeypatch.setattr(npc_profile, "_localized", AsyncMock(return_value={"content": "hello"}))
    monkeypatch.setattr(npc_profile, "_quest_info", AsyncMock(return_value={"game_quest_id": 200}))
    result = await npc_profile.npc_dialogue.__wrapped__("character:npc:123", locale="en", q="", limit=20, offset=0, session=session)
    assert all(row["quests"] == [{"game_quest_id": 200}] for row in result["results"])
    assert session.execute.await_count == 2
    npc_profile._quest_info.assert_awaited_once()
    npc_profile.prefetch_localizations.assert_awaited_once()


@pytest.mark.asyncio
async def test_flow_participants_remain_separate_from_confirmed_identity(monkeypatch):
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(SimpleNamespace(id=1), None, {
        "speakers": [], "flow_references": [{"flow": ["A", 1, 2], "basis": "explicit_flow_triple"}]}, 5)))
    monkeypatch.setattr(npc_profile, "npc_flow_state_ids", AsyncMock(return_value=[21]))
    session = SimpleNamespace(scalars=AsyncMock(return_value=[21]),
                              execute=AsyncMock(return_value=[(9, "A/1/2")]))
    monkeypatch.setattr(npc_profile, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(npc_profile, "_node_label", AsyncMock(return_value={"canonical_key": "speaker:9"}))
    result = await npc_profile.npc_flow_participants("character:npc:123", locale="en", session=session)
    assert result == {"results": [{"speaker": {"canonical_key": "speaker:9"}, "flow_state": "A/1/2"}], "truncated": False}
    assert "participation_spoken" in str(session.execute.call_args.args[0])


@pytest.mark.asyncio
async def test_flow_participants_reports_truncation_when_more_than_100_states(monkeypatch):
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(SimpleNamespace(id=1), None, {}, 5)))
    monkeypatch.setattr(npc_profile, "npc_flow_state_ids", AsyncMock(return_value=list(range(101))))
    monkeypatch.setattr(npc_profile, "prefetch_node_labels", AsyncMock())
    session = SimpleNamespace(execute=AsyncMock(return_value=[]))
    result = await npc_profile.npc_flow_participants("character:npc:123", session=session)
    assert result == {"results": [], "truncated": True}
