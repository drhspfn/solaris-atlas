"""Follow authored UI prefab/material references to their event textures."""

import asyncio
import json
import os
import subprocess
from pathlib import Path

from sqlalchemy import select
from wuwa_story.db.models.game_events import GameEvent
from wuwa_story.ingestion.game_events import CLIENT_SOURCE
from wuwa_story.ingestion.release_media import enqueue_event_images

from wuwa_story_worker.archive_index import ensure_archive_index, indexed_package_paths
from wuwa_story_worker.asset_export import _sha256, export_assets
from wuwa_story_worker.client_assets import workspace_lock
from wuwa_story_worker.event_catalog import texture_path
from wuwa_story_worker.tooling import tool_path

IMAGE_TYPES = {"Texture2D", "LGUITexturePackerSpriteData"}
DEPENDENCY_TYPES = {"PrefabAsset", "Material", "MaterialInstanceConstant", "SpineAtlasAsset",
                    "SpineSkeletonDataAsset"}
SEQUENCE_TYPES = {'SequenceData', 'LevelSequence',
                  'BP_SequenceData_C', 'BP_SequenceData_Generated_C'}


async def annotate_image_packages(root: Path, records: dict) -> list[dict]:
    expected = {image["path"][6:] for record in records.values()
                for image in record.get("artwork", [])}
    if not expected:
        return []
    index = await ensure_archive_index(root, tool_path("WUWA_FMODEL_PATH"), root)
    packages = await asyncio.to_thread(indexed_package_paths, index, expected)
    missing = {}
    for record in records.values():
        for image in record.get("artwork", []):
            path = image["path"]
            present = path[6:] in packages
            image["package_status"] = "present" if present else "not_in_client"
            if not present:
                missing[path] = {"expected": path,
                                 "status": "not_in_client", "matches": []}
    return list(missing.values())


def ui_references(value, *, include_sequences=False):
    """Yield typed references, excluding script classes, audio and gameplay assets."""
    if isinstance(value, dict):
        if include_sequences and value.get('Token') == 'EX_Context':
            receiver = value.get('ObjectExpression') or {}
            variable = receiver.get('Variable') or {}
            actor_class = (variable.get('Property') or {}).get('PropertyClass')
            if isinstance(actor_class, dict):
                raw_class = actor_class.get('ObjectPath', '')
                if not isinstance(raw_class, str):
                    raw_class = ''
                if raw_class.startswith('Client/Content/'):
                    raw_class = '/Game/' + raw_class.removeprefix('Client/Content/')
                if raw_class.startswith('/Game/') and (path := texture_path(raw_class)):
                    yield 'dependency', path
        raw = value.get("ObjectPath") or value.get("AssetPathName")
        if isinstance(raw, str) and raw.startswith('Client/Content/'):
            raw = '/Game/' + raw.removeprefix('Client/Content/')
        kind = value.get("ObjectName", "").partition("'")[0]
        path = texture_path(raw) if isinstance(raw, str) else None
        if path:
            if kind in IMAGE_TYPES or (not kind and any(p in path for p in ("/Image/", "/Atlas/"))):
                yield "image", path
            elif kind in DEPENDENCY_TYPES or (include_sequences and kind in SEQUENCE_TYPES) or (not kind and "/Prefabs/" in path):
                yield "dependency", path
        for child in value.values():
            yield from ui_references(child, include_sequences=include_sequences)
    elif isinstance(value, list):
        for child in value:
            yield from ui_references(child, include_sequences=include_sequences)


