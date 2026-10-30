"""Opt in to cooked Blueprint data for story control extraction, never execution."""

import sys
from pathlib import Path


def patch(cue: Path) -> None:
    program = cue / 'CUE4Parse.CLI/Program.cs'
    source = program.read_text(encoding='utf-8-sig')
    anchor = '        provider.PostMount();'
    if source.count(anchor) != 1:
        raise RuntimeError('Pinned CUE CLI PostMount changed')
    source = source.replace(anchor,
                            '        provider.ReadScriptData = Environment.GetEnvironmentVariable('
                            '"WUWA_READ_SCRIPT_DATA") == "1";\n' + anchor)
    program.write_text(source, encoding='utf-8')


if __name__ == '__main__':
    patch(Path(sys.argv[1]))
