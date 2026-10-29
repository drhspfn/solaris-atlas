"""Publish a verified local build using the same atomic model publication path."""

import asyncio
import json
import logging
import os
import shutil
import struct

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.character_models import CharacterModelJob
from wuwa_story.ingestion.model_archives import CHUNK_BYTES, FILES, digest, read_archive
from wuwa_story.ingestion.model_uploads import ModelUploadJob, chunk_count, chunk_key
from wuwa_story.storage.s3 import S3Storage

from wuwa_story_worker.broker import job_lock
from wuwa_story_worker.job_tracking import update_admin_run
from wuwa_story_worker.media_workspace import finish_writes
from wuwa_story_worker.model_animations import animation_summary
from wuwa_story_worker.model_facial import validate_facial_pack
from wuwa_story_worker.model_sources import glb_summary
from wuwa_story_worker.tooling import asset_workspace
from wuwa_story_worker.transient_workspace import transient_workspace

logger = logging.getLogger(__name__)


def decode_bundles(archive, root, job, expected_forms):
    manifest = read_archive(archive, root)
    if (manifest.get("character_key"), manifest.get("game_version")) != (job.character_key, job.game_version):
        raise ValueError("Archive belongs to another character or snapshot; download its recipe again")
    forms = [entry["form"] for entry in manifest["forms"]]
    if forms != expected_forms:
        raise ValueError("Archive forms no longer match authored model sources; rebuild with a fresh recipe")
    included = ["animations" in entry["files"] for entry in manifest["forms"]]
    if any(included) != all(included):
        raise ValueError("Archive must include animations for every form, or for none")
    publication = CharacterModelJob(run_id=job.run_id, character_key=job.character_key,
        owner_node_id=job.owner_node_id, release_id=job.release_id, game_version=job.game_version,
        asset_version=manifest.get("asset_version"), tier=manifest.get("tier"),
        download_id=digest(archive), include_animations=all(included))
    bundles = []
    for entry in manifest["forms"]:
        form = entry["form"]
        output = root / form["id"]
        output.mkdir()
        for kind, key in entry["files"].items():
            source = root / "objects" / key
            destination = output / FILES[kind]
            try:
                os.link(source, destination)
            except OSError:
                shutil.copyfile(source, destination)
        summaries = {kind: glb_summary(output / FILES[kind], allow_animations=kind == "animations")
                     for kind in ("preview", "download", *( ["animations"] if all(included) else []))}
        if summaries["preview"]["bones"] != summaries["download"]["bones"]:
            raise ValueError("Compact and full model rigs disagree")
        with (output / "character.glb").open("rb") as stream:
            length = struct.unpack_from("<I", stream.read(20), 12)[0]
            full = json.loads(stream.read(length))
        bones = {full["nodes"][index]["name"] for skin in full["skins"] for index in skin["joints"]}
        if all(included):
            animation_summary(output / "animations.glb", bones)
        if "facial" in entry["files"]:
            pack = validate_facial_pack(json.loads((output / "facial.json").read_text()))
            targets = {name.casefold() for mesh in full["meshes"] for name in mesh.get("extras", {}).get("targetNames", [])}
            if any(track["target"].casefold() not in targets for clip in pack["clips"] for track in clip["tracks"]):
                raise ValueError("Facial curves do not match the uploaded model")
        provenance = {**entry["provenance"], "model": form["model"],
                      "form_source": form["source"], "local_archive_sha256": publication.download_id}
        bundles.append((form, output, summaries, provenance))
    return publication, bundles


async def process_model_upload(payload):
    from wuwa_story_worker.character_models import publish_forms, source_forms, source_row

    job = ModelUploadJob.model_validate(payload)
    storage = S3Storage(get_settings())
    async with engine.connect() as connection, job_lock(connection, 707, job.owner_node_id % 2147483648):
        async with SessionFactory() as session:
            run = await session.get(ProcessingRun, job.run_id)
            if run is None or run.metadata_json.get("payload") != job.model_dump():
                raise ValueError("Model upload identity changed")
            if run.status in {"completed", "cancelled"}:
                return
            role = await source_row(session, job.release_id, "BinData/role/roleinfo.json",
                                    int(job.character_key.split(":")[1]))
            forms = await source_forms(session, job.release_id, role)

        async def execute():
            await update_admin_run(job.run_id, "character_model", "running",
                                   raw_output={"stage": "validate_archive"})
            with transient_workspace(asset_workspace().parent / "transient-jobs") as root:
                archive = root / "model.zip"
                with archive.open("xb") as target:
                    for index in range(chunk_count(job)):
                        received = 0
                        expected = min(CHUNK_BYTES, job.archive_bytes - index * CHUNK_BYTES)
                        async for block in storage.get(chunk_key(job, index)):
                            received += len(block)
                            if received > expected:
                                raise ValueError("Stored upload chunk exceeds declared size")
                            target.write(block)
                        if received != expected:
                            raise ValueError("Stored model upload chunk is incomplete")
                publication, bundles = await asyncio.to_thread(decode_bundles, archive, root, job, forms)
                await update_admin_run(job.run_id, "character_model", "running", raw_output={"stage": "publish"})
                await publish_forms(publication, bundles, publication_payload=job.model_dump())
            for index in range(chunk_count(job)):
                try:
                    await storage.delete(chunk_key(job, index))
                except Exception:
                    logger.exception("Published model upload cleanup failed run=%s part=%s", job.run_id, index)

        await finish_writes(execute())
