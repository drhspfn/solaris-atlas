"""Caption timing follows VideoConfig and VideoUtils in the verified 3.7 client."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.i18n import Locale, LocalizationKey, LocalizationValue

CAPTION_FPS = 30
VOICE_SUFFIXES = {"en": "En", "ja": "Ja", "ko": "Ko", "zh": ""}


def caption_tracks(rows: list[dict], texts: dict[str, str]) -> dict[str, list[dict]]:
    tracks = {}
    for language, suffix in VOICE_SUFFIXES.items():
        localized = [row for row in rows if row.get("Duration" + suffix, 0) > 0]
        # The game falls back as a whole, not one missing caption at a time.
        selected, fields = (localized, suffix) if localized else (rows, "")
        cues = []
        for row in selected:
            key = row.get("CaptionText")
            start, duration = row.get("ShowMoment" + fields, 0), row.get("Duration" + fields, 0)
            if key not in texts or start < 0 or duration <= 0:
                continue
            cues.append(
                {
                    "start": start / CAPTION_FPS,
                    "end": (start + duration) / CAPTION_FPS,
                    "text": texts[key],
                    "key": key,
                }
            )
        tracks[language] = sorted(cues, key=lambda cue: (cue["start"], cue["key"]))
    return tracks


async def caption_texts(
    session: AsyncSession, rows: list[dict], locale: str, release_id: int
) -> dict[str, str]:
    keys = {row.get("CaptionText") for row in rows if row.get("CaptionText")}
    if not keys:
        return {}
    values = await session.execute(
        select(LocalizationKey.key, LocalizationValue.content)
        .join(LocalizationValue, LocalizationValue.key_id == LocalizationKey.id)
        .join(Locale, Locale.id == LocalizationValue.locale_id)
        .where(
            LocalizationKey.key.in_(keys),
            Locale.code == locale,
            LocalizationValue.release_id == release_id,
            LocalizationValue.status == "resolved_nonempty",
        )
    )
    return {key: text for key, text in values if text}
