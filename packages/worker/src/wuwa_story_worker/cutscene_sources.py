"""Resolve cooked movie references to exact base-client archive paths."""

import asyncio
import logging
from pathlib import Path, PurePosixPath

from wuwa_story_worker.archive_index import (
    ensure_archive_index,
    indexed_audio_paths,
    voice_filter_batches,
)
from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.cutscene_import import movie_path

logger = logging.getLogger(__name__)


def authored_movie_paths(assets: Path, wrapper_paths, *, errors: dict[str, str] | None = None) -> dict[str, str]:
    """The wrapper filename/CgId is not evidence of its runtime movie filename."""
    movies = {}
    for package in sorted(set(wrapper_paths)):
        try:
            relative = PurePosixPath(package)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in package
                    or ":" in package or not package.startswith("Client/Content/Aki/")
                    or relative.suffix.casefold() not in {".uasset", ".uexp"}):
                raise ValueError("Expected an exact authored movie wrapper package")
            path = assets / relative.with_suffix(".uexp")
            if not path.resolve().is_relative_to(assets.resolve()):
                raise ValueError("Movie wrapper escapes exported assets")
            movies[package] = movie_path(path.read_bytes())
        except (ValueError, OSError) as error:
            if errors is None:
                raise
            errors[package] = str(error)
            logger.warning("cutscene.movie_source_rejected wrapper=%s reason=%s", package, error)
    return movies


def indexed_movie_paths(index: Path, expected) -> dict[str, str]:
    """Bound by exact basenames, then require full authored path equality."""
    expected = sorted(set(expected))
    for path in expected:
        relative = PurePosixPath(path)
        if (not path.startswith("Client/Content/Aki/Movies/") or relative.suffix.casefold() != ".mp4"
                or relative.is_absolute() or ".." in relative.parts or ":" in path or "\\" in path):
            raise ValueError("Expected an exact authored MP4 package path")
    available = {}
    for path in indexed_audio_paths(index, [PurePosixPath(path).name for path in expected]):
        key = path.casefold()
        if key in available and available[key] != path:
            raise ValueError("Ambiguous case-insensitive movie archive path")
        available[key] = path
    return {path: available[path.casefold()] for path in expected if path.casefold() in available}


async def prepare_base_movies(root: Path, assets: Path, wrapper_paths, fmodel: Path,
                              *, index: Path | None = None) -> dict:
    """Return receipts for merge_exports plus exact reference and absence evidence.

    Missing base movies may still live in separately downloaded video packages.
    Never infer a substitute from a CG number, wrapper alias or matching basename.
    """
    errors = {}
    movies = await asyncio.to_thread(authored_movie_paths, assets, wrapper_paths, errors=errors)
    diagnostics = {"errors": errors} if errors else {}
    if not movies:
        return {"receipts": [], "movies": {}, "missing": [], **diagnostics}
    if index is None:
        index = await ensure_archive_index(root, fmodel, root)
    available = await asyncio.to_thread(indexed_movie_paths, index, movies.values())
    receipts = []
    for batch in voice_filter_batches(available.values()):
        receipts.append(await export_assets(root, fmodel, "|".join(batch)))
    return {"receipts": receipts, "movies": movies,
            "missing": sorted(set(movies.values()) - available.keys()), **diagnostics}
