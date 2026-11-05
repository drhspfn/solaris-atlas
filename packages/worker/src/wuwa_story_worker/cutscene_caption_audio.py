"""Caption-triggered external voices, following the inspected client VideoView.

Evidence: VideoConfig.GetVideoCaptions, VideoView.JNo/_iy and
PlotAudioModel.GetExternalSourcesMediaName. This is independent of VideoSound.
"""

import re
import sqlite3
import struct
from contextlib import closing
from pathlib import Path

from flatbuffers.table import Table

CAPTION_AUDIO_SCHEMA = 1
LANGUAGES = ("en", "ja", "ko", "zh")
# VideoUtils.MillisecondPerFrame, as used by VideoView.JNo (not VideoSound seconds).
SECONDS_PER_CAPTION_FRAME = 33.33 / 1000


def _table(blob):
    return Table(blob, struct.unpack_from("<I", blob)[0])


def _int(table, offset):
    field = table.Offset(offset)
    return struct.unpack_from("<i", table.Bytes, table.Pos + field)[0] if field else 0


def _bool(table, offset):
    field = table.Offset(offset)
    return bool(table.Bytes[table.Pos + field]) if field else False


def _string(table, offset):
    field = table.Offset(offset)
    return table.String(table.Pos + field).decode("utf-8") if field else ""


def caption_rows(config: Path, name: str):
    with closing(sqlite3.connect(config.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'videocaption'").fetchone():
            return []
        rows = db.execute("SELECT CaptionId, BinData FROM videocaption WHERE CgName = ? ORDER BY CaptionId",
                          (name,)).fetchall()
    return [(identity, _table(blob)) for identity, blob in rows]


def has_caption_audio(config: Path, name: str) -> bool:
    return any(_bool(row, 26) for _, row in caption_rows(config, name))


def plot_audio_database(assets: Path) -> Path | None:
    paths = list(assets.rglob("db_plot_audio.db"))
    if len(paths) > 1:
        raise ValueError("Ambiguous caption PlotAudio configuration")
    return paths[0] if paths else None


def caption_voice_requests(config: Path, plot_config: Path | None, name: str, gender: int,
                           *, missing_assets: list[str] | None = None) -> list[dict]:
    """Return exact external filenames and start times; never invent missing voices.

    Localized timing fallback applies to the WHOLE caption list, as in VideoConfig.
    Caption Duration hides text; it does not stop the voice in VideoView.JNo.
    """
    rows = caption_rows(config, name)
    if not any(_bool(row, 26) for _, row in rows):
        return []

    def missing(reason):
        if missing_assets is None:
            raise ValueError(reason)
        if reason not in missing_assets:
            missing_assets.append(reason)

    if plot_config is None:
        missing(f"{name}: enabled caption voices require PlotAudio configuration")
        return []
    # VideoCaption generated binding offsets: base, EN, JA, KO frame pairs.
    localized = {"en": (14, 16), "ja": (22, 24), "ko": (18, 20), "zh": (8, 10)}
    use_localized = {lang: any(_int(row, offsets[1]) != 0 for _, row in rows)
                     for lang, offsets in localized.items()}
    requests = []
    with closing(sqlite3.connect(plot_config.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for identity, caption in rows:
            if not _bool(caption, 26):
                continue
            key = _string(caption, 12)
            found = db.execute("SELECT BinData FROM plotaudio WHERE Id = ?", (key,)).fetchone()
            if not found:
                missing(f"Caption {identity}: no exact PlotAudio.Id={key}")
                continue
            plot = _table(found[0])
            filename = _string(plot, 8)
            if not re.fullmatch(r"[A-Za-z0-9_]+", filename):
                # HL1 may substitute a runtime WorldModel value into {VarParams}.
                missing(f"Caption {identity}: unresolved or unsafe PlotAudio filename")
                continue
            for lang in LANGUAGES:
                start_field, duration_field = localized[lang] if use_localized[lang] else (8, 10)
                if use_localized[lang] and _int(caption, duration_field) == 0:
                    continue
                if _int(caption, duration_field) <= 0:
                    missing(f"Caption {identity}/{lang}: nonpositive text duration requires runtime timing")
                    continue
                start = _int(caption, start_field)
                if start < 0:
                    missing(f"Caption {identity}/{lang}: negative source start requires runtime timing")
                    continue
                gendered = _bool(plot, {"en": 12, "ja": 14, "ko": 16, "zh": 18}[lang])
                if gendered and gender not in (0, 1):
                    missing(f"Caption {identity}/{lang}: explicit player gender required")
                    continue
                suffix = ("_F" if gender == 0 else "_M") if gendered else ""
                prefix = "gl" if _bool(plot, 10) else lang
                requests.append({"filename": f"{prefix}_{filename}{suffix}.wem", "language": lang,
                                 "rover": "female" if gender == 0 else "male" if gender == 1 else None,
                                 "start_seconds": start * SECONDS_PER_CAPTION_FRAME,
                                 "caption_id": identity, "plot_audio_id": key})
    return requests


def caption_soundtracks(config: Path, assets: Path, name: str, gender: int,
                        *, missing_assets: list[str] | None = None) -> list[dict]:
    if not has_caption_audio(config, name):
        return []
    requests = caption_voice_requests(config, plot_audio_database(assets), name, gender,
                                      missing_assets=missing_assets)
    if not requests:
        return []
    wanted = {request['filename'].casefold() for request in requests}
    paths = {}
    for path in assets.rglob("*"):
        if path.is_file() and path.name.casefold() in wanted:
            paths.setdefault(path.name.casefold(), []).append(path)
    tracks = []
    for request in requests:
        matches = paths.get(request['filename'].casefold(), [])
        if len(matches) != 1:
            reason = f"Caption {request['caption_id']}/{request['language']}: expected one {request['filename']} ({len(matches)} found)"
            if missing_assets is None:
                raise ValueError(reason)
            if reason not in missing_assets:
                missing_assets.append(reason)
            continue
        tracks.append({"external_media": matches[0].relative_to(assets).as_posix(),
                       "language": request['language'], "rover": request['rover'],
                       "start_seconds": request['start_seconds']})
    return tracks
