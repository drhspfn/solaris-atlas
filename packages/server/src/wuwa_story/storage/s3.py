import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from wuwa_story.config.settings import Settings
from wuwa_story.storage.base import ObjectInfo


class S3Storage:
    backend = "s3"

    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            region_name=settings.s3_region,
            use_ssl=settings.s3_use_ssl,
        )

    async def ensure_bucket(self) -> None:
        await asyncio.to_thread(self._ensure_bucket)

    def _ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status != 404:
                raise
            self.client.create_bucket(Bucket=self.bucket)

    async def put_file(
        self, source: Path, object_key: str, content_type: str | None = None
    ) -> ObjectInfo:
        extra: dict[str, Any] = {"ContentType": content_type} if content_type else {}
        await asyncio.to_thread(
            self.client.upload_file, str(source), self.bucket, object_key, ExtraArgs=extra
        )
        return await self.stat(object_key) or ObjectInfo(source.stat().st_size)

    async def get(self, object_key: str, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
        response = await asyncio.to_thread(
            self.client.get_object, Bucket=self.bucket, Key=object_key
        )
        body = response["Body"]
        try:
            while chunk := await asyncio.to_thread(body.read, chunk_size):
                yield chunk
        finally:
            await asyncio.to_thread(body.close)

    async def exists(self, object_key: str) -> bool:
        return await self.stat(object_key) is not None

    async def stat(self, object_key: str) -> ObjectInfo | None:
        try:
            result = await asyncio.to_thread(
                self.client.head_object, Bucket=self.bucket, Key=object_key
            )
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return None
            raise
        return ObjectInfo(result["ContentLength"], result.get("ETag", "").strip('"') or None)

    async def delete(self, object_key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=object_key)
