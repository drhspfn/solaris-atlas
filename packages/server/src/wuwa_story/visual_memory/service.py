"""Human-reviewed references, existing object storage and balanced entity retrieval.

The caller owns the transaction. Query images remain temporary; no remote URL is fetched.
"""

import asyncio
import hashlib
import math
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models import (
    EmbeddingModel,
    FileLocation,
    Node,
    VisualAppearance,
    VisualEmbedding,
    VisualIdentity,
    VisualIdentityNode,
    VisualLoreIdentity,
    VisualReference,
    VisualReferenceLink,
    VisualReview,
)
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService


def normalized_vector(values):
    vector = [float(value) for value in values]
    if not vector or len(vector) > 4096 or not all(math.isfinite(x) for x in vector):
        raise ValueError("Embedding must contain finite values and at most 4096 dimensions")
    norm = math.sqrt(sum(x * x for x in vector))
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError("Embedding must have a non-zero finite norm")
    return [x / norm for x in vector]


def rank_candidates(rows, limit=10):
    """At most five independent source images per identity; never sum sample counts."""
    groups = {}
    for row in sorted(rows, key=lambda item: (-item["similarity"], item["reference_id"])):
        group = groups.setdefault(row["identity_id"], [])
        if not any(
            item["source_sha256"] == row["source_sha256"]
            or (
                row.get("perceptual_hash") and item.get("perceptual_hash") == row["perceptual_hash"]
            )
            for item in group
        ):
            group.append(row)
    candidates = []
    for identity_id, evidence in groups.items():
        evidence = sorted(evidence, key=lambda x: (-x["similarity"], x["reference_id"]))[:5]
        best = evidence[0]
        scores = [x["similarity"] for x in evidence]
        mean = sum(scores) / len(scores)
        consistency = max(0.0, 1.0 - (max(scores) - min(scores)))
        # Heuristic similarity, deliberately not a calibrated identity probability.
        score = 0.65 * scores[0] + 0.25 * mean + 0.1 * consistency
        score *= 0.5 + 0.5 * max(item.get("quality", 1.0) for item in evidence)
        candidates.append(
            {
                "identity_id": identity_id,
                "name": best["name"],
                "kind": best["kind"],
                "lore_id": best.get("lore_id"),
                "appearance_id": best.get("appearance_id"),
                "score": score,
                "best_similarity": scores[0],
                "independent_references": len(evidence),
                "evidence": evidence,
            }
        )
    return sorted(candidates, key=lambda x: (-x["score"], x["identity_id"]))[:limit]


def identification_status(candidates):
    # Conservative, explicitly uncalibrated thresholds; never used to approve a label.
    if not candidates or candidates[0]["best_similarity"] < 0.5:
        return "unknown"
    if len(candidates) > 1 and candidates[0]["score"] - candidates[1]["score"] < 0.04:
        return "unresolved"
    return "candidate"


def _name(value, field="name", max_length=256):
    value = str(value).strip()
    if not value or len(value) > max_length:
        raise ValueError(f"{field} must contain 1–{max_length} characters")
    return value


def _url(value):
    if value is None:
        return None
    if (
        len(value) > 4096
        or urlsplit(value).scheme not in ("https", "http")
        or not urlsplit(value).hostname
    ):
        raise ValueError("Source links must be HTTP(S) URLs")
    return value


