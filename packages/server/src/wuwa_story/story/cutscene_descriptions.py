"""Versioned public scene summaries, separate from quest analyses and visual reports."""

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.agents.cutscene_vision import visual_reference
from wuwa_story.db.models.content import Document, DocumentHead
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.storage import FileObject, FileReference
from wuwa_story.story.cutscene_publications import publication_order, publication_types


def description_type(release_id, *, manual=False):
    return f"cutscene-public{'-edit' if manual else ''}:{release_id}"


async def current_video(session, asset_id, asset_version=None):
    return await session.scalar(select(FileReference).where(
        FileReference.owner_node_id == asset_id,
        FileReference.reference_type.in_(publication_types("cutscene_video")),
        *([FileReference.metadata_json['asset_version'].astext ==
          asset_version] if asset_version else []),
    ).order_by(publication_order(FileReference.reference_type), FileReference.id.desc()).limit(1))


async def save_description(session, *, asset_id, release_id, locale_id, title, text,
                           source_hash, metadata, manual=False, expected_revision=None,
                           processor_run_id=None):
    """Caller owns transaction. Asset lock serializes revisions across quest jobs/editors."""
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise ValueError('Public title must contain 1–120 characters')
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2500:
        raise ValueError('Public description must contain 1–2500 characters')
    if await session.scalar(select(Node.id).where(Node.id == asset_id).with_for_update()) is None:
        raise ValueError('Scene asset not found')
    kind = description_type(release_id, manual=manual)
    latest = await session.scalar(select(Document).join(
        DocumentHead, DocumentHead.document_id == Document.id).where(
        DocumentHead.node_id == asset_id, DocumentHead.locale_id == locale_id,
        DocumentHead.document_type == kind))
    revision = latest.revision if latest else 0
    if manual and expected_revision != revision:
        raise ValueError('Description changed; reload before saving')
    public = {key: metadata[key] for key in (
        'visual_reference_id', 'chapters', 'observation_indices', 'asset_version', 'source_document_id',
    ) if key in metadata}
    if latest and latest.source_hash == source_hash and latest.title == title.strip() \
            and latest.plain_text == text.strip() and all(latest.metadata_json.get(k) == v for k, v in public.items()):
        return latest
    number = (await session.scalar(select(func.max(Document.revision)).where(
        Document.node_id == asset_id, Document.locale_id == locale_id, Document.document_type == kind)) or 0) + 1
    document = Document(node_id=asset_id, locale_id=locale_id, document_type=kind,
                        revision=number, title=title.strip(), plain_text=text.strip(), body_ast=[],
                        source_hash=source_hash, processor_run_id=processor_run_id,
                        metadata_json={**public, 'release_id': release_id, 'generated': not manual})
    session.add(document)
    await session.flush()
    await session.execute(insert(DocumentHead).values(
        node_id=asset_id, locale_id=locale_id, document_type=kind, document_id=document.id,
    ).on_conflict_do_update(constraint='uq_document_head_identity', set_={'document_id': document.id}))
    return document


async def publish_descriptions(session, entries, *, release_id, locale_id, source_document_id, processor_run_id):
    for entry in sorted(entries, key=lambda item: item.asset_node_id):
        report = await session.get(FileReference, entry.visual_reference_id)
        if report is None or report.owner_node_id != entry.asset_node_id:
            raise ValueError(
                'Public description does not match its visual report')
        video = await current_video(session, entry.asset_node_id, report.metadata_json.get('asset_version'))
        current = await visual_reference(session, entry.asset_node_id, report.metadata_json.get('asset_version'))
        file = await session.get(FileObject, video.file_id) if video else None
        if current is None or current.id != report.id or file is None or not file.sha256:
            raise ValueError(
                'Public description requires a current complete visual report')
        await save_description(session, asset_id=entry.asset_node_id, release_id=release_id,
                               locale_id=locale_id, title=entry.title, text=entry.text, source_hash=file.sha256,
                               metadata={**entry.model_dump(), 'asset_version': report.metadata_json.get('asset_version'),
                                         'source_document_id': source_document_id}, processor_run_id=processor_run_id)


