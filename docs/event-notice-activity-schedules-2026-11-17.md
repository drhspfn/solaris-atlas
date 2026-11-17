# Version 3.8 activity schedule evidence

The official Notice importer extracts individually dated patch-note sections
only when their titles uniquely identify a limited client activity. It also
joins trial notices through same-release client source records. Update-relative
starts remain unknown; literal server-specific timestamps are preserved. A
matching dedicated notice takes precedence over a patch-note section, and an
existing occurrence with the same end is not overwritten by the patch section.

Production read-only observations for release 22 (`3.8.0`):

- `activity:100100026` (event 1942), Gifts of Bygone Dreams, already has the
  official notice 50631 and a dated occurrence. Its existing calculated start
  predates this change and is not an observed maintenance completion.
- `activity:103500006` (event 1940), Glamour Couture, links through
  `RoleSkinTrialActivity.RoleIdList` to skin trial 81011108, then through
  `RoleSkinTrialInfo.PreviewSkinId` and `RoleSkin.Id` to Hiyuki / Lune Noire.
  Notice 50633 explicitly gives its tryout end as December 21 11:59 server
  time. The outfit sale ends at a different time; the outfit is permanent.
- `activity:100900046` (event 1938), Ascendant Aces, links through
  `RoleTrialActivity.RoleIdList` to `RoleTrialInfo` 1185 / `GachaId` 100090,
  the unique Lily banner. Notice 50623 states that featured resonators receive
  trials during that banner, which ends December 3 09:59 server time. Its
  roster also includes 1186 / 100091 and 1187 / 100092. Repeated banner
  identities are not guessed.
- Notice 50649 has separately dated sections for `activity:109100002`
  (Lahai-Roi Blocks: Turnabout), `activity:101500010` (Lollo Campaign:
  Groundwork), `activity:112000001` (Dreamfield Odyssey), and
  `activity:100100027` (Whither the Wind). These are not all active on the
  observation date; the literal dates distinguish future events.
- `activity:103400016` (event 1936) is Tactical Hologram: Simulation. The
  patch notice identifies Calamity Effigy as a new challenge but gives no
  activity-listing window. It does not establish an end to the permanent
  challenge. No promotion window is inferred from the patch end.
- Pioneer Podcast has an official notice but no established client event
  identity in the inspected catalog. Second Coming is already present.

The existing supported public source is the official in-game Notice feed,
discovered from the launcher's public Notice configuration. The inspected
catalog stores activity configuration, not live server opening windows. The
installed game content is packaged; no authenticated activity protocol or
public endpoint returning an authoritative active-event list was established
in this investigation. Do not infer that source from filenames or reuse player
credentials. A fresh Notice import and affected projection refresh are needed
after integration; no production import or refresh was performed here.
