"""Resolve cooked movie references to exact base-client archive paths."""

import asyncio
import logging
import re
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path, PurePosixPath

from wuwa_story_worker.archive_index import (
    ensure_archive_index,
    indexed_audio_paths,
    voice_filter_batches,
)
from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.cutscene_import import confined, movie_path
from wuwa_story_worker.cutscene_video_source import exported_video_path

logger = logging.getLogger(__name__)

_LEVELA_PREFIX = "/Game/Aki/Sequence/LevelA_Seq/"
_SAFE_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_SKIP_WRAPPER_PREFIXES = ("da_", "generated_")
_SKIP_WRAPPER_SUFFIXES = ("_all", "_1z")


def levela_sequence_code(engine_path: str) -> str | None:
    """Extract the exact scene code from a LevelA SequenceDataAsset UE path."""
    if not isinstance(engine_path, str):
        return None
    path = engine_path.removeprefix("asset:ue:")
    if not path.startswith(_LEVELA_PREFIX):
        return None
    leaf = path.rsplit("/", 1)[-1].split(".", 1)[0]
    code = leaf[3:] if leaf.startswith("DA_") else leaf
    if not code or not _SAFE_CODE.fullmatch(code):
        return None
    return code


def movie_stem_matches(stem: str, cg_name: str) -> bool:
    """Match exact movie stems or `<CgName>_*` variants without prefix leakage."""
    if not stem or not cg_name:
        return False
    s = stem.casefold()
    n = cg_name.casefold()
    return s == n or s.startswith(n + "_")


def infer_rover_variant(path_or_asset: str) -> tuple[str | None, int]:
    """Infer Rover variant ('male'|'female'|None, GirlOrBoy 1|0|2) from explicit suffixes."""
    stem = PurePosixPath(path_or_asset.split(".", 1)[0]).stem.casefold()
    if stem.endswith(("_nanzhu", "_nan")):
        return "male", 1
    if stem.endswith(("_nvzhu", "_nv")):
        return "female", 0
    return None, 2


def _is_candidate_wrapper(path: str, name: str) -> bool:
    pure = PurePosixPath(path)
    if pure.suffix.casefold() != ".uasset":
        return False
    stem_cf = pure.stem.casefold()
    if stem_cf.startswith(_SKIP_WRAPPER_PREFIXES) or stem_cf.endswith(_SKIP_WRAPPER_SUFFIXES):
        return False
    parts_cf = [part.casefold() for part in pure.parts]
    if path.startswith("Client/Content/Aki/Sequence/LevelA_Seq/"):
        if "anim" in parts_cf or "generated" in parts_cf:
            return False
        return len(pure.parts) >= 2 and pure.parts[-2].casefold() == name.casefold()
    if path.startswith("Client/Content/Aki/UI/UIResources/UiActivity/Mp4/"):
        return movie_stem_matches(pure.stem, name)
    return False