async def edit_public_description(session, *, asset_id, release_id, locale_id, title, text, expected_revision):
    """Admin caller must verify this asset belongs to the selected scene/snapshot."""
    video = await current_video(session, asset_id)
    file = await session.get(FileObject, video.file_id) if video else None
    if file is None or not file.sha256:
        raise ValueError(
            'A published video is required for a public description')
    return await save_description(session, asset_id=asset_id, release_id=release_id, locale_id=locale_id,
                                  title=title, text=text, source_hash=file.sha256,
                                  metadata={'asset_version': video.metadata_json.get(
                                      'asset_version')},
                                  manual=True, expected_revision=expected_revision)


async def clear_manual_description(session, *, asset_id, release_id, locale_id, expected_revision):
    """Restore generated fallback; all prior manual revisions remain in Document."""
    await session.scalar(select(Node.id).where(Node.id == asset_id).with_for_update())
    kind = description_type(release_id, manual=True)
    document = await session.scalar(select(Document).join(DocumentHead, DocumentHead.document_id == Document.id).where(
        DocumentHead.node_id == asset_id, DocumentHead.locale_id == locale_id, DocumentHead.document_type == kind))
    if (document.revision if document else 0) != expected_revision:
        raise ValueError('Description changed; reload before clearing')
    await session.execute(delete(DocumentHead).where(
        DocumentHead.node_id == asset_id, DocumentHead.locale_id == locale_id, DocumentHead.document_type == kind))


async def public_descriptions(session, asset_ids, release_id, locale, *, asset_versions=None):
    if not asset_ids:
        return {}
    kinds = [description_type(release_id, manual=True),
             description_type(release_id)]
    documents = list(await session.scalars(select(Document).join(
        DocumentHead, DocumentHead.document_id == Document.id).join(Locale, Locale.id == Document.locale_id).where(
        DocumentHead.node_id.in_(asset_ids), DocumentHead.document_type.in_(kinds), Locale.code == locale)))
    result = {}
    for asset_id in sorted(set(asset_ids)):
        candidates = [d for d in documents if d.node_id == asset_id]
        if not candidates:
            continue
        video = await current_video(session, asset_id, (asset_versions or {}).get(asset_id))
        file = await session.get(FileObject, video.file_id) if video else None
        if file is None or not file.sha256:
            continue
        for document in sorted(candidates, key=lambda d: (d.document_type == kinds[0], d.revision), reverse=True):
            if document.source_hash != file.sha256:
                continue
            meta = document.metadata_json
            result[asset_id] = {
                'asset_node_id': asset_id, 'title': document.title, 'text': document.plain_text,
                'chapters': meta.get('chapters', []), 'observation_indices': meta.get('observation_indices', []),
                'visual_reference_id': meta.get('visual_reference_id'), 'document_id': document.id,
                'revision': document.revision, 'locale': locale,
                'origin': 'manual' if document.document_type == kinds[0] else 'analysis',
            }
            break
    return result


async def attach_public_descriptions(session, flows, release_id, locale):
    media = [item for flow in flows.values() if flow for item in (flow.get('media') or {}).values()
             if item.get('asset_node_id')]
    entries = await public_descriptions(session, [m['asset_node_id'] for m in media], release_id, locale,
                                        asset_versions={m['asset_node_id']: m.get('asset_version') for m in media})
    for item in media:
        if item['asset_node_id'] in entries:
            item['description'] = entries[item['asset_node_id']]
        elif item.get('description'):
            legacy = item['description']
            report = await visual_reference(session, item['asset_node_id'], item.get('asset_version'))
            if (report is None or report.id != legacy.get('visual_reference_id')
                    or (legacy.get('locale') != locale and locale != 'en')):
                item.pop('description', None)
            else:
                entries[item['asset_node_id']] = {**legacy, 'locale': locale, 'origin': 'analysis'}
    return list(entries.values())
