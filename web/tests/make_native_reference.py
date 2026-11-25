#!/usr/bin/env python3
"""Make the perceptual gate's native reference from a native capture.

The input is a PNG the native editor rendered at device scale 2 (Pulp
`super-convolver-shot`, `render_to_file(..., 2.0f, skia)`): 8-bit RGB or RGBA,
not interlaced. The output is the luma of each 2 by 2 block, rounded, as an
8-bit greyscale PNG at the editor's CSS size. The gate reduces the browser's
device-scale-2 capture the same way (`perceptual_gate.js`, `reduceLuma`).

  make_native_reference.py native-820x560.png reference.png
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def decode_png(data: bytes) -> tuple[int, int, int, bytes]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit("not a PNG")
    at, idat, header = 8, b"", None
    while at < len(data):
        length, kind = struct.unpack(">I4s", data[at:at + 8])
        body = data[at + 8:at + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        at += 12 + length
    width, height, depth, color, _, _, interlace = header
    if depth != 8 or color not in (2, 6) or interlace:
        raise SystemExit("expected 8-bit RGB or RGBA, not interlaced")
    channels = 3 if color == 2 else 4
    raw = zlib.decompress(idat)
    stride = width * channels
    out = bytearray(height * stride)
    previous = bytearray(stride)
    for row in range(height):
        kind = raw[row * (stride + 1)]
        line = bytearray(raw[row * (stride + 1) + 1:(row + 1) * (stride + 1)])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            corner = previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 0xFF
        out[row * stride:(row + 1) * stride] = line
        previous = line
    return width, height, channels, bytes(out)


def luma(red: int, green: int, blue: int) -> float:
    return 0.299 * red + 0.587 * green + 0.114 * blue


def reduce_luma(width: int, height: int, channels: int, pixels: bytes) -> tuple[int, int, bytes]:
    half_w, half_h = width // 2, height // 2
    out = bytearray(half_w * half_h)
    stride = width * channels
    for y in range(half_h):
        for x in range(half_w):
            total = 0.0
            for dy in (0, 1):
                base = (2 * y + dy) * stride + 2 * x * channels
                for dx in (0, channels):
                    at = base + dx
                    total += luma(pixels[at], pixels[at + 1], pixels[at + 2])
            out[y * half_w + x] = min(255, int(total / 4 + 0.5))
    return half_w, half_h, bytes(out)


def encode_grey_png(width: int, height: int, pixels: bytes) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    rows = b"".join(b"\x00" + pixels[y * width:(y + 1) * width] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    width, height, channels, pixels = decode_png(Path(sys.argv[1]).read_bytes())
    half_w, half_h, grey = reduce_luma(width, height, channels, pixels)
    Path(sys.argv[2]).write_bytes(encode_grey_png(half_w, half_h, grey))
    print(f"{half_w}x{half_h} luma reference written to {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