async def prefab_images(root: Path, paths: list[str], *, assets: dict | None = None, include_sequences=False):
    pending = set(paths)
    seen = set()
    images = {}
    missing = []
    fmodel, converter = tool_path("WUWA_FMODEL_PATH"), tool_path(
        "WUWA_TEXTURE_CONVERTER_PATH")
    index = await ensure_archive_index(root, fmodel, root) if pending else None
    while pending:
        source = min(pending)
        pending.remove(source)
        if source in seen:
            continue
        seen.add(source)
        if len(seen) > 5000:
            raise ValueError(
                "Event UI dependency graph exceeded 5000 packages; inspect its references")
        try:
            available = await asyncio.to_thread(indexed_package_paths, index, [source[6:]])
            if source[6:] not in available:
                missing.append(
                    {"expected": source, "status": "not_in_client", "matches": []})
                continue
            package = available[source[6:]]
            receipt = await export_assets(root, fmodel, package.rsplit(".", 1)[0])
            raw = receipt.parent / "files"
            output = receipt.parent / \
                (("ui-json-sequences-v2-" if include_sequences else "ui-json-references-v2-") +
                 _sha256(converter)[:16])
            output.mkdir(exist_ok=True)
            complete = output / "references.json"
            if assets is not None and include_sequences:
                # A converter may not know the cooked controller class yet. Its
                # original bytes are still essential for a later score rebuild.
                assets[source] = {'raw': raw, 'decoded': output, 'package': package,
                                  'decode_failed': True}

            def decode(raw=raw, output=output, complete=complete, package=package):
                with workspace_lock(output):
                    if complete.is_file():
                        return json.loads(complete.read_text(encoding="utf-8"))
                    packages = output / "packages.txt"
                    paths = [raw / package]
                    if not paths[0].is_file():
                        raise ValueError(
                            "Indexed prefab package was not exported")
                    with (output / "converter.log").open("w", encoding="utf-8") as log:
                        for offset in range(0, len(paths), 8):
                            packages.write_text("\n".join(raw.name + "/" + p.relative_to(raw).as_posix()
                                                          for p in paths[offset:offset + 8]), encoding="utf-8")
                            subprocess.run([str(converter), "-i", str(raw), "-g", "GAME_WutheringWaves",
                                            "-c", str(packages), "-f", "json", "-o", str(output), "-y"],
                                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1800,
                                           env={**os.environ, "DOTNET_PROCESSOR_COUNT": "2",
                                                "WUWA_READ_SCRIPT_DATA": "1" if include_sequences else "0"})
                    references = sorted({ref for path in output.rglob("*.json") if path != complete
                                         for ref in ui_references(json.loads(path.read_text(encoding="utf-8-sig")),
                                                                  include_sequences=include_sequences)})
                    if not list(output.rglob("*.json")):
                        raise ValueError("Prefab converter produced no JSON")
                    temporary = complete.with_suffix(".partial")
                    temporary.write_text(json.dumps(
                        references), encoding="utf-8")
                    temporary.replace(complete)
                    return references
            references = await asyncio.to_thread(decode)
            if assets is not None:
                assets[source] = {'raw': raw,
                                  'decoded': output, 'package': package}
            for kind, path in references:
                if kind == "image":
                    images.setdefault(
                        path, {"path": path, "role": "prefab_texture", "prefab": source})
                elif path not in seen:
                    pending.add(path)
        except BlockingIOError:
            raise
        except (RuntimeError, ValueError, subprocess.SubprocessError) as error:
            # Preserve the successful branches and the exact failed package.
            missing.append(
                {"expected": source, "status": "dependency_failed", "error": str(error)[:500]})
    return list(images.values()), missing


async def event_resources(payload, parent, session, root: Path):
    events = list(await session.scalars(select(GameEvent).where(
        GameEvent.source == CLIENT_SOURCE, GameEvent.source_id.in_(payload["targets"]))))
    # expire_on_commit=False retains the snapshot without holding read locks
    # throughout potentially lengthy prefab exports.
    await session.commit()
    paths = set()
    entries = []
    for event in events:
        client = (event.source_data or {}).get("client", {})
        if client.get("download_id") != parent["download_id"]:
            raise ValueError(
                "Event UI dependency request belongs to another client catalog")
        prefabs = sorted({resource["path"] for resource in client.get("resources", [])
                          if "/Prefabs/" in resource["path"]})
        artwork, missing = await prefab_images(root, prefabs)
        await session.refresh(event, with_for_update=True)
        client = (event.source_data or {}).get("client", {})
        if client.get("download_id") != parent["download_id"]:
            raise ValueError(
                "A newer client catalog replaced this event during UI extraction")
        event.source_data = {**event.source_data, "client": {**client, "prefab_artwork": artwork,
                                                             "missing_resources": missing}}
        await session.commit()
        paths.update(image["path"] for image in artwork)
        entries.extend(missing)
    await session.commit()
    jobs = await enqueue_event_images(session, payload, sorted(paths))
    client = {key: parent[key]
              for key in ("asset_version", "download_id", "tier")}
    return {**client, "stage": "event_resources_ready", "events": len(events),
            "images": len(paths), "image_tasks": jobs, "media_report": {"entries": entries},
            "missing": len(entries)}, "partial" if entries else "completed"
