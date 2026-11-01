"""Convert a verified single-rig Spine archive without modifying its source."""

import hashlib
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def convert_archive(bundle: Path, output: Path, cli: list[str], *, skeleton_file: str | None = None) -> None:
    """Run the independent CLI; reject ambiguous or damaged source bundles."""
    with tempfile.TemporaryDirectory(prefix="riglyph-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(bundle) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            skeletons = manifest.get("skeletons", [])
            if skeleton_file is not None:
                skeletons = [item for item in skeletons if item['file'] == skeleton_file]
            if len(skeletons) != 1:
                raise ValueError("Expected exactly one skeleton; multi-rig archives require explicit mapping")
            skeleton = PurePosixPath(skeletons[0]["file"])
            files = manifest["files"]
            if str(skeleton) not in files:
                raise ValueError("Skeleton is absent from the archive manifest")
            atlases = [PurePosixPath(name) for name in files if name.endswith(".atlas")]
            local_atlases = [path for path in atlases if path.parent == skeleton.parent]
            if len(local_atlases) == 1:
                atlases = local_atlases
            if len(atlases) != 1:
                raise ValueError("Expected one atlas; multi-atlas archives require explicit mapping")
            for name, expected in files.items():
                relative = PurePosixPath(name)
                if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                    raise ValueError("Unsafe archive path")
                info = archive.getinfo(name)
                if info.file_size != expected["size_bytes"] or info.file_size > 64 * 1024 * 1024:
                    raise ValueError("Invalid archive entry size")
                data = archive.read(info)
                if hashlib.sha256(data).hexdigest() != expected["sha256"]:
                    raise ValueError("Archive checksum mismatch")
                if relative == skeleton or relative.parent == atlases[0].parent:
                    dest = root.joinpath(*relative.parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
            skel = root.joinpath(*skeleton.parts)
            atlas = root.joinpath(*atlases[0].parts)
            completed = subprocess.run([*cli, "convert", str(skel), "--atlas", str(atlas), "--images",
                                        str(atlas.parent), "--out-dir", str(output)],
                                       check=True, timeout=300, capture_output=True, text=True)
            response = json.loads(completed.stdout)
            report = response.get('report') if isinstance(response, dict) else None
            if not isinstance(report, dict) or report.get('losses') != [] or report.get('unsupported') != []:
                raise ValueError('Riglyph conversion has missing diagnostics, losses or unsupported features')
            if report.get('bytesConsumed') != skel.stat().st_size:
                raise ValueError('Riglyph did not consume the complete skeleton')
            if not (output / "manifest.json").is_file() or not (output / "skeleton.ir.json").is_file():
                raise ValueError("Riglyph did not produce a complete bundle")
            (output / 'conversion-report.json').write_text(json.dumps(report, sort_keys=True), encoding='utf-8')


def convert_bundle(bundle: Path, output: Path, cli: list[str]) -> Path:
    """Package the CLI output deterministically for the worker's object store."""
    with tempfile.TemporaryDirectory(prefix="riglyph-output-") as temporary:
        converted = Path(temporary) / "converted"
        with zipfile.ZipFile(bundle) as archive:
            source_manifest = json.loads(archive.read('manifest.json'))
        skeletons = source_manifest.get('skeletons', [])
        if len(skeletons) <= 1:
            convert_archive(bundle, converted, cli)
        else:
            components = []
            for skeleton in skeletons:
                component = 'components/' + hashlib.sha256(skeleton['file'].encode()).hexdigest()[:16]
                destination = converted / component
                convert_archive(bundle, destination, cli, skeleton_file=skeleton['file'])
                components.append({'source': skeleton['source'], 'skeleton': skeleton['file'],
                                   'manifest': component + '/manifest.json'})
            (converted / 'manifest.json').write_text(json.dumps({
                'format': 'solaris.riglyph-components.v1', 'components': components,
                'composition_status': 'source_controls_required'}, sort_keys=True), encoding='utf-8')
            (converted / 'source-manifest.json').write_text(json.dumps(source_manifest, sort_keys=True), encoding='utf-8')
        partial = output.with_suffix(".partial")
        try:
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(converted.rglob("*")):
                    if path.is_file():
                        info = zipfile.ZipInfo(path.relative_to(converted).as_posix(), (2000, 1, 1, 0, 0, 0))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        archive.writestr(info, path.read_bytes())
            partial.replace(output)
        finally:
            partial.unlink(missing_ok=True)
    return output
