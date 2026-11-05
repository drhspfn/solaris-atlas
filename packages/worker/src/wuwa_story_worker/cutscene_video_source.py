"""Exact exported paths for authored VideoData MP4s and Unreal media objects."""

import re


def exported_video_path(engine_path: str) -> str:
    """Preserve direct MP4 references; never manufacture a wrapper for them."""
    if (not isinstance(engine_path, str)
            or not re.fullmatch(r"/Game/[A-Za-z0-9_./-]+", engine_path)
            or any(part in {"", ".", ".."} for part in engine_path.split("/")[1:])):
        raise ValueError("Expected an exact authored video source path")
    if engine_path.casefold().endswith(".mp4"):
        if not engine_path.startswith("/Game/Aki/Movies/"):
            raise ValueError("Direct video source must be under Aki/Movies")
        relative = engine_path.removeprefix("/Game/")
    else:
        package, separator, object_name = engine_path.rpartition(".")
        if not separator or package.rsplit("/", 1)[-1].casefold() != object_name.casefold():
            raise ValueError("Expected an exact Unreal media asset identity")
        relative = package.removeprefix("/Game/") + ".uexp"
    return "Client/Content/" + relative
