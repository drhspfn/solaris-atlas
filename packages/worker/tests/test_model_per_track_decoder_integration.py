"""Opt-in regression using the local extracted game fixture and built converter."""

import io
import os
import struct
import subprocess
from pathlib import Path

import pytest


def _int(stream):
    return struct.unpack("<i", stream.read(4))[0]


def _string(stream):
    length = _int(stream)
    assert 0 < length < 100_000
    return stream.read(length).rstrip(b"\0").decode("utf-8")


def _animation_counts(path):
    stream = io.BytesIO(path.read_bytes())
    assert stream.read(8) == b"UEFORMAT"
    assert _string(stream) == "UEANIM"
    stream.read(1)  # version
    _string(stream)  # name
    _string(stream)  # object path
    assert stream.read(1) == b"\0"  # uncompressed
    attributes = {}
    for _ in range(_int(stream)):
        name = _string(stream)
        size = _int(stream)
        assert size >= 0
        payload = stream.read(size)
        assert len(payload) == size
        attributes[name] = io.BytesIO(payload)
    frames = _int(attributes["METADATA"])
    tracks = attributes["TRACKS"]
    track_count = _int(tracks)
    key_counts = [0, 0, 0]
    for _ in range(track_count):
        _string(tracks)
        for index, key_size in enumerate((16, 20, 16)):
            count = _int(tracks)
            assert 0 <= count <= frames
            key_counts[index] += count
            assert len(tracks.read(count * key_size)) == count * key_size
    curves = attributes["CURVES"]
    curve_count = _int(curves)
    curve_keys = 0
    for _ in range(curve_count):
        _string(curves)
        count = _int(curves)
        assert count >= 0
        curve_keys += count
        assert len(curves.read(count * 8)) == count * 8
    return frames, track_count, curve_count, key_counts, curve_keys


def test_real_per_track_animation_decodes_engine_codecs(tmp_path):
    converter = os.environ.get("SOLARIS_MODEL_TEST_CONVERTER")
    fixture = os.environ.get("SOLARIS_MODEL_TEST_EXTRACTED_FILES")
    if not converter or not fixture:
        pytest.skip("Set SOLARIS_MODEL_TEST_CONVERTER and SOLARIS_MODEL_TEST_EXTRACTED_FILES")
    root = Path(fixture)
    assert (root / "Client").is_dir() and (root / "Engine").is_dir()
    clip = "D0026_Cam006_R2T1QianXiaMd10011"
    result = subprocess.run([
        converter, "-i", str(root / "Client"), "-i", str(root / "Engine"),
        "-g", "GAME_WutheringWaves", "-p", f"*{clip}.uasset", "--mesh-format", "UEFormat",
        "--compression", "None", "-o", str(tmp_path / "output"), "-y", "-v",
    ], cwd=tmp_path, capture_output=True, text=True, timeout=120, check=True)
    assert "Unknown bone compression codec" not in result.stdout + result.stderr
    assert "Unknown curve compression codec" not in result.stdout + result.stderr
    outputs = list((tmp_path / "output").rglob(f"{clip}.ueanim"))
    assert len(outputs) == 1
    frames, tracks, curves, keys, curve_keys = _animation_counts(outputs[0])
    assert (frames, tracks, curves) == (151, 397, 18)
    assert keys[0] > tracks and keys[1] > tracks and keys[2] > tracks
    assert curve_keys > curves
