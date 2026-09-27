# Storage contract

`FileObject` identifies bytes by SHA-256 and size; the canonical key is `objects/<first-byte>/<second-byte>/<full-sha256>`. `FileLocation` records backend, bucket, key, ETag, and whether the location is primary. `FileVariant` links derived forms to an original. `FileReference` links an object to source/canonical context.

`StorageBackend` exposes async put, get, exists, stat, and delete operations. `LocalStorage` writes atomically via a temporary sibling file and rejects absolute paths and `..` traversal. `S3Storage` wraps blocking boto3 operations in worker threads. The service records object metadata separately from physical bytes, supports multiple locations, and keeps `source_path` distinct from the physical `object_key`.

Local Compose uses community-built MinIO CE images from GHCR because the previously configured Docker Hub references are no longer pullable in this environment. Treat this image choice as a development dependency and pin/review a trusted artifact before production. Actual game asset extraction/decryption and bulk object upload are not part of the foundation importer.
