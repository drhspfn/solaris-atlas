"""Apply checked model-export adaptations to the pinned CUE CLI source."""

import sys
from pathlib import Path


def patch(cue: Path) -> None:
    program = cue / "CUE4Parse.CLI/Program.cs"
    source = program.read_text(encoding="utf-8-sig")
    replacements = (
        (
            "new DefaultFileProvider(directory, SearchOption.AllDirectories, new VersionContainer(gameVersion))",
            "new DefaultFileProvider(new DirectoryInfo(directory), "
            "sources.Skip(1).Select(path => new DirectoryInfo(path)).ToArray(), "
            "SearchOption.AllDirectories, new VersionContainer(gameVersion))",
        ),
        (
            "        provider.PostMount();",
            "        provider.PostMount();\n        provider.LoadVirtualPaths();",
        ),
    )
    for old, new in replacements:
        if source.count(old) != 1:
            raise RuntimeError(f"Pinned source changed: expected 1 occurrences of {old!r}")
        source = source.replace(old, new)
    program.write_text(source, encoding="utf-8")


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
