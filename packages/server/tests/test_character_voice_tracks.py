from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes.story import character_archive as archive


@pytest.mark.asyncio
async def test_archive_exposes_independent_tracks_without_changing_text_locale(monkeypatch):
    path = "/Game/Voice/Test"
    monkeypatch.setattr(archive, "get_node", AsyncMock(return_value=SimpleNamespace(id=1, canonical_key="character:1")))
    monkeypatch.setattr(archive, "_release_id", AsyncMock(return_value=1))
    monkeypatch.setattr(archive, "_localized_strings", AsyncMock(return_value={"title": "English title", "text": "English text"}))

    async def records(session, release, file, field, identity):
        if file.endswith("roleinfo.json"):
            return [SimpleNamespace(data={"Id": 1}, row_index=0)]
        if file.endswith("favorword.json"):
            return [SimpleNamespace(data={"Id": 7, "Sort": 1, "Title": "title", "Content": "text", "Voice": path}, row_index=0)]
        return []

    monkeypatch.setattr(archive, "_records", records)
    async def record_groups(session, release, requests):
        return {file: await records(session, release, file, field, identity)
                for file, (field, identity) in requests.items()}

    monkeypatch.setattr(archive, "_record_groups", record_groups)
    monkeypatch.setattr(archive, "get_settings", lambda: SimpleNamespace(s3_bucket="test"))
    monkeypatch.setattr(archive, "S3Storage", lambda _: SimpleNamespace(public_url=lambda key: f"https://cdn.example/{key}"))
    rows = [(path, language, mime, key)
            for language, mime, key in [("en", "audio/wav", "en.wav"), ("ja", "audio/ogg", "ja.ogg"),
                                        ("zh", "audio/wav", "zh.wav"), ("ko", "text/plain", "wrong.txt"),
                                        (None, "audio/wav", "unlabelled.wav")]]
    session = AsyncMock()
    session.get.return_value = object()
    session.execute.side_effect = [[], rows]
    result = await archive.character_archive.__wrapped__("character:1", locale="en", session=session)
    line = result["voice_lines"][0]
    assert result["locale"] == "en" and line["text"] == "English text"
    assert line["audio_url"] == "https://cdn.example/en.wav"
    assert line["audio_tracks"] == {"en": "https://cdn.example/en.wav", "ja": "https://cdn.example/ja.ogg", "zh-Hans": "https://cdn.example/zh.wav"}
    assert session.execute.await_count == 2
