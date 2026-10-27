import runpy
from pathlib import Path

import pytest

patch = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "docker/patch_model_tools.py")
)["patch"]

PROVIDER = "new DefaultFileProvider(directory, SearchOption.AllDirectories, new VersionContainer(gameVersion))"
POST_MOUNT = "        provider.PostMount();"
SOURCE = (
    "provider = directory.EndsWith(\".apk\")\n"
    "    ? new ApkFileProvider(directory, new VersionContainer(gameVersion))\n"
    f"    : {PROVIDER};\n"
    f"{POST_MOUNT}\n"
)


def test_model_patch_adds_extra_roots_and_virtual_paths(tmp_path):
    program = tmp_path / "CUE4Parse.CLI/Program.cs"
    program.parent.mkdir()
    program.write_text(SOURCE, encoding="utf-8-sig")

    patch(tmp_path)

    source = program.read_text(encoding="utf-8")
    assert source == (
        "provider = directory.EndsWith(\".apk\")\n"
        "    ? new ApkFileProvider(directory, new VersionContainer(gameVersion))\n"
        "    : new DefaultFileProvider(new DirectoryInfo(directory), "
        "sources.Skip(1).Select(path => new DirectoryInfo(path)).ToArray(), "
        "SearchOption.AllDirectories, new VersionContainer(gameVersion));\n"
        "        provider.PostMount();\n"
        "        provider.LoadVirtualPaths();\n"
    )


@pytest.mark.parametrize("anchor", [PROVIDER, POST_MOUNT])
@pytest.mark.parametrize("copies", [0, 2])
def test_model_patch_rejects_source_drift_without_writing(tmp_path, anchor, copies):
    program = tmp_path / "CUE4Parse.CLI/Program.cs"
    program.parent.mkdir()
    original = SOURCE.replace(anchor, anchor * copies).encode("utf-8")
    program.write_bytes(original)

    with pytest.raises(RuntimeError, match="Pinned source changed"):
        patch(tmp_path)

    assert program.read_bytes() == original
