# Shared model animation packs

Each appearance keeps its geometry, materials, physics, facial curves and linked
objects. Its `animation_packs` manifest lists immutable GLBs and the exact clip
names applicable to that appearance. The viewer combines these lists; the legacy
`animations` field remains compatible with older publications.

New builds reuse clips only with compatible bone names, hierarchy and rest pose,
the same export recipe, source package and converted clip checksum. Rest matrices
allow the importer's existing absolute numerical tolerance of `1e-4`. Unknown or
different skeletons do not share packs. Skin folders are scoped to their owning
appearance and its authored baseline; explicit cross-folder references survive.

Completed legacy exports can be repackaged without Blender or client extraction:

```sh
python -m wuwa_story_worker.model_pack_backfill --run-id COMPLETED_SOURCE_RUN
```

The command locks the owner, verifies downloaded checksums, preserves independent
appearance data and publishes atomically as a new processing run. Original files
remain unchanged. It skips repeated or superseded publications. Legacy float-noise
reuse additionally requires matching raw source checksums and unambiguous sources;
different or unproven clip content stays separate. Validation failure leaves the
existing publication available.

Deploy API/web first. Do not restart a model worker with an active import. Drain
and verify it is idle before switching its image; repack completed runs instead of
force-enqueueing model imports. Existing consumers can serve legacy bundles while
the model worker finishes. No database migration is required.
