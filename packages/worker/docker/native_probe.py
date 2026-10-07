"""Generate an Oodle block so each real CLI can verify managed/native decompression."""

import ctypes
import sys
from pathlib import Path

root = Path(sys.argv[1])
library = ctypes.CDLL(str(root / "liboodle-data-shared.so"))
compress = library.OodleLZ_Compress
compress.restype = ctypes.c_ssize_t
compress.argtypes = [
    ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t,
]
source = b"solaris-native-probe\n" * 128
output = ctypes.create_string_buffer(len(source) + 4096)
size = compress(8, source, len(source), output, 4, None, None, None, None, 0)
if not 0 < size < len(source):
    raise RuntimeError(f"Oodle compression probe failed: {size}")
(root / "oodle-probe.bin").write_bytes(output.raw[:size])
