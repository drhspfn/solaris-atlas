"""Convert a verified single-rig Spine archive without modifying its source."""

import hashlib
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def convert_archive(bundle: Path, output: Path, cli: list[str]) -> None:
    """Run the independent CLI; reject ambiguous or damaged source bundles."""
    with tempfile.TemporaryDirectory(prefix="riglyph-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(bundle) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            skeletons = manifest.get("skeletons", [])
            if len(skeletons) != 1:
                raise ValueError("Expected exactly one skeleton; multi-rig archives require explicit mapping")
            skeleton = PurePosixPath(skeletons[0]["file"])
            files = manifest["files"]
            if str(skeleton) not in files:
                raise ValueError("Skeleton is absent from the archive manifest")
            atlases = [PurePosixPath(name) for name in files
                       if name.endswith(".atlas") and PurePosixPath(name).parent == skeleton.parent]
            if len(atlases) != 1:
                raise ValueError("Expected one atlas alongside the skeleton")
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
                if relative.parent == skeleton.parent:
                    dest = root.joinpath(*relative.parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
            skel = root.joinpath(*skeleton.parts)
            atlas = root.joinpath(*atlases[0].parts)
            subprocess.run([*cli, "convert", str(skel), "--atlas", str(atlas), "--images",
                            str(atlas.parent), "--out-dir", str(output)], check=True, timeout=300)
            if not (output / "manifest.json").is_file() or not (output / "skeleton.ir.json").is_file():
                raise ValueError("Riglyph did not produce a complete bundle")
