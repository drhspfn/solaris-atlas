"""Offline native Opus/WEM regression: libopus packets, no Ogg/header rewriting.

Run with a patched vgmstream-cli. --expect-rejection verifies the original r2117
rejects the six-channel fixture. All generated files live in a temporary directory.
"""

import argparse
import ctypes as ct
import ctypes.util
import json
import math
import struct
import subprocess
import tempfile
import wave
from pathlib import Path


def fixture(channels: int, anonymous: bool) -> tuple[bytes, list[int]]:
    opus = ct.CDLL(ctypes.util.find_library("opus"))
    create_args = [ct.c_int] * 4 + [ct.POINTER(ct.c_ubyte)]
    opus.opus_multistream_encoder_create.argtypes = create_args + [ct.c_int, ct.POINTER(ct.c_int)]
    opus.opus_multistream_encoder_create.restype = ct.c_void_p
    opus.opus_multistream_decoder_create.argtypes = create_args + [ct.POINTER(ct.c_int)]
    opus.opus_multistream_decoder_create.restype = ct.c_void_p
    opus.opus_multistream_encode.argtypes = [
        ct.c_void_p, ct.POINTER(ct.c_short), ct.c_int, ct.POINTER(ct.c_ubyte), ct.c_int,
    ]
    opus.opus_multistream_decode.argtypes = [
        ct.c_void_p, ct.POINTER(ct.c_ubyte), ct.c_int, ct.POINTER(ct.c_short), ct.c_int,
        ct.c_int,
    ]
    opus.opus_multistream_encoder_destroy.argtypes = [ct.c_void_p]
    opus.opus_multistream_decoder_destroy.argtypes = [ct.c_void_p]
    coupled = 0 if anonymous else channels // 2
    streams = channels - coupled
    mapping = (ct.c_ubyte * channels)(*range(channels))
    error = ct.c_int()
    encoder = opus.opus_multistream_encoder_create(
        48000, channels, streams, coupled, mapping, 2049, ct.byref(error),
    )
    if not encoder or error.value:
        raise RuntimeError(f"Opus encoder: {error.value}")
    decoder = opus.opus_multistream_decoder_create(
        48000, channels, streams, coupled, mapping, ct.byref(error),
    )
    if not decoder or error.value:
        opus.opus_multistream_encoder_destroy(encoder)
        raise RuntimeError(f"Opus decoder: {error.value}")
    packets = []
    reference = []
    frame_size, frame_count, skip = 960, 25, 312
    try:
        for frame in range(frame_count):
            # Different nonzero signals expose channel duplication and permutations.
            pcm = (ct.c_short * (frame_size * channels))(*(
                int(10000 * math.sin(2 * math.pi * (240 + ch * 170)
                                     * (frame * frame_size + sample) / 48000))
                for sample in range(frame_size) for ch in range(channels)
            ))
            packet = (ct.c_ubyte * 8192)()
            size = opus.opus_multistream_encode(encoder, pcm, frame_size, packet, len(packet))
            if not 0 < size < 65536:
                raise RuntimeError(f"Opus encode: {size}")
            packets.append(bytes(packet[:size]))
            decoded = (ct.c_short * (frame_size * channels))()
            count = opus.opus_multistream_decode(decoder, packet, size, decoded, frame_size, 0)
            if count != frame_size:
                raise RuntimeError(f"Opus decode: {count}")
            reference.extend(decoded)
    finally:
        opus.opus_multistream_encoder_destroy(encoder)
        opus.opus_multistream_decoder_destroy(decoder)

    samples = frame_size * frame_count - skip
    config = channels if anonymous else channels | (1 << 8) | (3 << 12)
    fmt = struct.pack("<HHIIHHHHIIIHBB", 0x3041, channels, 48000, 0, 0, 0,
                      0x12, frame_size, config, samples, frame_count, skip, 1,
                      255 if anonymous else 1)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return tag + struct.pack("<I", len(payload)) + payload

    body = (b"WAVE" + chunk(b"fmt ", fmt)
            + chunk(b"seek", struct.pack(f"<{frame_count}H", *map(len, packets)))
            + chunk(b"data", b"".join(packets)))
    return b"RIFF" + struct.pack("<I", len(body)) + body, reference[skip * channels:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decoder", type=Path)
    parser.add_argument("--expect-rejection", action="store_true")
    args = parser.parse_args()
    cases = [(6, True)] if args.expect_rejection else [(1, True), (2, True), (6, True), (2, False)]
    with tempfile.TemporaryDirectory(prefix="vgmstream-probe-") as directory:
        root = Path(directory)
        for channels, anonymous in cases:
            wem, expected = fixture(channels, anonymous)
            source, output = root / "probe.wem", root / "decoded.wav"
            source.write_bytes(wem)
            result = subprocess.run(
                [str(args.decoder.resolve()), "-i", "-o", str(output), str(source)],
                capture_output=True, text=True, check=False,
            )
            if args.expect_rejection:
                if result.returncode == 0 or "unknown mapping" not in result.stdout + result.stderr:
                    raise RuntimeError(f"Expected original mapping rejection: {result}")
                print("Original r2117 rejects anonymous 6ch/family255 as expected")
                continue
            if result.returncode:
                raise RuntimeError(f"Native decode failed: {result.stdout}\n{result.stderr}")
            metadata = subprocess.run(
                [str(args.decoder.resolve()), "-m", "-I", str(source)],
                capture_output=True, text=True, check=True,
            )
            if anonymous:
                assert json.loads(metadata.stdout)["channelLayout"] in (None, 0)
            with wave.open(str(output)) as wav:
                assert (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) == (channels, 48000, 2)
                assert wav.getnframes() == len(expected) // channels
                pcm = wav.readframes(wav.getnframes())
            actual = struct.unpack(f"<{len(expected)}h", pcm)
            for ch in range(channels):
                errors = [abs(a - b) for a, b in zip(actual[ch::channels], expected[ch::channels], strict=True)]
                # Native FFmpeg and libopus rounding can differ slightly.
                assert sum(errors) / len(errors) < 150, (channels, ch, max(errors))
            print(f"PASS {channels}ch anonymous={anonymous}: exact duration and discrete channel order")


if __name__ == "__main__":
    main()
