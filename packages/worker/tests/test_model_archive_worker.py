import json
import struct
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from wuwa_story.ingestion.model_archives import write_archive
from wuwa_story.ingestion.model_uploads import ModelUploadJob

from wuwa_story_worker import character_models, model_uploads


def portable(tmp_path):
    output = tmp_path / "bundle"
    output.mkdir()
    data = {"asset": {"version": "2.0"}, "nodes": [{"name": "Root"}],
            "skins": [{"joints": [0]}], "meshes": [{"primitives": []}]}
    payload = json.dumps(data).encode()
    payload += b" " * (-len(payload) % 4)
    raw = struct.pack("<4sIII4s", b"glTF", 2, 20 + len(payload), len(payload), b"JSON") + payload
    for name in ("preview.glb", "character.glb"):
        (output / name).write_bytes(raw)
    form = {"id": "mesh-1", "label": "Default", "order": 0, "model": "Aki/Character/Role/Test/Model/Rig",
            "blueprints": [], "source": {"table": "roleinfo", "id": 1}}
    archive = tmp_path / "model.zip"
    write_archive(archive, {"character_key": "character:1", "game_version": "3.3.0",
        "asset_version": "3.7.0", "tier": "hd"}, [(form, output, {}, {"model": form["model"]})])
    job = ModelUploadJob(run_id=1, character_key="character:1", owner_node_id=2, release_id=3,
        game_version="3.3.0", archive_bytes=archive.stat().st_size, storage_token="a" * 32)
    return archive, form, job


def test_decoding_uses_archive_identity_and_rejects_stale_authored_forms(tmp_path):
    archive, form, job = portable(tmp_path)
    publication, bundles = model_uploads.decode_bundles(archive, tmp_path / "decoded", job, [form])
    assert publication.asset_version == "3.7.0" and not publication.include_animations
    assert len(bundles) == 1 and bundles[0][2]["download"]["bones"] == 1
    with pytest.raises(ValueError, match="no longer match"):
        model_uploads.decode_bundles(archive, tmp_path / "stale", job, [{**form, "model": "Other"}])
    wrong = job.model_copy(update={"character_key": "character:2"})
    with pytest.raises(ValueError, match="another character"):
        model_uploads.decode_bundles(archive, tmp_path / "wrong", wrong, [form])


@pytest.mark.asyncio
async def test_uploaded_model_publishes_without_game_client_or_extraction(tmp_path, monkeypatch):
    archive, form, job = portable(tmp_path)
    run = NS(status="queued", metadata_json={"payload": job.model_dump()})
    session = NS(get=AsyncMock(return_value=run))

    @asynccontextmanager
    async def context(*args):
        yield session

    storage = NS(delete=AsyncMock())

    async def get(key):
        yield archive.read_bytes()

    storage.get = get
    monkeypatch.setattr(model_uploads, "S3Storage", lambda _: storage)
    monkeypatch.setattr(model_uploads, "engine", NS(connect=context))
    monkeypatch.setattr(model_uploads, "job_lock", context)
    monkeypatch.setattr(model_uploads, "SessionFactory", context)
    monkeypatch.setattr(model_uploads, "asset_workspace", lambda: tmp_path / "not-installed-client")
    monkeypatch.setattr(model_uploads, "update_admin_run", AsyncMock())
    monkeypatch.setattr(character_models, "source_row", AsyncMock(return_value={"Id": 1}))
    monkeypatch.setattr(character_models, "source_forms", AsyncMock(return_value=[form]))
    monkeypatch.setattr(character_models, "extract_model", AsyncMock(side_effect=AssertionError("must not extract")))
    publish = AsyncMock()
    monkeypatch.setattr(character_models, "publish_forms", publish)
    await character_models.process_character_model(job.model_dump())
    publish.assert_awaited_once()
    assert publish.call_args.kwargs["publication_payload"] == job.model_dump()
    storage.delete.assert_awaited_once()
    assert not list((tmp_path / "transient-jobs").glob("*/model.zip"))
