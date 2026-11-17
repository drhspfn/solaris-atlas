"""Resolve notice-described trials through same-release authored client joins."""
import re

from sqlalchemy import select

from wuwa_story.db.models.i18n import (
    Locale,
    LocalizationContent,
    LocalizationKey,
    LocalizationValue,
)
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord

TRIAL_ACTIVITY = "BinData/RoleTrial/roletrialactivity.json"
TRIAL_INFO = "BinData/RoleTrial/roletrialinfo.json"
SKIN_ACTIVITY = "BinData/RoleSkinTrial/roleskintrialactivity.json"
SKIN_INFO = "BinData/RoleSkinTrial/roleskintrialinfo.json"
SKIN = "BinData/role/roleskin.json"
PATHS = (TRIAL_ACTIVITY, TRIAL_INFO, SKIN_ACTIVITY, SKIN_INFO, SKIN)


def _normalized(value):
    return re.sub(r"[^\w]+", "", value.casefold())


def embedded_notice_activities(content, events):
    """Resolve individually dated patch-note sections by unique exact title."""
    if not re.search(r"Patch Notes for Version\s+\d+\.\d+", content["title"], re.I):
        return []
    result = []
    sections = re.finditer(r"(?m)^\[([^]\n]+)\][^\n]*\n(.*?)(?=^\[|^✦(?:New|Other|Adjustments)|\Z)",
                           content["text"], re.S)
    for section in sections:
        body = section[2]
        if "Duration" not in body:
            continue
        matching = [event for event in events if event.event_kind == "limited"
                    and _normalized(event.title) == _normalized(section[1])]
        if len(matching) == 1:
            result.append((matching[0], {**content, "text": body},
                           {"basis": "notice_section", "title": section[1]}))
    return result


def resolve_trials(content, events, rows, translations, release_id):
    """Only emit a match when the full source join identifies one client event."""
    tables = {path: [] for path in PATHS}
    for path, data in rows:
        if path in tables:
            tables[path].append(data)
    by_source = {event.source_id: event for event in events}
    result = []
    if "will become available in Ascendant Aces as Trial Resonators" in content["text"]:
        featured = re.search(r"5-Star Resonator:\s*([^,\n]+)", content["text"])
        if featured:
            matching_banners = [event for event in events if event.event_kind == "banner"
                and event.source_id.startswith("gacha:")
                and (event.source_data or {}).get("client", {}).get("gacha_type") == 2
                and any(_normalized(name) == _normalized(featured[1]) for name in
                    (event.source_data or {}).get("client", {}).get("locales", {}).get("en", {}).get("featured_names", []))]
            for banner in matching_banners if len(matching_banners) == 1 else []:
                gacha = banner.source_id.removeprefix("gacha:")
                if not gacha.isdecimal():
                    continue
                trial_ids = {info.get("Id") for info in tables[TRIAL_INFO] if info.get("GachaId") == int(gacha)}
                activities = [row for row in tables[TRIAL_ACTIVITY] if trial_ids.intersection(row.get("RoleIdList") or [])]
                if len(activities) != 1:
                    continue
                event = by_source.get(f"activity:{activities[0].get('Id')}")
                if event and event.event_kind == "limited":
                    result.append((event, content, {"basis": "role_trial_gacha", "release_id": release_id,
                                                   "gacha_id": int(gacha)}))
    section = re.search(r"✦\[Glamour Couture\] Outfit Tryout✦(.*?)(?=✦|$)", content["text"], re.S)
    if section:
        candidates = []
        for activity in tables[SKIN_ACTIVITY]:
            event = by_source.get(f"activity:{activity.get('Id')}")
            if not event or event.event_kind != "limited":
                continue
            for info in tables[SKIN_INFO]:
                if info.get("Id") not in (activity.get("RoleIdList") or []):
                    continue
                for skin in tables[SKIN]:
                    if skin.get("Id") != info.get("PreviewSkinId") or skin.get("RoleId") != info.get("RoleId"):
                        continue
                    role, outfit = translations.get(skin.get("Name")), translations.get(skin.get("TitleName"))
                    if role and outfit and _normalized(f"{role} - {outfit}") in _normalized(content["text"]):
                        candidates.append((event, skin["Id"]))
        if len(candidates) == 1:
            event, skin_id = candidates[0]
            result.append((event, {**content, "text": section[1]},
                           {"basis": "outfit_trial", "release_id": release_id, "skin_id": skin_id}))
    return result


async def notice_trial_matches(session, content, events):
    if ("will become available in Ascendant Aces as Trial Resonators" not in content["text"]
        and "[Glamour Couture] Outfit Tryout" not in content["text"]):
        return []
    version = re.search(r"Version\s+(\d+\.\d+)\s+update", content["text"], re.I)
    if not version:
        return []
    events = [event for event in events if ((event.source_data or {}).get("client", {}).get("asset_version") or "").startswith(version[1] + ".")]
    versions = {event.source_data["client"]["asset_version"] for event in events}
    if len(versions) != 1:
        return []
    releases = list(await session.scalars(select(GameRelease.id).where(GameRelease.game_version == next(iter(versions)))))
    if len(releases) != 1:
        return []
    release = releases[0]
    rows = (await session.execute(select(SourceFile.logical_source_path, SourceRecord.data)
        .join(SourceRecord, SourceRecord.source_file_id == SourceFile.id)
        .where(SourceFile.release_id == release, SourceRecord.release_id == release,
               SourceFile.logical_source_path.in_(PATHS)))).all()
    keys = {row.get(field) for path, row in rows if path == SKIN for field in ("Name", "TitleName") if row.get(field)}
    translations = {}
    if keys:
        values = (await session.execute(select(LocalizationKey.key, LocalizationContent.content)
            .join(LocalizationValue, LocalizationValue.key_id == LocalizationKey.id)
            .join(Locale, Locale.id == LocalizationValue.locale_id)
            .join(LocalizationContent, LocalizationContent.id == LocalizationValue.content_id)
            .where(LocalizationValue.release_id == release, Locale.code == "en", LocalizationKey.key.in_(keys)))).all()
        translations = dict(values)
    return resolve_trials(content, events, rows, translations, release)
