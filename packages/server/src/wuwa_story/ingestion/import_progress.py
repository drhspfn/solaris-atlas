"""Durable phase checkpoints for a pinned compiled snapshot."""

import hashlib
import logging
from time import monotonic

from sqlalchemy import select

from wuwa_story.db.models.ops import ImportRun

logger = logging.getLogger(__name__)


class ImportProgress:
    def __init__(self, session, run, stages):
        self.session, self.run, self.stages = session, run, stages

    @classmethod
    async def start(cls, session, run, manifest_bytes):
        fingerprint = hashlib.sha256(manifest_bytes).hexdigest()
        previous = await session.scalar(
            select(ImportRun)
            .where(
                ImportRun.release_id == run.release_id,
                ImportRun.importer == run.importer,
                ImportRun.importer_version == run.importer_version,
                ImportRun.id != run.id,
                ImportRun.metadata_json["source_fingerprint"].astext == fingerprint,
            )
            .order_by(ImportRun.id.desc()).limit(1)
        )
        stages = {
            name: {**stage, "counts": (stage["counts"][0], 0)}
            for name, stage in (previous.metadata_json or {}).get("stages", {}).items()
        } if previous else {}
        run.metadata_json = {**(run.metadata_json or {}),
                             "source_fingerprint": fingerprint, "stages": stages}
        await session.commit()
        return cls(session, run, stages)

    async def phase(self, name, operation):
        if name in self.stages:
            logger.info("snapshot.phase_reused run=%s phase=%s", self.run.id, name)
            return tuple(self.stages[name]["counts"])
        started = monotonic()
        logger.info("snapshot.phase_started run=%s phase=%s", self.run.id, name)
        counts = tuple(await operation())
        elapsed = round(monotonic() - started, 3)
        self.stages[name] = {"counts": counts, "seconds": elapsed}
        self.run.metadata_json = {**self.run.metadata_json, "stages": dict(self.stages)}
        self.run.records_seen = sum(stage["counts"][0] for stage in self.stages.values())
        self.run.records_created = sum(stage["counts"][1] for stage in self.stages.values())
        await self.session.commit()
        logger.info("snapshot.phase_completed run=%s phase=%s seconds=%s counts=%s",
                    self.run.id, name, elapsed, counts)
        return counts
