from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from wuwa_story.api.cache import PROJECTION_TABLES
from wuwa_story.api.routes.story import media
from wuwa_story.auth.constants import UserRole
from wuwa_story.auth.dependencies import get_auth_context
from wuwa_story.auth.security import csrf_token
from wuwa_story.config.settings import get_settings
from wuwa_story.db.session import get_session
from wuwa_story.story.sequence_video import attach_sequence_videos, occurrence_id, youtube_id


@pytest.mark.parametrize("url", [
    "https://youtube.com/watch?v=abcdefghijk&feature=share",
    "https://youtu.be/abcdefghijk?t=5",
    "https://www.youtube.com/embed/abcdefghijk",
    "https://m.youtube.com/shorts/abcdefghijk",
])
def test_youtube_url_valid(url):
    assert youtube_id(url) == "abcdefghijk"


@pytest.mark.parametrize("url", [
    "http://youtube.com/watch?v=abcdefghijk",
    "https://youtube.com.evil.test/watch?v=abcdefghijk",
    "https://youtube.com@evil.test/watch?v=abcdefghijk",
    "https://youtube.com:443/watch?v=abcdefghijk",
    "https://youtu.be/abcdefghijk/extra",
    "https://youtube.com/watch?v=abcdefghijk&v=ABCDEFGHIJK",
    "https://youtu.be/invalid",
])
def test_youtube_url_invalid(url):
    with pytest.raises(ValueError):
        youtube_id(url)


@pytest.mark.parametrize("start,end", [(-1, None), (1, 1), (2, 1), (0, -1), (1.5, 2), (True, 2)])
def test_invalid_time_ranges(start, end):
    with pytest.raises(ValidationError):
        media.SequenceVideoInput(url="https://youtu.be/abcdefghijk", start_seconds=start, end_seconds=end)


def event(action="one", raw_path="$.Actions[0]"):
    return {"kind": "sequence", "action": action, "reference": "asset:ue:shared",
            "flow_state": "state", "action_index": 0,
            "source": {"file": "flow.json", "raw_path": raw_path}}


@pytest.mark.asyncio
async def test_overlay_is_exact_and_clears_stale_projected_values():
    first, second = event(), event("two", "$.Actions[1]")
    key = occurrence_id("quest:1", "3.7", first)
    row = SimpleNamespace(occurrence_id=key, video_id="abcdefghijk", start_seconds=5, end_seconds=9)
    session = SimpleNamespace(scalars=AsyncMock(return_value=[row]))
    manifest = {"quest": "quest:1", "game_version": "3.7", "events": [first, second]}
    await attach_sequence_videos(session, manifest)
    assert first["youtube_fallback"]["start_seconds"] == 5
    assert "youtube_fallback" not in second
    assert first["occurrence_id"] != second["occurrence_id"]
    assert key != occurrence_id("quest:1", "3.8", first)
    session.scalars.return_value = []
    await attach_sequence_videos(session, manifest)
    assert "youtube_fallback" not in first


@pytest.mark.asyncio
async def test_ambiguous_duplicate_cannot_be_assigned():
    first = event()
    manifest = {"quest": "quest:1", "game_version": "3.7", "events": [first, deepcopy(first)]}
    session = SimpleNamespace(scalars=AsyncMock())
    await attach_sequence_videos(session, manifest)
    assert all("occurrence_id" not in item for item in manifest["events"])
    session.scalars.assert_not_awaited()


def test_projection_cache_tracks_fallback_table():
    assert "ops.sequence_video_fallback" in PROJECTION_TABLES


def test_admin_api_requires_admin_and_csrf_and_valid_occurrence(monkeypatch):
    app = FastAPI()
    app.include_router(media.router)
    session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), get=AsyncMock(return_value=None))
    app.dependency_overrides[get_session] = lambda: session
    role = [UserRole.USER]
    app.dependency_overrides[get_auth_context] = lambda: SimpleNamespace(user=SimpleNamespace(role=role[0]))
    monkeypatch.setattr(media, "quest_media", AsyncMock(return_value={
        "events": [{"kind": "sequence", "occurrence_id": "a" * 64}],
    }))
    settings = get_settings()
    token = csrf_token(settings.auth_csrf_secret.get_secret_value())
    client = TestClient(app)
    url = "/admin/quests/1/media/" + "a" * 64 + "/youtube?game_version=3.7"
    body = {"url": "https://youtu.be/abcdefghijk", "start_seconds": 5, "end_seconds": 12}
    client.cookies.set(settings.auth_csrf_cookie_name, token)
    assert client.put(url, json=body, headers={"X-CSRF-Token": token}).status_code == 403
    role[0] = UserRole.ADMIN
    assert client.put(url, json=body).status_code == 403
    assert client.put(url.replace("a" * 64, "b" * 64), json=body,
                      headers={"X-CSRF-Token": token}).status_code == 404
    assert client.put(url, json={**body, "end_seconds": 5},
                      headers={"X-CSRF-Token": token}).status_code == 422
    response = client.put(url, json=body, headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    assert response.json()["video_id"] == "abcdefghijk"
    session.commit.assert_awaited_once()
    assert client.delete(url, headers={"X-CSRF-Token": token}).json()["deleted"] is False

    def unauthenticated():
        raise HTTPException(401, "Sign in")

    app.dependency_overrides[get_auth_context] = unauthenticated
    assert client.put(url, json=body, headers={"X-CSRF-Token": token}).status_code == 401
    assert client.delete(url, headers={"X-CSRF-Token": token}).status_code == 401


@pytest.mark.asyncio
async def test_projected_media_overlays_current_video(monkeypatch):
    projected = {"quest": "quest:1", "game_version": "3.7", "events": [event()]}
    key = occurrence_id("quest:1", "3.7", projected["events"][0])
    row = SimpleNamespace(occurrence_id=key, video_id="abcdefghijk", start_seconds=5, end_seconds=8)
    session = SimpleNamespace(scalars=AsyncMock(return_value=[row]))
    monkeypatch.setattr(media.read_projections, "should_route_to_projection", lambda *a, **kw: True)
    monkeypatch.setattr(media.read_projections, "quest_section", AsyncMock(return_value=projected))
    monkeypatch.setattr(media, "_release_id", AsyncMock(return_value=1))
    monkeypatch.setattr(media, "attach_public_descriptions", AsyncMock())
    result = await media.quest_media("1", "3.7", "en", session)
    assert result["events"][0]["youtube_fallback"]["video_id"] == "abcdefghijk"
    assert "youtube_fallback" not in projected["events"][0]
