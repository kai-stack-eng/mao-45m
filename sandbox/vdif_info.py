"""Print the time range of a VDIF file from its first and last frames.

    uv run python sandbox/vdif_info.py path/to/file.vdif
"""

import os
import sys

from mao_45m.vdif import FRAME_BYTES
from mao_45m.vdif.convert import get_integ_time, get_time

path = sys.argv[1]
n_frames = os.path.getsize(path) // FRAME_BYTES

with open(path, "rb") as f:
    first = f.read(FRAME_BYTES)
    f.seek((n_frames - 1) * FRAME_BYTES)
    last = f.read(FRAME_BYTES)

print("start (UTC):", get_time(first))
print("end   (UTC):", get_time(last))
print("integ time :", get_integ_time(first), "s")
print("frames     :", n_frames)