class VisualMemoryService:
    def __init__(self, session, storage=None, engine=None):
        self.session = session
        self.storage = storage if storage is not None else S3Storage(get_settings())
        self.engine = engine

    def _engine(self):
        if self.engine is None:
            from wuwa_story.visual_memory.inference import get_engine

            self.engine = get_engine()
        return self.engine

    async def _identity(self, value):
        identity = await self.session.get(VisualIdentity, value)
        if identity is None:
            raise ValueError("Visual identity not found")
        return identity

    async def _reference(self, value, lock=False):
        statement = select(VisualReference).where(VisualReference.id == value)
        if lock:
            statement = statement.with_for_update()
        reference = await self.session.scalar(statement)
        if reference is None:
            raise ValueError("Visual reference not found")
        return reference

    async def _audit(self, reference_id, action, actor_id=None, reason=None, **details):
        self.session.add(
            VisualReview(
                reference_id=reference_id,
                action=action,
                actor_id=actor_id,
                reason=reason,
                details=details,
            )
        )
        await self.session.flush()

    async def create_lore_identity(self, name):
        row = VisualLoreIdentity(name=_name(name))
        self.session.add(row)
        await self.session.flush()
        return {"id": row.id, "name": row.name}

    async def list_lore_identities(self):
        rows = (
            await self.session.scalars(select(VisualLoreIdentity).order_by(VisualLoreIdentity.name))
        ).all()
        return {"items": [{"id": x.id, "name": x.name} for x in rows], "total": len(rows)}

    async def _node_links(self, identity_id, node_ids):
        node_ids = sorted(set(node_ids))
        if len(node_ids) > 50:
            raise ValueError("At most 50 game entities per visual identity")
        found = set(
            (await self.session.scalars(select(Node.id).where(Node.id.in_(node_ids)))).all()
        )
        if found != set(node_ids):
            raise ValueError("A linked game entity does not exist")
        await self.session.execute(
            delete(VisualIdentityNode).where(VisualIdentityNode.identity_id == identity_id)
        )
        self.session.add_all(
            VisualIdentityNode(identity_id=identity_id, node_id=x) for x in node_ids
        )

    async def create_identity(self, name, kind, node_ids=None, lore_id=None, description=""):
        kind = _name(kind, "kind", 64)
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", kind):
            raise ValueError("kind must be a lowercase key, e.g. npc or custom_location")
        if lore_id is not None and await self.session.get(VisualLoreIdentity, lore_id) is None:
            raise ValueError("Lore identity not found")
        row = VisualIdentity(
            name=_name(name), kind=kind, lore_id=lore_id, description=description[:10000]
        )
        self.session.add(row)
        await self.session.flush()
        await self._node_links(row.id, node_ids or [])
        await self.session.flush()
        return await self._identity_dict(row)

    async def update_identity(self, identity_id, **changes):
        row = await self._identity(identity_id)
        if "name" in changes:
            row.name = _name(changes["name"])
        if "kind" in changes:
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", changes["kind"]):
                raise ValueError("Invalid kind")
            row.kind = changes["kind"]
        if "lore_id" in changes:
            lore_id = changes["lore_id"]
            if lore_id is not None and await self.session.get(VisualLoreIdentity, lore_id) is None:
                raise ValueError("Lore identity not found")
            row.lore_id = lore_id
        if "description" in changes:
            row.description = changes["description"][:10000]
        if "node_ids" in changes:
            await self._node_links(row.id, changes["node_ids"])
        await self.session.flush()
        return await self._identity_dict(row)

    async def _identity_dict(self, row):
        nodes = (
            await self.session.scalars(
                select(VisualIdentityNode.node_id).where(VisualIdentityNode.identity_id == row.id)
            )
        ).all()
        appearances = await self.list_appearances(row.id)
        return {
            "id": row.id,
            "name": row.name,
            "kind": row.kind,
            "description": row.description,
            "lore_id": row.lore_id,
            "node_ids": list(nodes),
            "appearances": appearances["items"],
        }

    async def list_identities(self, q="", kind=None, limit=50, offset=0):
        limit, offset = self._page(limit, offset)
        statement = select(VisualIdentity)
        if q:
            statement = statement.where(VisualIdentity.name.ilike(f"%{q[:256]}%"))
        if kind:
            statement = statement.where(VisualIdentity.kind == kind)
        total = await self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (
            await self.session.scalars(
                statement.order_by(VisualIdentity.name, VisualIdentity.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return {"items": [await self._identity_dict(x) for x in rows], "total": total}

    async def create_appearance(self, identity_id, name):
        await self._identity(identity_id)
        row = VisualAppearance(identity_id=identity_id, name=_name(name))
        self.session.add(row)
        await self.session.flush()
        return {"id": row.id, "identity_id": identity_id, "name": row.name}

    async def list_appearances(self, identity_id):
        rows = (
            await self.session.scalars(
                select(VisualAppearance)
                .where(VisualAppearance.identity_id == identity_id)
                .order_by(VisualAppearance.id)
            )
        ).all()
        return {
            "items": [{"id": x.id, "identity_id": x.identity_id, "name": x.name} for x in rows],
            "total": len(rows),
        }

    @staticmethod
    def _page(limit, offset):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Pagination requires limit 1–100 and offset >= 0")
        return limit, offset

    async def _embed(self, image, *, register=False):
        result = await asyncio.to_thread(self._engine().embed, image)
        vector = normalized_vector(result.vector)
        key = _name(result.model_key, "model_key", 128)
        config = dict(result.config)
        config["dimensions"] = len(vector)
        model = await self.session.scalar(select(EmbeddingModel).where(EmbeddingModel.key == key))
        if model is None:
            values = dict(
                key=key,
                provider="local_visual",
                model_name=config.get("model_name", key),
                dimensions=len(vector),
                distance_metric="cosine",
                config=config,
                active=True,
            )
            if not register:
                # Public tools and read-only agents must not mutate the model registry.
                return EmbeddingModel(**values), vector
            await self.session.execute(
                insert(EmbeddingModel)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[EmbeddingModel.key],
                )
            )
            model = await self.session.scalar(
                select(EmbeddingModel).where(EmbeddingModel.key == key)
            )
        if (
            model.dimensions != len(vector)
            or model.config != config
            or model.distance_metric != "cosine"
        ):
            raise ValueError(
                "Embedding model key was reused with incompatible weights or preprocessing"
            )
        return model, vector

    async def search(self, image_bytes, bbox=None, kind=None, limit=10):
        from wuwa_story.visual_memory.images import crop_image, load_image

        if not 1 <= limit <= 10:
            raise ValueError("Candidate limit must be 1–10")
        image = await asyncio.to_thread(load_image, image_bytes)
        crop = crop_image(image, bbox) if bbox else image
        model, vector = await self._embed(crop)
        return await self._search_vector(model, vector, kind, limit)

    async def _search_vector(self, model, vector, kind=None, limit=10):
        if model.id is None:
            return {
                "model_key": model.key,
                "status": "unknown",
                "candidates": [],
                "score_type": "uncalibrated_similarity",
                "retrieval": "balanced_exact_cosine",
            }
        # Exact balanced retrieval intentionally has no global image LIMIT: a popular
        # identity must not evict a rare NPC before entity aggregation. pgvector does
        # the distance calculation; SQL caps each independent source per identity.
        distance = VisualEmbedding.embedding.cosine_distance(vector)
        ranked = (
            select(
                VisualReference.id.label("reference_id"),
                VisualIdentity.id.label("identity_id"),
                VisualIdentity.name,
                VisualIdentity.kind,
                VisualIdentity.lore_id,
                VisualReferenceLink.appearance_id,
                VisualReference.source_sha256,
                VisualReference.perceptual_hash,
                VisualReference.bbox,
                VisualReference.source_type,
                VisualReference.quality,
                (1 - distance).label("similarity"),
                func.row_number()
                .over(
                    partition_by=(VisualIdentity.id, VisualReference.source_sha256),
                    order_by=(distance, VisualReference.id),
                )
                .label("source_rank"),
            )
            .select_from(VisualEmbedding)
            .join(VisualReference, VisualReference.id == VisualEmbedding.reference_id)
            .join(
                VisualReferenceLink,
                VisualReferenceLink.reference_id == VisualReference.id,
            )
            .join(VisualIdentity, VisualIdentity.id == VisualReferenceLink.identity_id)
            .where(
                VisualEmbedding.model_id == model.id,
                VisualEmbedding.dimensions == model.dimensions,
                VisualReference.status == "approved",
                VisualReference.enabled_for_search.is_(True),
                VisualReference.quality > 0,
            )
        )
        if kind:
            ranked = ranked.where(VisualIdentity.kind == kind)
        sources = ranked.subquery()
        independent = (
            select(
                *sources.c,
                func.row_number()
                .over(
                    partition_by=sources.c.identity_id,
                    order_by=(sources.c.similarity.desc(), sources.c.reference_id),
                )
                .label("entity_rank"),
            )
            .where(sources.c.source_rank == 1)
            .subquery()
        )
        rows = [
            dict(x)
            for x in (
                await self.session.execute(
                    select(independent)
                    .where(independent.c.entity_rank <= 5)
                    .order_by(independent.c.similarity.desc())
                )
            ).mappings()
        ]
        candidates = rank_candidates(rows, limit)
        for candidate in candidates:
            for item in candidate["evidence"]:
                reference = await self._reference(item["reference_id"])
                item["image_url"] = await self._image_url(reference)
                for internal in ("source_rank", "entity_rank", "source_sha256", "perceptual_hash"):
                    item.pop(internal, None)
        return {
            "model_key": model.key,
            "status": identification_status(candidates),
            "candidates": candidates,
            "score_type": "uncalibrated_similarity",
            "retrieval": "balanced_exact_cosine",
        }

    async def identify(self, image_bytes, boxes=None, detect=True, limit=10, kind=None):
        from wuwa_story.visual_memory.images import crop_image, load_image

        image = await asyncio.to_thread(load_image, image_bytes)
        if boxes is not None and len(boxes) > 20:
            raise ValueError("At most 20 boxes per image")
        detections = []
        if boxes is not None:
            detections = [{"bbox": box, "score": None, "status": "manual"} for box in boxes]
        elif detect:
            detections = await asyncio.to_thread(self._engine().detect, image)
        else:
            detections = [
                {"bbox": [0, 0, image.width, image.height], "score": None, "status": "whole_image"}
            ]
        result = []
        model_key = None
        for detection in detections[:20]:
            crop = crop_image(image, detection["bbox"])
            model, vector = await self._embed(crop)
            model_key = model.key
            found = await self._search_vector(model, vector, kind, limit)
            result.append(
                {
                    "bbox": detection["bbox"],
                    "detection": {
                        "score": detection.get("score"),
                        "status": detection.get("status", "detected"),
                    },
                    "identification": found,
                }
            )
        return {
            "model_key": model_key,
            "width": image.width,
            "height": image.height,
            "detections": result,
        }

    async def _links(self, reference_id, links):
        if not links or len(links) > 20:
            raise ValueError("A reference requires 1–20 explicit visual identity links")
        seen = set()
        for link in links:
            identity_id = int(link["identity_id"])
            await self._identity(identity_id)
            if identity_id in seen:
                raise ValueError("Duplicate identity link")
            seen.add(identity_id)
            appearance_id = link.get("appearance_id")
            if appearance_id is not None:
                appearance = await self.session.get(VisualAppearance, appearance_id)
                if appearance is None or appearance.identity_id != identity_id:
                    raise ValueError("Appearance does not belong to the selected visual identity")
        await self.session.execute(
            delete(VisualReferenceLink).where(VisualReferenceLink.reference_id == reference_id)
        )
        self.session.add_all(
            VisualReferenceLink(
                reference_id=reference_id,
                identity_id=int(x["identity_id"]),
                appearance_id=x.get("appearance_id"),
            )
            for x in links
        )
        await self.session.flush()

    async def _store_image(self, image, name, encoded=None):
        from wuwa_story.visual_memory.images import encode_png

        if encoded is None:
            encoded = await asyncio.to_thread(encode_png, image)
        with tempfile.TemporaryDirectory(prefix="visual-reference-") as directory:
            path = Path(directory) / name
            await asyncio.to_thread(path.write_bytes, encoded)
            row = await FileRegistrationService(self.storage).register_file(
                self.session,
                path,
                "image",
                mime_type="image/png",
            )
        return row.id

    async def add_reference(
        self,
        image_bytes,
        bbox=None,
        links=None,
        source_type="manual",
        source_url=None,
        source_page=None,
        rights=None,
        author=None,
        quality=1.0,
        status="pending",
        retention_mode="image",
        actor_id=None,
    ):
        from wuwa_story.visual_memory.images import crop_image, encode_png, load_image, phash

        if status not in ("pending", "approved") or retention_mode not in ("image", "vector_only"):
            raise ValueError("Invalid initial moderation or retention mode")
        if status == "approved" and actor_id is None:
            raise ValueError("Approval requires an authenticated human administrator")
        if not math.isfinite(quality) or not 0 <= quality <= 1:
            raise ValueError("Quality must be a finite number between 0 and 1")
        if author is not None and len(author) > 512:
            raise ValueError("Author must contain at most 512 characters")
        image = await asyncio.to_thread(load_image, image_bytes)
        bbox = bbox or [0, 0, image.width, image.height]
        crop = crop_image(image, bbox)
        encoded_crop = await asyncio.to_thread(encode_png, crop)
        digest = hashlib.sha256(encoded_crop).hexdigest()
        # Serialize concurrent identical uploads. Rejected images stay rejected, and
        # retrying an upload cannot silently replace labels or moderation decisions.
        await self.session.execute(select(func.pg_advisory_xact_lock(811, int(digest[:7], 16))))
        existing = await self.session.scalar(
            select(VisualReference).where(VisualReference.sha256 == digest)
        )
        if existing:
            return {**await self.get_reference(existing.id), "duplicate": True}
        source_type = _name(source_type, "source_type", 64)
        source_url, source_page = _url(source_url), _url(source_page)
        model, vector = await self._embed(crop, register=True)
        reference = VisualReference(
            source_sha256=hashlib.sha256(image_bytes).hexdigest(),
            sha256=digest,
            perceptual_hash=await asyncio.to_thread(phash, crop),
            width=image.width,
            height=image.height,
            bbox=bbox,
            source_type=source_type,
            source_url=source_url,
            source_page=source_page,
            rights=rights[:4096] if rights else None,
            author=author,
            quality=quality,
            status=status,
            enabled_for_search=status == "approved",
            retention_mode=retention_mode,
        )
        self.session.add(reference)
        await self.session.flush()
        await self._links(reference.id, links or [])
        if retention_mode == "image":
            reference.original_file_id = await self._store_image(image, "visual-original.png")
            reference.file_id = await self._store_image(crop, "visual-crop.png", encoded_crop)
        self.session.add(
            VisualEmbedding(
                reference_id=reference.id,
                model_id=model.id,
                dimensions=model.dimensions,
                embedding=vector,
            )
        )
        await self._audit(reference.id, "create", actor_id, links=links, status=status)
        return await self.get_reference(reference.id)

    async def _image_url(self, reference, original=False):
        file_id = reference.original_file_id if original else reference.file_id
        if not file_id:
            return None
        location = await self.session.scalar(
            select(FileLocation)
            .where(
                FileLocation.file_id == file_id,
                FileLocation.available.is_(True),
                FileLocation.backend == self.storage.backend,
                FileLocation.bucket == self.storage.bucket,
            )
            .order_by(FileLocation.is_primary.desc(), FileLocation.id)
        )
        if location is None:
            return None
        if hasattr(self.storage, "public_url"):
            return self.storage.public_url(location.object_key)
        suffix = "?original=true" if original else ""
        return f"/admin/visual-memory/references/{reference.id}/image{suffix}"

    async def get_image_bytes(self, reference_id, original=False):
        reference = await self._reference(reference_id)
        file_id = reference.original_file_id if original else reference.file_id
        location = (
            await self.session.scalar(
                select(FileLocation)
                .where(
                    FileLocation.file_id == file_id,
                    FileLocation.backend == self.storage.backend,
                    FileLocation.bucket == self.storage.bucket,
                    FileLocation.available.is_(True),
                )
                .order_by(FileLocation.is_primary.desc(), FileLocation.id)
            )
            if file_id
            else None
        )
        if location is None:
            raise ValueError("Reference image is not retained or is unavailable")
        result = bytearray()
        async for chunk in self.storage.get(location.object_key):
            result.extend(chunk)
            if len(result) > 80 * 1024 * 1024:
                raise ValueError("Stored image exceeds the decoded image limit")
        return bytes(result)

    async def get_reference(self, reference_id):
        row = await self._reference(reference_id)
        links = (
            await self.session.execute(
                select(VisualReferenceLink, VisualIdentity.name, VisualIdentity.kind)
                .join(
                    VisualIdentity,
                    VisualIdentity.id == VisualReferenceLink.identity_id,
                )
                .where(VisualReferenceLink.reference_id == row.id)
            )
        ).all()
        models = (
            await self.session.scalars(
                select(EmbeddingModel.key)
                .join(
                    VisualEmbedding,
                    VisualEmbedding.model_id == EmbeddingModel.id,
                )
                .where(VisualEmbedding.reference_id == row.id)
            )
        ).all()
        embedded_at = await self.session.scalar(
            select(func.max(VisualEmbedding.created_at)).where(
                VisualEmbedding.reference_id == row.id,
            )
        )
        return {
            "id": row.id,
            "image_url": await self._image_url(row),
            "original_url": await self._image_url(row, True),
            "sha256": row.sha256,
            "source_sha256": row.source_sha256,
            "perceptual_hash": row.perceptual_hash,
            "width": row.width,
            "height": row.height,
            "bbox": row.bbox,
            "source_type": row.source_type,
            "source_url": row.source_url,
            "source_page": row.source_page,
            "rights": row.rights,
            "author": row.author,
            "quality": row.quality,
            "status": row.status,
            "enabled_for_search": row.enabled_for_search,
            "retention_mode": row.retention_mode,
            "model_keys": list(models),
            "links": [
                {
                    "identity_id": x.identity_id,
                    "appearance_id": x.appearance_id,
                    "name": name,
                    "kind": kind,
                }
                for x, name, kind in links
            ],
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "embedded_at": embedded_at.isoformat() if embedded_at else None,
        }

    async def list_references(
        self,
        q="",
        kind=None,
        status=None,
        model_key=None,
        source_type=None,
        limit=20,
        offset=0,
        quality_min=None,
    ):
        limit, offset = self._page(limit, offset)
        statement = select(VisualReference.id)
        if quality_min is not None:
            if not math.isfinite(quality_min) or not 0 <= quality_min <= 1:
                raise ValueError("Minimum quality must be between 0 and 1")
            statement = statement.where(VisualReference.quality >= quality_min)
        if q:
            pattern = f"%{q[:256]}%"
            identities = (
                select(VisualReferenceLink.reference_id)
                .join(VisualIdentity, VisualIdentity.id == VisualReferenceLink.identity_id)
                .where(VisualIdentity.name.ilike(pattern))
            )
            statement = statement.where(
                or_(
                    VisualReference.id.in_(identities),
                    VisualReference.source_url.ilike(pattern),
                    VisualReference.source_page.ilike(pattern),
                    VisualReference.author.ilike(pattern),
                )
            )
        if kind:
            identities = (
                select(VisualReferenceLink.reference_id)
                .join(
                    VisualIdentity,
                    VisualIdentity.id == VisualReferenceLink.identity_id,
                )
                .where(VisualIdentity.kind == kind)
            )
            statement = statement.where(VisualReference.id.in_(identities))
        if status:
            statement = statement.where(VisualReference.status == status)
        if source_type:
            statement = statement.where(VisualReference.source_type == source_type)
        if model_key:
            statement = statement.where(
                VisualReference.id.in_(
                    select(VisualEmbedding.reference_id)
                    .join(EmbeddingModel, EmbeddingModel.id == VisualEmbedding.model_id)
                    .where(EmbeddingModel.key == model_key)
                )
            )
        total = await self.session.scalar(select(func.count()).select_from(statement.subquery()))
        ids = (
            await self.session.scalars(
                statement.order_by(VisualReference.id.desc()).limit(limit).offset(offset)
            )
        ).all()
        return {"items": [await self.get_reference(x) for x in ids], "total": total}

    async def link_reference(self, reference_id, links, actor_id=None):
        reference = await self._reference(reference_id, lock=True)
        await self._links(reference_id, links)
        # Changing the label revokes approval until it is explicitly reviewed again.
        reference.status, reference.enabled_for_search = "pending", False
        await self._audit(reference_id, "relink", actor_id, links=links)
        return await self.get_reference(reference_id)

    async def review_reference(
        self, reference_id, status, enabled_for_search=True, reason=None, actor_id=None
    ):
        if status not in ("pending", "approved", "rejected"):
            raise ValueError("Invalid moderation status")
        if actor_id is None:
            raise ValueError("Moderation requires an authenticated human administrator")
        row = await self._reference(reference_id, lock=True)
        row.status, row.enabled_for_search = status, status == "approved" and enabled_for_search
        await self._audit(
            reference_id,
            "review",
            actor_id,
            reason,
            status=status,
            enabled_for_search=row.enabled_for_search,
        )
        return await self.get_reference(reference_id)

    async def remove_reference(self, reference_id, mode="reject", reason=None, actor_id=None):
        if mode not in ("reject", "vector_only"):
            raise ValueError("Removal mode must be reject or vector_only")
        row = await self._reference(reference_id, lock=True)
        if mode == "reject":
            row.status, row.enabled_for_search = "rejected", False
        else:
            # Unlink only: shared content-addressed files may belong to game imports.
            # Physical cleanup is the storage GC's responsibility, never delete here.
            row.file_id = row.original_file_id = None
            row.retention_mode = "vector_only"
        await self._audit(reference_id, "remove", actor_id, reason, mode=mode)
        return await self.get_reference(reference_id)

    async def get_review_history(self, reference_id):
        rows = (
            await self.session.scalars(
                select(VisualReview)
                .where(VisualReview.reference_id == reference_id)
                .order_by(VisualReview.id.desc())
                .limit(100)
            )
        ).all()
        return {
            "items": [
                {
                    "id": x.id,
                    "action": x.action,
                    "actor_id": x.actor_id,
                    "reason": x.reason,
                    "details": x.details,
                    "created_at": x.created_at.isoformat(),
                }
                for x in rows
            ]
        }

    async def reindex(self, reference_ids, actor_id=None):
        from wuwa_story.visual_memory.images import load_image

        if not 1 <= len(reference_ids) <= 20:
            raise ValueError("Reindex requires 1–20 reference IDs")
        results = []
        for reference_id in dict.fromkeys(reference_ids):
            row = await self._reference(reference_id, lock=True)
            if row.file_id is None:
                results.append({"id": row.id, "status": "image_not_retained"})
                continue
            image = await asyncio.to_thread(
                load_image, await self.get_image_bytes(row.id), max_bytes=80 * 1024 * 1024
            )
            model, vector = await self._embed(image, register=True)
            statement = insert(VisualEmbedding).values(
                reference_id=row.id,
                model_id=model.id,
                dimensions=model.dimensions,
                embedding=vector,
            )
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=[VisualEmbedding.reference_id, VisualEmbedding.model_id],
                    set_={
                        "embedding": vector,
                        "dimensions": model.dimensions,
                        "created_at": func.now(),
                    },
                )
            )
            await self._audit(row.id, "reindex", actor_id, model_key=model.key)
            results.append({"id": row.id, "status": "indexed", "model_key": model.key})
        return {"items": results}
