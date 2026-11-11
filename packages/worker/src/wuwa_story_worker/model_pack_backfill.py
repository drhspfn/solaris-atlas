"""Repackage an existing completed character model publication without a game client."""

import argparse
import asyncio
import hashlib
import logging
import os
import re
import shutil
from datetime import UTC, datetime

from sqlalchemy import func, select
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.character_models import CharacterModelJob
from wuwa_story.ingestion.model_archives import FILES, MAX_EXPANDED_BYTES
from wuwa_story.ingestion.model_limits import MAX_MODEL_FORMS
from wuwa_story.storage.s3 import S3Storage

from wuwa_story_worker.broker import job_lock
from wuwa_story_worker.media_workspace import finish_writes
from wuwa_story_worker.model_animation_repack import repackage_bundles
from wuwa_story_worker.model_sources import glb_summary
from wuwa_story_worker.model_uploads import _component_summary
from wuwa_story_worker.tooling import asset_workspace
from wuwa_story_worker.transient_workspace import transient_workspace

logger = logging.getLogger(__name__)
JOB_TYPE = "media.character_model_pack_reuse"
FIXED_KINDS = {kind: name for kind, name in FILES.items() if kind != "animation_manifest"}


def source_job(run, processor_key):
    if run is None or run.status != "completed" or processor_key != "character_model":
        raise ValueError("Source must be a completed character model run")
    payload = (run.metadata_json or {}).get("payload")
    job = CharacterModelJob.model_validate(payload)
    if job.run_id != run.id or run.target_node_id != job.owner_node_id:
        raise ValueError("Source run identity does not match its payload")
    return job


async def source_rows(session, job, bucket):
    refs = await session.execute(
        select(FileReference.reference_type, FileReference.metadata_json, FileObject.sha256,
               FileObject.size_bytes, FileLocation.object_key)
        .outerjoin(FileObject, FileObject.id == FileReference.file_id)
        .outerjoin(FileLocation, (FileLocation.file_id == FileObject.id)
                   & (FileLocation.backend == "s3") & (FileLocation.bucket == bucket)
                   & FileLocation.available.is_(True) & FileLocation.is_primary.is_(True))
        .where(FileReference.owner_node_id == job.owner_node_id,
               FileReference.release_id == job.release_id,
               FileReference.metadata_json["model_bundle_id"].astext == str(job.run_id),
               FileReference.reference_type.in_(
                   [*("character_model_" + kind for kind in FIXED_KINDS),
                    "character_model_animation_pack", "character_model_animation_manifest"]))
        .order_by(FileReference.id)
    )
    return list(refs)


async def skip_reason(session, job):
    completed = await session.scalar(select(ProcessingRun.id).where(
        ProcessingRun.processor_id == select(Processor.id).where(
            Processor.key == "character_model").scalar_subquery(),
        ProcessingRun.status == "completed",
        ProcessingRun.metadata_json["payload"]["job_type"].astext == JOB_TYPE,
        ProcessingRun.metadata_json["payload"]["source_run_id"].astext == str(job.run_id),
    ).limit(1))
    if completed is not None:
        return f"already repackaged as run {completed}"
    refs = FileReference.metadata_json["model_bundle_id"].astext
    packed = await session.scalar(select(FileReference.id).where(
        FileReference.owner_node_id == job.owner_node_id, FileReference.release_id == job.release_id,
        refs == str(job.run_id), FileReference.reference_type.in_(
            ("character_model_animation_pack", "character_model_animation_manifest")),
    ).limit(1))
    if packed is not None:
        return "source already contains animation packs"
    # Publication order is the reference ID, as in published_model_releases.
    publication_ids = select(refs.label("bundle"), func.max(FileReference.id).label("last_id")).where(
        FileReference.owner_node_id == job.owner_node_id,
        FileReference.reference_type.in_(("character_model_preview", "character_model_download")),
        refs.is_not(None),
    ).group_by(refs).subquery()
    source_last = await session.scalar(select(publication_ids.c.last_id).where(
        publication_ids.c.bundle == str(job.run_id)))
    latest = await session.scalar(select(func.max(publication_ids.c.last_id)))
    if source_last is None:
        raise ValueError("Source publication has no model references")
    if latest != source_last:
        return "a newer model publication exists"
    return None


