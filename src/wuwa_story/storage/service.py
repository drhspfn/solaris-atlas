import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.storage import (
    FileLocation,
    FileObject,
    FileReference,
    FileType,
    FileVariant,
)
from wuwa_story.storage.base import ObjectStorage
from wuwa_story.storage.hashing import content_object_key, sha256_file


class FileRegistrationService:
    def __init__(self, storage: ObjectStorage) -> None:
        self.storage = storage

    async def register_file(
        self,
        session: AsyncSession,
        path: Path,
        file_type: str,
        logical_name: str | None = None,
        mime_type: str | None = None,
    ) -> FileObject:
        digest, size = await asyncio.to_thread(sha256_file, path)
        kind = await session.scalar(select(FileType).where(FileType.key == file_type))
        if kind is None:
            raise ValueError(f"Unknown storage file type: {file_type}")
        file_obj = await session.scalar(select(FileObject).where(FileObject.sha256 == digest))
        if file_obj is None:
            statement = (
                insert(FileObject)
                .values(
                    file_type_id=kind.id,
                    logical_name=logical_name or path.name,
                    mime_type=mime_type,
                    size_bytes=size,
                    sha256=digest,
                )
                .on_conflict_do_nothing(index_elements=[FileObject.sha256])
                .returning(FileObject.id)
            )
            inserted_id = await session.scalar(statement)
            if inserted_id is not None:
                file_obj = await session.get(FileObject, inserted_id)
            else:
                file_obj = await session.scalar(
                    select(FileObject).where(FileObject.sha256 == digest)
                )
            if file_obj is None:
                raise RuntimeError(
                    "File object insert conflicted but the existing row was not found"
                )
        elif file_obj.size_bytes != size or file_obj.file_type_id != kind.id:
            raise ValueError("Existing SHA-256 identity has incompatible size or file type")
        key = content_object_key(digest)
        stored = await self.storage.stat(key)
        if stored is None:
            stored = await self.storage.put_file(path, key, mime_type)
        if stored.size_bytes != size:
            raise OSError("Stored object size does not match the hashed source")
        location = await session.scalar(
            select(FileLocation).where(
                FileLocation.backend == self.storage.backend,
                FileLocation.bucket == self.storage.bucket,
                FileLocation.object_key == key,
            )
        )
        if location is None:
            location_statement = (
                insert(FileLocation)
                .values(
                    file_id=file_obj.id,
                    backend=self.storage.backend,
                    bucket=self.storage.bucket,
                    object_key=key,
                    is_primary=True,
                    available=True,
                    etag=stored.etag,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        FileLocation.backend,
                        FileLocation.bucket,
                        FileLocation.object_key,
                    ]
                )
            )
            await session.execute(location_statement)
            location = await session.scalar(
                select(FileLocation).where(
                    FileLocation.backend == self.storage.backend,
                    FileLocation.bucket == self.storage.bucket,
                    FileLocation.object_key == key,
                )
            )
            if location is None:
                raise RuntimeError("File location was not registered")
        elif location.file_id != file_obj.id:
            raise ValueError(
                "A physical storage key is already assigned to a different file identity"
            )
        return file_obj

    async def register_reference(
        self,
        session: AsyncSession,
        *,
        file_id: int | None,
        reference_type: str,
        release_id: int | None = None,
        owner_node_id: int | None = None,
        source_name: str | None = None,
        source_path: str | None = None,
        source_record_id: int | None = None,
    ) -> FileReference:
        """Register metadata identity; source_path is game/upstream metadata, never object storage."""
        reference = FileReference(
            release_id=release_id,
            owner_node_id=owner_node_id,
            file_id=file_id,
            reference_type=reference_type,
            source_name=source_name,
            source_path=source_path,
            source_record_id=source_record_id,
        )
        session.add(reference)
        await session.flush()
        return reference

    async def register_variant(
        self,
        session: AsyncSession,
        parent_file_id: int,
        child_file_id: int,
        variant_type: str,
        processor_run_id: int | None = None,
    ) -> FileVariant:
        variant = await session.get(FileVariant, (parent_file_id, child_file_id, variant_type))
        if variant is None:
            variant = FileVariant(
                parent_file_id=parent_file_id,
                child_file_id=child_file_id,
                variant_type=variant_type,
                processor_run_id=processor_run_id,
            )
            session.add(variant)
            await session.flush()
        return variant
