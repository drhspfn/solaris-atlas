"""Portable, named morph curves alongside the independent skeletal GLB."""

import math

FORMAT = "solaris-facial-v1"


def facial_clip(dto, name, morph_names, *, body_clip=None, source_curves=None):
    """Keep only exact authored morph matches; gameplay curves are not expressions."""
    names = {name.casefold(): name for name in morph_names}
    if not names:
        return None
    tracks = []
    curves = ([(curve["CurveName"], curve["FloatCurve"]["Keys"]) for curve in source_curves]
              if source_curves is not None else [(curve.name, curve.keys) for curve in dto.curves])
    for curve_name, curve_keys in curves:
        target = names.get(curve_name.casefold())
        if target is None or not curve_keys:
            continue
        fps = dto.metadata.frames_per_second if dto.metadata else 0
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("Facial animation needs a positive frame rate")
        keys = ([[float(key["Time"]), float(key["Value"])] for key in curve_keys]
                if source_curves is not None else
                [[float(key.frame / fps), float(key.value)] for key in curve_keys])
        tracks.append({"target": target, "keys": keys})
    if not tracks:
        return None
    seconds = max(
        max(track["keys"][-1][0] for track in tracks),
        (dto.metadata.num_frames - 1) / dto.metadata.frames_per_second,
    )
    clip = {"name": name, "body_clip": body_clip, "seconds": seconds, "tracks": tracks}
    validate_facial_pack({"format": FORMAT, "clips": [clip]})
    for track in tracks if source_curves is None else []:
        # Retain the ends of constant runs without changing linear interpolation.
        compact = []
        for key in track["keys"]:
            if len(compact) >= 2 and compact[-2][1] == compact[-1][1] == key[1]:
                compact[-1] = key
            else:
                compact.append(key)
        track["keys"] = compact
    return clip


def validate_facial_pack(pack):
    """Validate bytes again before publication, not just exporter statistics."""
    if not isinstance(pack, dict) or pack.get("format") != FORMAT:
        raise ValueError("Invalid facial animation format")
    clips = pack.get("clips")
    if not isinstance(clips, list):
        raise ValueError("Invalid facial animation clips")
    names, body_clips = set(), set()
    for clip in clips:
        if not isinstance(clip, dict):
            raise ValueError("Invalid facial animation clip")
        name = clip.get("name")
        seconds = clip.get("seconds")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Facial animation names must be unique")
        names.add(name)
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Invalid facial animation duration")
        if clip.get("body_clip") is not None and not isinstance(clip["body_clip"], str):
            raise ValueError("Invalid facial body clip")
        if clip.get("body_clip") is not None:
            if not clip["body_clip"] or clip["body_clip"] in body_clips:
                raise ValueError("Ambiguous facial body clip")
            body_clips.add(clip["body_clip"])
        if not isinstance(clip.get("tracks"), list) or not clip["tracks"]:
            raise ValueError("Empty facial animation")
        targets = set()
        for track in clip["tracks"]:
            if not isinstance(track, dict):
                raise ValueError("Invalid facial animation track")
            target = track.get("target")
            if not isinstance(target, str) or not target or target.casefold() in targets:
                raise ValueError("Facial targets must be unique")
            targets.add(target.casefold())
            previous = -1
            if not isinstance(track.get("keys"), list) or not track["keys"]:
                raise ValueError("Empty facial curve")
            for key in track["keys"]:
                if (
                    not isinstance(key, list) or len(key) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in key)
                    or key[0] < 0 or not previous < key[0] <= seconds
                ):
                    raise ValueError("Invalid facial curve samples")
                previous = key[0]
    return pack
