import runpy
from pathlib import Path

import pytest

patch = runpy.run_path(str(Path(__file__).resolve().parents[1] / "docker/patch_model_tools.py"))[
    "patch"
]

PROVIDER = "new DefaultFileProvider(directory, SearchOption.AllDirectories, new VersionContainer(gameVersion))"
POST_MOUNT = "        provider.PostMount();"
SOURCE = (
    'provider = directory.EndsWith(".apk")\n'
    "    ? new ApkFileProvider(directory, new VersionContainer(gameVersion))\n"
    f"    : {PROVIDER};\n"
    f"{POST_MOUNT}\n"
)
LOOSE_FILE = "new OsGameFile(_workingDirectory, file, mountPoint, Versions)"


def write_sources(root, program_source=SOURCE, provider_source=LOOSE_FILE):
    program = root / "CUE4Parse.CLI/Program.cs"
    provider = root / "CUE4Parse/FileProvider/DefaultFileProvider.cs"
    for path, source in ((program, program_source), (provider, provider_source)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8-sig")
    return program, provider


def test_model_patch_adds_extra_roots_and_virtual_paths(tmp_path):
    program, provider = write_sources(tmp_path)

    patch(tmp_path)

    source = program.read_text(encoding="utf-8")
    assert source == (
        'provider = directory.EndsWith(".apk")\n'
        "    ? new ApkFileProvider(directory, new VersionContainer(gameVersion))\n"
        "    : new DefaultFileProvider(new DirectoryInfo(directory), "
        "sources.Skip(1).Select(path => new DirectoryInfo(path)).ToArray(), "
        "SearchOption.AllDirectories, new VersionContainer(gameVersion));\n"
        "        provider.PostMount();\n"
        "        provider.LoadVirtualPaths();\n"
    )
    assert provider.read_text(encoding="utf-8") == (
        "new OsGameFile(directory, file, mountPoint, Versions)"
    )


@pytest.mark.parametrize("anchor", [PROVIDER, POST_MOUNT])
@pytest.mark.parametrize("copies", [0, 2])
def test_model_patch_rejects_source_drift_without_writing(tmp_path, anchor, copies):
    program, provider = write_sources(tmp_path, SOURCE.replace(anchor, anchor * copies))
    originals = {path: path.read_bytes() for path in (program, provider)}

    with pytest.raises(RuntimeError, match="Pinned source changed"):
        patch(tmp_path)

    assert all(path.read_bytes() == original for path, original in originals.items())


@pytest.mark.parametrize("copies", [0, 2])
def test_model_patch_rejects_loose_root_source_drift_without_partial_writes(tmp_path, copies):
    paths = write_sources(tmp_path, provider_source=LOOSE_FILE * copies)
    originals = {path: path.read_bytes() for path in paths}

    with pytest.raises(RuntimeError, match="Pinned source changed"):
        patch(tmp_path)

    assert all(path.read_bytes() == original for path, original in originals.items())
