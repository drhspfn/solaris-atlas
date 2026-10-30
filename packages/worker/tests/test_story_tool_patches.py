import runpy
from pathlib import Path

import pytest

patch = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'docker/patch_story_tools.py'))['patch']


def test_script_data_decode_is_explicitly_opt_in(tmp_path):
    program = tmp_path / 'CUE4Parse.CLI/Program.cs'
    program.parent.mkdir()
    program.write_text('before\n        provider.PostMount();\nafter', encoding='utf-8')
    patch(tmp_path)
    assert 'Environment.GetEnvironmentVariable("WUWA_READ_SCRIPT_DATA") == "1"' in program.read_text()
    assert program.read_text().index('ReadScriptData') < program.read_text().index('PostMount')


def test_changed_tool_source_fails_the_build_instead_of_silently_skipping_scripts(tmp_path):
    program = tmp_path / 'CUE4Parse.CLI/Program.cs'
    program.parent.mkdir()
    program.write_text('changed pinned source', encoding='utf-8')
    with pytest.raises(RuntimeError, match='PostMount'):
        patch(tmp_path)