async def materialize(rows, root, storage):
    forms, downloads, total = {}, {}, 0
    for reference_type, metadata, digest, size, key in rows:
        if reference_type in {"character_model_animation_pack", "character_model_animation_manifest"}:
            raise ValueError("Source already contains animation packs")
        kind = reference_type.removeprefix("character_model_")
        if kind not in FIXED_KINDS or not isinstance(metadata, dict):
            raise ValueError("Invalid source model reference")
        form_id = metadata.get("form_id") or "default"
        label = metadata.get("form_label") or ("Default" if form_id == "default" else None)
        order = metadata.get("form_order") if metadata.get("form_order") is not None else 0
        component, scene, provenance = metadata.get("component"), metadata.get("scene"), metadata.get("game_source")
        if (not isinstance(form_id, str) or re.fullmatch(
                r"default|mesh-[1-9][0-9]*(?:-form-[1-9][0-9]*)?|component-[a-f0-9]{16}", form_id) is None
                or not isinstance(label, str) or not label.strip() or type(order) is not int or order < 0
                or not isinstance(provenance, dict) or not isinstance(provenance.get("model"), str)
                or not provenance["model"].strip() or (component is not None and not isinstance(component, dict))
                or (scene is not None and not isinstance(scene, dict))
                or form_id.startswith("component-") != (component is not None)
                or (component is not None and (not isinstance(component.get("id"), str)
                    or not isinstance(component.get("parent_form_id"), str)))
                or (component is not None and scene is not None)):
            raise ValueError("Invalid source form metadata")
        form = {"id": form_id, "label": label, "order": order}
        if component is not None:
            form["component"] = component
        provenance = dict(provenance)
        if scene is not None:
            provenance["scene"] = scene
        entry = forms.setdefault(form_id, {"form": form, "provenance": provenance, "files": {}})
        if entry["form"] != form or entry["provenance"] != provenance or kind in entry["files"]:
            raise ValueError("Conflicting or duplicate source form references")
        if not isinstance(digest, bytes) or len(digest) != 32 or type(size) is not int or not 0 < size <= MAX_EXPANDED_BYTES:
            raise ValueError("Invalid source file identity")
        if not isinstance(key, str) or not key:
            raise ValueError("Invalid source file location")
        if digest not in downloads:
            total += size
            if total > MAX_EXPANDED_BYTES:
                raise ValueError("Source model exceeds size limit")
            path = root / digest.hex()
            actual, count = hashlib.sha256(), 0
            with path.open("xb") as target:
                async for block in storage.get(key):
                    count += len(block)
                    if count > size:
                        raise ValueError("Source file exceeds declared size")
                    actual.update(block)
                    target.write(block)
            if count != size or actual.digest() != digest:
                raise ValueError("Source file checksum or size mismatch")
            downloads[digest] = (path, size)
        source, previous_size = downloads[digest]
        if size != previous_size:
            raise ValueError("Conflicting source file sizes")
        entry["files"][kind] = source
    if not forms or len(forms) > MAX_MODEL_FORMS:
        raise ValueError("Invalid number of source forms")
    for form_id in forms:
        visited, current = set(), form_id
        while current in forms and forms[current]["form"].get("component"):
            if current in visited:
                raise ValueError("Source component graph contains a cycle")
            visited.add(current)
            current = forms[current]["form"]["component"]["parent_form_id"]
        if current not in forms:
            raise ValueError("Source component parent is missing")
    bundles = []
    for entry in forms.values():
        if not {"preview", "download"} <= entry["files"].keys():
            raise ValueError("Source form is incomplete")
        output = root / entry["form"]["id"]
        output.mkdir()
        for kind, source in entry["files"].items():
            target = output / FIXED_KINDS[kind]
            try:
                os.link(source, target)
            except OSError:
                shutil.copyfile(source, target)
        summary = _component_summary if entry["form"].get("component") else glb_summary
        summaries = {kind: summary(output / FIXED_KINDS[kind]) for kind in ("preview", "download")}
        bundles.append((entry["form"], output, summaries, entry["provenance"]))
    return sorted(bundles, key=lambda item: (item[0]["order"], item[0]["id"]))


async def load_source(session, run_id):
    run = await session.get(ProcessingRun, run_id)
    processor = await session.get(Processor, run.processor_id) if run else None
    return source_job(run, processor.key if processor else None)


async def mark_failed(run_id, error):
    async with SessionFactory() as session:
        run = await session.scalar(select(ProcessingRun).where(
            ProcessingRun.id == run_id).with_for_update())
        if run is not None and run.status == "running":
            run.status = "failed"
            run.error = "Model pack backfill failed (" + type(error).__name__ + ")"
            run.finished_at = datetime.now(UTC)
            await session.commit()


async def backfill(run_id):
    if type(run_id) is not int or not 0 < run_id < 2**63:
        raise ValueError("Run ID must be a positive integer")
    async with SessionFactory() as session:
        initial = await load_source(session, run_id)
    async with engine.connect() as connection, job_lock(connection, 707, initial.owner_node_id % 2147483648):
        async with SessionFactory() as session:
            job = await load_source(session, run_id)
            if job != initial:
                raise ValueError("Source run changed while acquiring the lock")
            reason = await skip_reason(session, job)
            if reason:
                logger.warning("Skipping source run %s: %s", run_id, reason)
                return None
            rows = await source_rows(session, job, get_settings().s3_bucket)
            processor_id = await session.scalar(select(Processor.id).where(
                Processor.key == "character_model"))
            if processor_id is None:
                raise ValueError("Character model processor is missing")
            run = ProcessingRun(processor_id=processor_id, target_node_id=job.owner_node_id,
                                status="running")
            session.add(run)
            await session.flush()
            new_id = run.id
            payload = {"job_type": JOB_TYPE, "source_run_id": run_id, "run_id": new_id}
            run.metadata_json = {"payload": payload}
            await session.commit()

        async def execute():
            from wuwa_story_worker.character_models import publish_forms

            with transient_workspace(asset_workspace().parent / "transient-jobs") as root:
                storage = S3Storage(get_settings())
                bundles = await materialize(rows, root, storage)
                destination = root / "repacked"
                repacked = await asyncio.to_thread(repackage_bundles, bundles, destination)
                # The lock excludes normal model publications; check again immediately
                # before publishing to catch a publication made outside that workflow.
                async with SessionFactory() as session:
                    reason = await skip_reason(session, job)
                    if reason:
                        raise ValueError("Source became ineligible before publication")
                await publish_forms(job.model_copy(update={"run_id": new_id}), repacked,
                                    publication_payload=payload)
                async with SessionFactory() as session:
                    published = await session.get(ProcessingRun, new_id)
                    if published is None or published.status != "completed":
                        raise ValueError("Model pack publication did not complete")
            return new_id

        try:
            return await finish_writes(execute())
        except BaseException as error:
            await finish_writes(mark_failed(new_id, error))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        result = asyncio.run(backfill(args.run_id))
    except Exception as error:
        logger.error("Model pack backfill failed (%s)", type(error).__name__)
        raise SystemExit(1) from None
    if result is not None:
        logger.info("Published model pack backfill run %s", result)


if __name__ == "__main__":
    main()
