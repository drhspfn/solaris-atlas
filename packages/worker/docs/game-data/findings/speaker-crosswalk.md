# Speaker crosswalk investigation

Display-name equality alone is not used to connect a speaker to a character. The 3.6.0 snapshot contains 6,624 speaker records, 152 RoleInfo character records, and 94 NPC records. Speaker IDs and RoleInfo IDs are separate namespaces.

A deterministic exact-resource crosswalk is now emitted only when at least two distinct fields among `HeadIconAsset`, `HeadRoundIconAsset`, `HeadRoundIconAssetMaleVariant`, and `RolePileIconAsset` exactly match RoleInfo portrait/card fields, and every accepted path resolves to the same single `RoleInfo.Id`. The resulting graph edge is `speaker -> references_character -> character`, with `exact_join` basis and the original speaker row/path plus the matched asset paths retained as provenance. Ambiguous paths and name-only matches remain unresolved.

For Denia, `speaker:200144` and `speaker:250055` each match `RoleInfo.Id=1211` through the same dedicated `HeadIconAsset` and `RolePileIconAsset` paths. `speaker:200172` is not linked: its asset fields do not provide two unambiguous exact matches to RoleInfo 1211.

The full 3.6.0 rebuild emitted 27 exact-resource speaker-to-character edges under this conservative rule. This is a deterministic identity crosswalk, not a claim that each dialogue line in every quest uses the same speaker ID.
