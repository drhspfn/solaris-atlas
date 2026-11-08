"""Bounded native WebM derivatives; the verified source archive is never replaced."""

import asyncio
import hashlib
import json
import math
import os
import signal
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from wuwa_story.storage.hashing import content_object_key

from wuwa_story_worker.riglyph_convert import convert_archive
from wuwa_story_worker.tooling import asset_workspace

RECIPE = "riglyph-native-v2"
VERSION = "0.16.2"


@dataclass
class AnimationArtifacts:
    converted: Path | None
    variants: list[dict]
    decision: dict
    default_animation: str | None = None


def enabled() -> bool:
    return os.getenv("WUWA_RIGLYPH_ENABLED", "").lower() in {"1", "true"}


def _integer(name: str, default: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def _run(args: list[str], log: Path, timeout: int) -> dict:
    """Keep diagnostics outside scratch and stop the entire renderer tree on timeout."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=stream, text=True,
                              start_new_session=os.name != "nt") as process:
            try:
                stdout, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, check=False)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise TimeoutError(f"Riglyph render timed out; log: {log}") from None
            if process.returncode:
                raise ValueError(f"Riglyph render failed ({process.returncode}); log: {log}")
    return json.loads(stdout)


def _clips(ir: dict) -> list[dict]:
    clips = ir.get("skeleton", {}).get("animations", [])
    if not clips or len(clips) > 64:
        raise ValueError("Expected between 1 and 64 authored animation clips")
    names = set()
    for clip in clips:
        name, duration = clip.get("id"), clip.get("durationSec")
        if (not isinstance(name, str) or not name or name in names
                or type(duration) not in {int, float} or not math.isfinite(duration)
                or not 0 <= duration <= 180):
            raise ValueError("Invalid, duplicate or unbounded authored animation")
        names.add(name)
    return clips


def frame_geometry(bounds: list, max_edge: int) -> dict:
    """Preserve authored aspect and framing; resolution is a proportional upper bound."""
    if (not isinstance(bounds, list) or len(bounds) != 4
            or any(type(value) not in {float, int} or not math.isfinite(value) for value in bounds)
            or bounds[2] <= 0 or bounds[3] <= 0):
        raise ValueError("Animation has no valid canvas")
    x, y, width, height = bounds
    scale = min(1, max_edge / max(width, height))
    # VP9 yuv420 alpha requires even dimensions. Never stretch the source geometry.
    pixels_w, pixels_h = max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2)
    world_width = max(width, height * pixels_w / pixels_h)
    return {"width": pixels_w, "height": pixels_h, "source_bounds": bounds,
            "camera": {"mode": "fixed", "x": x + width / 2, "y": y + height / 2,
                       "worldWidth": world_width}}


def _compose_black(video: Path, width: int, height: int, fps: int, count: int, log: Path, timeout: int):
    """Composite a verified opaque prefab background and keep a VP9 alpha stream."""
    ffmpeg = os.getenv('FFMPEG_PATH', 'ffmpeg')
    ffprobe = os.getenv('FFPROBE_PATH', 'ffprobe')
    output = video.with_name(video.stem + '-background.webm')
    args = [ffmpeg, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
            f'color=c=black:s={width}x{height}:r={fps}', '-c:v', 'libvpx-vp9', '-i', str(video),
            '-filter_complex', '[0:v]format=rgba[bg];[1:v]format=rgba[fg];'
                               '[bg][fg]overlay=shortest=1:format=auto,format=yuva420p[out]',
            '-map', '[out]', '-frames:v', str(count), '-c:v', 'libvpx-vp9', '-pix_fmt', 'yuva420p',
            '-auto-alt-ref', '0', '-deadline', 'realtime', '-cpu-used', '4', str(output)]
    try:
        with log.open('w', encoding='utf-8') as stream:
            subprocess.run(args, check=True, timeout=timeout, stdout=stream, stderr=stream)
        probe = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-count_frames',
                                '-show_entries', 'stream=codec_name,width,height,nb_read_frames:stream_tags=alpha_mode',
                                '-of', 'json', str(output)], check=True, timeout=timeout,
                               capture_output=True, text=True)
        streams = json.loads(probe.stdout).get('streams', [])
        if (len(streams) != 1 or streams[0].get('codec_name') != 'vp9'
                or streams[0].get('width') != width or streams[0].get('height') != height
                or int(streams[0].get('nb_read_frames', 0)) != count
                or streams[0].get('tags', {}).get('alpha_mode') != '1'):
            raise ValueError('Composited background video failed validation')
        with log.open('a', encoding='utf-8') as stream:
            subprocess.run([ffmpeg, '-v', 'error', '-c:v', 'libvpx-vp9', '-i', str(output),
                            '-vf', 'alphaextract', '-frames:v', '1', '-f', 'null', '-'],
                           check=True, timeout=timeout, stdout=stream, stderr=stream)
        output.replace(video)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        with log.open('a', encoding='utf-8') as stream:
            stream.write(str(getattr(error, 'stderr', '') or error))
        raise ValueError(f'Background composition failed; log: {log}') from error
    finally:
        output.unlink(missing_ok=True)


def _verify_opaque_first_frame(cli, native, converted, clip, config, width, height, video, logs, timeout):
    """Distinguish a naturally opaque first frame from an unverified alpha stream."""
    from PIL import Image

    reference = video.with_name(video.stem + '-alpha-reference')
    _run([*cli, 'render', str(converted / 'skeleton.ir.json'), '--images', str(converted / 'textures'),
          '--clip', clip, '--start', '0', '--end', '0.01', '--fps', '1', '--width', str(width),
          '--height', str(height), '--format', 'png-sequence', '--alpha', '--backend', 'native-cpu',
          '--native-path', native, '--config', str(config), '--max-frames', '1', '--overwrite',
          '--progress', 'none', '--out-dir', str(reference)], logs / f'{video.stem}-alpha-reference.log', timeout)
    decoded = video.with_name(video.stem + '-decoded-alpha.png')
    with (logs / f'{video.stem}-alpha-decode.log').open('w', encoding='utf-8') as stream:
        subprocess.run([os.getenv('FFMPEG_PATH', 'ffmpeg'), '-y', '-v', 'error', '-c:v', 'libvpx-vp9',
                        '-i', str(video), '-vf', 'alphaextract', '-frames:v', '1', str(decoded)],
                       check=True, timeout=timeout, stdout=stream, stderr=stream)
    with Image.open(reference / 'frame_000000.png') as original, Image.open(decoded) as encoded:
        return (original.size == (width, height) and encoded.size == (width, height)
                and original.convert('RGBA').getchannel('A').getextrema() == (255, 255)
                and encoded.convert('L').getextrema() == (255, 255))


def archive_policy(bundle: Path) -> dict:
    """Re-evaluate legacy archives without exporting the game a second time."""
    # Only decoded asset JSON is needed by the existing source eligibility policy.
    # Validate it before extraction, including the paths used by old archives.
    import tempfile
    from pathlib import PurePosixPath

    from wuwa_story_worker.animation_policy import assess_animation_sources

    with tempfile.TemporaryDirectory(prefix="riglyph-policy-") as directory, zipfile.ZipFile(bundle) as source:
        manifest = json.loads(source.read("manifest.json"))
        assets = {}
        by_package = {}
        for index, package in enumerate(manifest.get("packages", [])):
            identity = package.casefold()
            if identity in by_package:
                raise ValueError("Ambiguous case-insensitive archive package")
            assets[package] = {"decoded": Path(directory) / str(index), "decode_failed": True}
            assets[package]["decoded"].mkdir()
            by_package[identity] = assets[package]
        for name, expected in manifest.get("files", {}).items():
            if not name.startswith("decoded/") or not name.endswith(".json"):
                continue
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                raise ValueError("Unsafe decoded archive path")
            if source.getinfo(name).file_size > 64 * 1024 * 1024:
                raise ValueError("Decoded archive entry exceeds its limit")
            data = source.read(name)
            if len(data) != expected["size_bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
                raise ValueError("Decoded archive checksum mismatch")
            if "Client/Content/" not in name:
                raise ValueError("Decoded entry has no canonical package path")
            package = "/Game/" + name.split("Client/Content/", 1)[1].removesuffix(".json")
            # Cooked/exported package casing can differ from the authored reference.
            # Match the complete path, never a basename or another package's JSON.
            asset = by_package.get(package.casefold())
            if asset is not None:
                (asset["decoded"] / "asset.json").write_bytes(data)
                asset["decode_failed"] = False
        roots = manifest.get("source_resources") or [v["prefab"] for v in manifest.get("variants", [])]
        return assess_animation_sources(assets, (manifest.get("control_sources") or {}).get("missing", []), roots)


def build_animation(bundle: Path, *, cli: list[str] | None = None) -> AnimationArtifacts:
    """Render each clip independently. No invented sequence or dialogue timing."""
    cli = cli or ["node", os.getenv("WUWA_RIGLYPH_CLI",
                                   "/app/tools/riglyph/node_modules/@riglyph/cli/dist/cli/main.js")]
    with zipfile.ZipFile(bundle) as source:
        manifest = json.loads(source.read("manifest.json"))
    decision = archive_policy(bundle)
    legacy = None
    if not decision.get("conversion_allowed"):
        try:
            from wuwa_story_worker.legacy_spine_composition import legacy_plan
            legacy = legacy_plan(bundle)
        except (OSError, ValueError, KeyError, TypeError, IndexError, zipfile.BadZipFile) as error:
            return AnimationArtifacts(None, [], {**decision, "display": "static", "state": "static_only",
                                                "reasons": [*decision.get("reasons", []),
                                                            {"code": "legacy_composition_unsupported", "error": str(error)[:500]}]})
        decision = {"schema_version": 1, "conversion_allowed": True, "display": "static",
                    "state": "awaiting_render_validation", "reasons": [{"code": "render_validation_required"}]}
    if not legacy and len(manifest.get("skeletons", [])) != 1:
        raise ValueError("Native preview requires a verified single-rig composition")
    max_edge = _integer("WUWA_RIGLYPH_MAX_EDGE", 2048, 2048)
    fps = _integer("WUWA_RIGLYPH_FPS", 30, 60)
    threads = _integer("WUWA_RIGLYPH_THREADS", 2, 16)
    max_frames = _integer("WUWA_RIGLYPH_MAX_FRAMES", 18000, 36000)
    timeout = _integer("WUWA_RIGLYPH_TIMEOUT_SECONDS", 1800, 7200)
    # Attempt-local output prevents partial files from being reused as complete derivatives.
    output = bundle.parent / ("render-" + uuid.uuid4().hex)
    output.mkdir()
    with bundle.open("rb") as stream:
        source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    logs = asset_workspace() / "render-logs" / source_hash / output.name
    logs.mkdir(parents=True, exist_ok=True)
    converted = output / "converted"
    try:
        if legacy:
            convert_archive(bundle, converted, cli, skeleton_file=legacy['skeleton'], atlas_file=legacy['atlas'])
        else:
            convert_archive(bundle, converted, cli)
    except subprocess.SubprocessError as error:
        log = logs / "conversion.log"
        log.write_text(str(getattr(error, "stderr", "") or error), encoding="utf-8")
        raise ValueError(f"Riglyph conversion failed; log: {log}") from error
    ir = json.loads((converted / "skeleton.ir.json").read_text(encoding="utf-8"))
    clips = _clips(ir)
    if legacy:
        clips = [clip for clip in clips if clip['id'] in legacy['clips']]
        if {clip['id'] for clip in clips} != set(legacy['clips']):
            raise ValueError('Authored prefab or dialogue animation is absent from the converted skeleton')
        for slot in ir.get('skeleton', {}).get('slots', []):
            mode = slot.get('blend', 'normal')
            if mode not in legacy['blend']:
                raise ValueError('Unrecognized Spine slot blend mode')
            slot['blend'] = legacy['blend'][mode]
        (converted / 'skeleton.ir.json').write_text(json.dumps(ir), encoding='utf-8')
    if sum(max(1, math.ceil(clip["durationSec"] * fps)) for clip in clips) > max_frames:
        raise ValueError("Animation package exceeds the configured frame budget")
    archive = output / "riglyph.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
        for path in sorted(converted.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(converted).as_posix(), (2000, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                target.writestr(info, path.read_bytes())
        # Retain exact package paths and control cues alongside converted IR.
        info = zipfile.ZipInfo("source-manifest.json", (2000, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        target.writestr(info, json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        with zipfile.ZipFile(bundle) as source:
            for field in ("controls", "presentation"):
                entry = (manifest.get(field) or {}).get("file")
                if entry:
                    # convert_archive already verified every source entry's hash and size.
                    info = zipfile.ZipInfo(f"source-{field}.json", (2000, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    target.writestr(info, source.read(entry))
    try:
        bounds = ir.get("skeleton", {}).get("bounds")
        framing_basis = "skeleton_bounds"
        if legacy:
            width, height = legacy['size']
            scale, origin = legacy['layer']['scale'], legacy['layer']['origin']
            bounds = [(-width / 2 - origin[0]) / scale, (-height / 2 - origin[1]) / scale,
                      width / scale, height / scale]
            framing_basis = 'prefab_canvas'
        elif not isinstance(bounds, list) or len(bounds) != 4 or bounds[2] <= 0 or bounds[3] <= 0:
            # Some source rigs export [0,0,0,0]. Measure all clips with Riglyph itself,
            # using one envelope so transitions cannot change zoom or aspect ratio.
            geometry = _run([cli[0], str(Path(__file__).with_name("riglyph_frame.mjs")), cli[-1],
                             str(converted / "skeleton.ir.json"), str(fps), str(max_frames)],
                            logs / "framing.log", timeout)
            bounds = geometry.get("bounds")
            framing_basis = "animation_bounds"
        framing = frame_geometry(bounds, max_edge)
        width, height = framing["width"], framing["height"]
        config = output / "render-config.json"
        config.write_text(json.dumps({"camera": framing["camera"]}), encoding="utf-8")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return AnimationArtifacts(archive, [], {
            **decision, "display": "static", "state": "render_failed",
            "reasons": [{"code": "framing_failed", "error": str(error)[:500]}],
        })
    native = os.getenv("RIGLYPH_NATIVE_BIN", "/app/tools/riglyph-native/riglyph-native")
    variants = []
    for index, clip in enumerate(clips):
        video = output / f"{index:03d}.webm"
        try:
            report = _run([*cli, "render", str(converted / "skeleton.ir.json"),
                           "--images", str(converted / "textures"), "--clip", clip["id"],
                           "--format", "webm", "--alpha", "--backend", "native-cpu",
                           "--native-path", native, "--native-threads", str(threads),
                           "--width", str(width), "--height", str(height), "--fps", str(fps),
                           "--config", str(config),
                           "--end", str(max(1 / fps, clip["durationSec"])),
                           "--max-frames", str(max_frames), "--max-bytes", str(256 * 1024 * 1024),
                           "--sampling", "linear", "--progress", "json",
                           "--out", str(video)], logs / f"{index:03d}.log", timeout)
            probe = report.get("probe") or {}
            if (report.get("format") != "webm" or not video.is_file() or video.stat().st_size == 0
                    or probe.get("codec") != "vp9" or probe.get("alpha") is not True
                    or probe.get("width") != width or probe.get("height") != height
                    or report.get("frameCount") != max(1, math.ceil(clip["durationSec"] * fps))):
                raise ValueError(f"Riglyph did not validate a complete alpha WebM; log: {logs / f'{index:03d}.log'}")
            if legacy and probe.get('decodedAlphaVerified') is not True:
                if not _verify_opaque_first_frame(cli, native, converted, clip['id'], config,
                                                  width, height, video, logs, timeout):
                    raise ValueError('Could not validate alpha against the rendered first frame')
            if legacy and legacy.get('background'):
                _compose_black(video, width, height, fps, report['frameCount'],
                               logs / f'{index:03d}-background.log', timeout)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return AnimationArtifacts(archive, [], {
                **decision, "display": "static", "state": "render_failed",
                "reasons": [{"code": "render_failed", "error": str(error)[:500]}],
            })
        variants.append({"animation": clip["id"], "duration_seconds": clip["durationSec"],
                         "width": width, "height": height, "fps": fps, "alpha": True,
                         "source_bounds": bounds, "framing_basis": framing_basis,
                         **({'background': 'black'} if legacy and legacy.get('background') else {}),
                         "loop": legacy['clips'][clip['id']] if legacy else False,
                         "path": video, "mime_type": "video/webm"})
    authored = manifest.get("variants", [])
    if len(authored) == 1:
        for variant in variants:
            variant["rover"] = authored[0].get("rover")
    explicit = {item.get("animation") for item in authored if item.get("animation")}
    default = next(iter(explicit)) if len(explicit) == 1 else (
        "idle" if any(clip["id"] == "idle" for clip in clips)
        else clips[0]["id"] if len(clips) == 1 else None)
    if default is not None and default not in {clip["id"] for clip in clips}:
        raise ValueError("Authored default animation is absent from the converted skeleton")
    return AnimationArtifacts(archive, variants, {
        **decision, "display": "animated", "state": "rendered", "reasons": [],
        "renderer": "native-cpu", "renderer_version": VERSION,
    }, default)


async def publish_animation(session, bundle: Path, published, service, run_id: int | None,
                            *, artifacts: AnimationArtifacts | None = None) -> dict:
    """Content-address all derivatives and link to the retained original archive."""
    artifacts = artifacts or await asyncio.to_thread(build_animation, bundle)
    if artifacts.converted is None:
        return {"animation_import": artifacts.decision,
                "animation_preview": {"recipe": RECIPE, "status": "static", "variants": [],
                                      "source_file_id": published.id}}
    converted = await service.register_file(session, artifacts.converted, "unknown",
                                            mime_type="application/zip")
    await service.register_variant(session, published.id, converted.id, "riglyph_bundle", run_id)
    variants = []
    for item in artifacts.variants:
        file = await service.register_file(session, item["path"], "unknown", mime_type="video/webm")
        await service.register_variant(session, published.id, file.id, "riglyph_preview", run_id)
        key = content_object_key(file.sha256)
        variants.append({**{k: v for k, v in item.items() if k != "path"}, "file_id": file.id,
                         "url": service.storage.public_url(key), "object_key": key,
                         "size_bytes": file.size_bytes})
    return {"riglyph_file_id": converted.id, "animation_import": artifacts.decision,
            "animation_preview": {"recipe": RECIPE, "status": "ready" if variants else "failed", "variants": variants,
                                  "default_animation": artifacts.default_animation,
                                  "source_file_id": published.id, "renderer": "native-cpu",
                                  "renderer_version": VERSION}}
