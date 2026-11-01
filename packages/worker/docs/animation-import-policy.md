# Animation import eligibility

The importer records `animation_import` on event client metadata, source
archive manifests and published entity file-reference metadata. A discovered
skeleton or a successful CLI exit is not proof of a reproducible composition.

Current source policy:

* A complete, explicitly selected single Spine rig can be converted as a
  candidate. Its state is `awaiting_render_validation`, display `static`.
* Missing/failed dependencies, multiple rigs requiring composition controls,
  prefab/sequence-driven UI and unverified materials select `static_only` with
  machine-readable reason codes. Event imports keep queuing their images but
  do not publish a new event animation archive for these unsupported sources.
* Riglyph reports must contain empty losses and unsupported-feature lists and
  confirm consumption of the complete binary skeleton. Each accepted component
  retains its `conversion-report.json` in the resulting archive.
* Conversion failure selects `conversion_failed`, retains static display and
  source provenance, and makes the entity processing result partial with the
  actual error. No converted archive is published on failure.

Existing source archives are retained; this policy does not delete historical
research assets or their shared R2 objects. Story-source archives can still
hold authored controls for archival purposes, but are not advertised as an
animated preview. Public playback is gated by both the deployment setting and
an explicitly animated display decision; current source assessment never
grants that decision.

The separate future frame/video exporter must validate the actual composition
and rendered result before publishing an animated preview. No frame/video
export or automatic approval is implemented here. Unsupported compositions
must not become animated merely because the exporter returns a file.

Spine recipe 4 invalidates the old eligibility assumptions on a subsequent
media import, while reusing immutable client extraction caches. Reimporting
the same client event retains its decision; a new client catalog must be
assessed again.