def scan_archive_cutscene_candidates(index: Path, names: Iterable[str]) -> dict[str, dict[str, list[str]]]:
    """Scan archive-index.sqlite once for non-VideoData wrappers and direct MP4 files."""
    targets = sorted({name for name in names if isinstance(name, str) and name})
    if not targets or not index.is_file():
        return {}
    by_cf = {name.casefold(): name for name in targets}
    found: dict[str, dict[str, set[str]]] = {}
    with closing(sqlite3.connect(index.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT path FROM files "
            "WHERE path LIKE 'Client/Content/Aki/Movies/%.mp4' "
            "   OR path LIKE 'Client/Content/Aki/Sequence/LevelA_Seq/%.uasset' "
            "   OR path LIKE 'Client/Content/Aki/UI/UIResources/UiActivity/Mp4/%.uasset'"
        ).fetchall()
    for (path,) in rows:
        pure = PurePosixPath(path)
        if path.startswith("Client/Content/Aki/Movies/") and pure.suffix.casefold() == ".mp4":
            for orig in by_cf.values():
                if movie_stem_matches(pure.stem, orig):
                    found.setdefault(orig, {"wrappers": set(), "direct_mp4s": set()})["direct_mp4s"].add(path)
        elif pure.suffix.casefold() == ".uasset":
            for orig in by_cf.values():
                if _is_candidate_wrapper(path, orig):
                    found.setdefault(orig, {"wrappers": set(), "direct_mp4s": set()})["wrappers"].add(path)
    return {
        name: {
            "wrappers": sorted(entry["wrappers"]),
            "direct_mp4s": sorted(entry["direct_mp4s"]),
        }
        for name, entry in found.items()
    }


def _ue_asset_key_from_wrapper(wrapper_path: str) -> str:
    package = "/Game/" + wrapper_path.removeprefix("Client/Content/").rsplit(".", 1)[0]
    object_name = package.rsplit("/", 1)[-1]
    return f"asset:ue:{package}.{object_name}"


def resolve_discovered_cutscene_sources(
    assets: Path,
    name: str,
    *,
    authored_assets: Iterable[str] = (),
    candidate_wrappers: Iterable[str] = (),
    candidate_mp4s: Iterable[str] = (),
    require_movie_file: bool = True,
) -> list[dict]:
    """Resolve fallback cutscene video variants from authored asset paths or client exports."""
    resolved_by_movie: dict[str, dict] = {}
    embedded_marker = "_cam"

    def record_source(asset_key: str, wrapper_rel: str, movie_rel: str, basis: str, priority: int) -> None:
        if require_movie_file and not confined(assets, movie_rel).is_file():
            return
        key = movie_rel.casefold()
        existing = resolved_by_movie.get(key)
        if existing is not None and existing["_priority"] <= priority:
            return
        rover, girl_or_boy = infer_rover_variant(movie_rel)
        if rover is None:
            rover, girl_or_boy = infer_rover_variant(asset_key)
        stem = PurePosixPath(movie_rel).stem.casefold()
        resolved_by_movie[key] = {
            "asset": asset_key,
            "wrapper_path": wrapper_rel,
            "movie_path": movie_rel,
            "rover": rover,
            "girl_or_boy": girl_or_boy,
            "basis": basis,
            "embedded_fragment": embedded_marker in stem,
            "_priority": priority,
        }

    for raw_asset in authored_assets:
        engine = raw_asset.removeprefix("asset:ue:")
        asset_key = f"asset:ue:{engine}"
        try:
            rel = exported_video_path(engine)
        except ValueError:
            continue
        if rel.casefold().endswith(".mp4"):
            record_source(asset_key, rel, rel, "authored asset reference", priority=0)
        else:
            uexp = confined(assets, rel)
            if uexp.is_file():
                try:
                    movie_rel = movie_path(uexp.read_bytes())
                except ValueError:
                    continue
                record_source(asset_key, rel, movie_rel, "authored StreamMediaSource", priority=0)

    local_wrappers = list(candidate_wrappers)
    seq_root = assets / "Client/Content/Aki/Sequence/LevelA_Seq"
    if seq_root.is_dir():
        for uasset in seq_root.rglob("*.uasset"):
            rel = uasset.relative_to(assets).as_posix()
            if _is_candidate_wrapper(rel, name):
                local_wrappers.append(rel)
    ui_root = assets / "Client/Content/Aki/UI/UIResources/UiActivity/Mp4"
    if ui_root.is_dir():
        for uasset in ui_root.rglob("*.uasset"):
            rel = uasset.relative_to(assets).as_posix()
            if _is_candidate_wrapper(rel, name):
                local_wrappers.append(rel)

    for wrapper in sorted(set(local_wrappers)):
        uexp_rel = PurePosixPath(wrapper).with_suffix(".uexp").as_posix()
        try:
            uexp = confined(assets, uexp_rel)
        except ValueError:
            continue
        if not uexp.is_file():
            continue
        try:
            movie_rel = movie_path(uexp.read_bytes())
        except ValueError:
            continue
        record_source(
            _ue_asset_key_from_wrapper(wrapper),
            uexp_rel,
            movie_rel,
            "archive StreamMediaSource wrapper",
            priority=1,
        )

    local_mp4s = list(candidate_mp4s)
    movies_root = assets / "Client/Content/Aki/Movies"
    if movies_root.is_dir():
        for mp4 in movies_root.rglob("*.mp4"):
            if movie_stem_matches(mp4.stem, name):
                local_mp4s.append(mp4.relative_to(assets).as_posix())

    for mp4_rel in sorted(set(local_mp4s)):
        try:
            normalized = exported_video_path("/Game/" + mp4_rel.removeprefix("Client/Content/"))
        except ValueError:
            continue
        record_source(
            "asset:ue:/Game/" + normalized.removeprefix("Client/Content/"),
            normalized,
            normalized,
            "archive direct MP4",
            priority=2,
        )

    results = sorted(
        resolved_by_movie.values(),
        key=lambda item: (item["girl_or_boy"], item["asset"]),
    )
    for item in results:
        item.pop("_priority", None)
    return results


def authored_movie_paths(assets: Path, wrapper_paths, *, errors: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve direct MP4s or wrapper-authored paths, never infer a movie from CgId."""
    movies = {}
    for package in sorted(set(wrapper_paths)):
        try:
            relative = PurePosixPath(package)
            if relative.suffix.casefold() == ".mp4":
                movie = exported_video_path("/Game/" + package.removeprefix("Client/Content/"))
                if movie != package or not (assets / relative).resolve().is_relative_to(assets.resolve()):
                    raise ValueError("Direct movie escapes exported assets")
                movies[package] = movie
                continue
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
        try:
            receipts.append(await export_assets(root, fmodel, "|".join(batch)))
        except RuntimeError:
            # A single large movie can exhaust the extractor's memory and must
            # not prevent unrelated cutscenes from being imported.
            for path in batch:
                try:
                    receipts.append(await export_assets(root, fmodel, path))
                except RuntimeError as error:
                    errors[path] = str(error)
                    logger.warning("cutscene.movie_export_failed path=%s reason=%s", path, error)
    if errors:
        diagnostics["errors"] = errors
    return {"receipts": receipts, "movies": movies,
            "missing": sorted(set(movies.values()) - available.keys()), **diagnostics}
