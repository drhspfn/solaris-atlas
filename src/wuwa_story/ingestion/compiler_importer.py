import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.ops import ImportRun
from wuwa_story.ingestion.canonical import CanonicalRecordAdapter, detect_compiled_dataset
from wuwa_story.ingestion.canonical_import import import_canonical_edges, import_canonical_entities
from wuwa_story.ingestion.context import ImportSummary
from wuwa_story.ingestion.importer import Importer
from wuwa_story.ingestion.localization import import_localization_batch
from wuwa_story.ingestion.raw import import_raw_snapshot
from wuwa_story.ingestion.releases import register_release, start_import_run


class CompiledDatasetImporter(Importer):
    key = "wuwa-deterministic-compiler"
    version = "0.1.0"

    def detect(self, source: Path) -> bool:
        return detect_compiled_dataset(source)

    async def import_release(
        self, source: Path, session: AsyncSession, batch_size: int = 1000
    ) -> ImportSummary:
        if not self.detect(source):
            raise ValueError(f"Not a compiled WuWa dataset: {source}")
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        release = await register_release(
            session,
            game_version=manifest["game_version"],
            resource_version=manifest.get("resource_version"),
            upstream_name=manifest["source_repository"],
            upstream_commit=manifest.get("source_commit"),
            metadata={
                "schema_version": manifest.get("schema_version"),
                "schema_inventory_hash": manifest.get("schema_inventory_hash"),
                "extraction_timestamp": manifest.get("extraction_timestamp"),
            },
        )
        run = await start_import_run(
            session, release.id, self.key, self.version, {"source_path": str(source)}
        )
        run.status = "running"
        run_id = run.id
        await session.commit()
        seen = created = 0
        try:
            raw_seen, raw_created = await import_raw_snapshot(
                source, session, release.id, batch_size
            )
            seen += raw_seen
            created += raw_created
            localization_seen, localization_created = await self._import_localization(
                source / "localization" / "all-locales.jsonl",
                session,
                release.id,
                min(batch_size, 250),
            )
            seen += localization_seen
            created += localization_created
            adapter = CanonicalRecordAdapter(source)
            entity_count = await import_canonical_entities(
                adapter,
                session,
                release.id,
                release.sequence,
                batch_size,
            )
            edge_count = await import_canonical_edges(adapter, session, release.id, batch_size)
            seen += entity_count + edge_count
            created += entity_count + edge_count
            run = await session.get(ImportRun, run_id)
            assert run is not None
            run.status = "succeeded"
            run.finished_at = datetime.now(UTC)
            run.records_seen = seen
            run.records_created = created
            run.records_updated = 0
            await session.commit()
            return ImportSummary(release.id, run_id, seen, created, 0, "succeeded")
        except Exception as exc:
            await session.rollback()
            run = await session.get(ImportRun, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = datetime.now(UTC)
                run.records_seen = seen
                run.records_created = created
                run.records_failed += 1
                run.error = f"{type(exc).__name__}: {exc}"
                await session.commit()
            raise

    @staticmethod
    async def _import_localization(
        path: Path, session: AsyncSession, release_id: int, batch_size: int
    ) -> tuple[int, int]:
        if not path.is_file():
            raise FileNotFoundError(f"Compiler localization output is missing: {path}")
        seen = created = 0
        batch: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid aggregate localization at {path}:{line_number}"
                    ) from exc
                if not isinstance(row, dict) or not (
                    isinstance(row.get("key"), str)
                    or (isinstance(row.get("key"), int) and not isinstance(row.get("key"), bool))
                ):
                    raise ValueError(f"Localization identity is malformed at {path}:{line_number}")
                batch.append(row)
                if len(batch) >= batch_size:
                    inserted_keys, inserted_values = await import_localization_batch(
                        session, release_id, batch
                    )
                    seen += len(batch) + sum(len(row.get("values_by_locale", {})) for row in batch)
                    created += inserted_keys + inserted_values
                    await session.commit()
                    batch.clear()
        if batch:
            inserted_keys, inserted_values = await import_localization_batch(
                session, release_id, batch
            )
            seen += len(batch) + sum(len(row.get("values_by_locale", {})) for row in batch)
            created += inserted_keys + inserted_values
            await session.commit()
        return seen, created
