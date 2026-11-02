# Character trial verification: Chixia

Verified against the server's pinned 3.7.0 client, 2026-11-01.

* ConfigDB `db_activity.db` and `db_RoleTrial.db` both resolve RoleId 1202 to
  `/Game/Aki/UI/UIResources/Common/Spine/Portraits/Portraits_Chixia/Portraits_ChiXia`.
  The identical aliases were checked rather than treated as two characters.
* The shared worker `build_spine_bundle` extracted a portable archive with the
  binary skeleton, atlas, pages and original cooked packages: 3,780,987 bytes.
* Published `@riglyph/cli` 0.15.1 converted the actual archive: source version
  4.1.23, 87,375 skeleton bytes consumed, no reported losses or unsupported
  features. The resulting portable archive is 2,767,660 bytes. Numerical and
  visual parity remain unverified by the engine, as its report states.
* Both archives were registered using the existing FileRegistrationService,
  linked as `riglyph_bundle`, and HEAD-verified in the configured R2 bucket.
  The exact character node is 15497 (`character:1202`), reference 606192,
  source file 565723, converted file 565724, release 21.
* `STORY_SPINE_PLAYBACK_ENABLED` on the running API was confirmed false.

This is an actual client-extraction/conversion/storage smoke test, not a
browser-rendering check or an end-to-end broker delivery test. The normal
target planner is covered separately by a regression test for persisted
Character.trial_presentation metadata. Its change still requires deployment.
The extraction smoke test used an isolated diagnostic container with 4 GiB;
the default shared image-worker's 2 GiB run had previously failed extraction.
Do not infer all-character coverage or visual correctness from this one case.
