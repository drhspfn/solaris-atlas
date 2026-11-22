from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes.story import character_archive, transcripts


@pytest.mark.asyncio
async def test_source_transcript_resolves_dialogue_and_choice_terms(monkeypatch):
    text = 'My work log. See—Day 727 in the <te href=700034>world of the dead</te>.'
    result = {"lines": [{"text": {"content": text}, "player_choices": [
        {"text": {"inline_text": '<te href="42">Choice</te>'}},
    ]}]}
    terms = {"700034": {"title": "World of the Dead", "description": "Lily's name for the Somnoire."}}
    monkeypatch.setattr(transcripts.read_projections, "should_route_to_projection", lambda *a, **k: False)
    monkeypatch.setattr(transcripts.usecases, "quest_transcript", AsyncMock(return_value=result))
    monkeypatch.setattr(transcripts, "_release_id", AsyncMock(return_value=38))
    loader = AsyncMock(return_value=terms)
    monkeypatch.setattr(transcripts, "_load_term_explanations", loader)
    response = await transcripts.quest_transcript("7543301", locale="en", game_version="3.8.0",
                                                  include_media=False, session=object())
    assert response["terms"] == terms
    assert transcripts.usecases.quest_transcript.await_args.kwargs["include_media"] is False
    loader.assert_awaited_once_with(
        loader.await_args.args[0], 38, "en", [text, '<te href="42">Choice</te>']
    )


@pytest.mark.asyncio
async def test_projection_returns_published_media_without_second_read(monkeypatch):
    published = {"lines": [{"id": "line:1", "media": {"voice_references": ["voice"]}}]}
    monkeypatch.setattr(transcripts.read_projections, "should_route_to_projection", lambda *a, **k: True)
    read = AsyncMock(return_value=published)
    monkeypatch.setattr(transcripts.read_projections, "transcript_page", read)
    response = await transcripts.quest_transcript("7543301", game_version="3.3.0", include_media=False,
                                                  session=object())
    assert response is published
    assert response["lines"][0]["media"]["voice_references"] == ["voice"]
    assert not response.get("media_deferred")
    read.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_term_format_is_resolved_from_config_and_localization(monkeypatch):
    session = SimpleNamespace(scalars=AsyncMock(return_value=[SimpleNamespace(data={
        "Id": 700034, "Term": "亡魂世界", "Color": "", "Placeholder": [],
    })]))
    localized = AsyncMock(return_value={
        "Term700034_Title": "World of the Dead",
        "Term700034_Desc": "Lily's name for the Somnoire.",
    })
    monkeypatch.setattr(character_archive, "_localized_strings", localized)
    result = await character_archive._load_term_explanations(
        session, 38, "en", ["Day 727 in the <te href=700034>world of the dead</te>"]
    )
    assert result["700034"]["title"] == "World of the Dead"
    assert result["700034"]["description"] == "Lily's name for the Somnoire."
    localized.assert_awaited_once_with(session, 38, "en", ["Term700034_Title", "Term700034_Desc"])
