#!/usr/bin/env python3
"""The native-reference converter turns a device-scale-2 PNG into 2x2 block luma.

Browser-independent: known RGB and RGBA pixels go through the real decode,
reduce and encode steps, and the result is read back and checked for its
dimensions and luma values. Removing or breaking the conversion fails these.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import make_native_reference as conv


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def make_png(width: int, height: int, channels: int, pixels: list[tuple[int, ...]],
             filter_kind: int = 0) -> bytes:
    """Encode pixels (row-major tuples) as an 8-bit RGB (3) or RGBA (4) PNG."""
    stride = width * channels
    flat = bytearray()
    for p in pixels:
        flat.extend(p)
    rows = b""
    previous = bytearray(stride)
    for y in range(height):
        line = bytearray(flat[y * stride:(y + 1) * stride])
        if filter_kind == 0:
            filtered = bytes(line)
        elif filter_kind == 1:
            filtered = bytes((line[i] - (line[i - channels] if i >= channels else 0)) & 0xFF
                             for i in range(stride))
        elif filter_kind == 2:
            filtered = bytes((line[i] - previous[i]) & 0xFF for i in range(stride))
        else:
            raise ValueError(filter_kind)
        rows += bytes([filter_kind]) + filtered
        previous = line
    color = 2 if channels == 3 else 6
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def convert(png: bytes) -> tuple[int, int, bytes]:
    width, height, channels, pixels = conv.decode_png(png)
    half_w, half_h, grey = conv.reduce_luma(width, height, channels, pixels)
    out = conv.encode_grey_png(half_w, half_h, grey)
    # Read the encoded greyscale PNG back independently of the converter's decoder.
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    at, idat, header = 8, b"", None
    while at < len(out):
        length, kind = struct.unpack(">I4s", out[at:at + 8])
        body = out[at + 8:at + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        at += 12 + length
    w, h, depth, color = header[:4]
    assert (depth, color) == (8, 0)
    raw = zlib.decompress(idat)
    assert len(raw) == h * (w + 1)
    return w, h, b"".join(raw[y * (w + 1) + 1:(y + 1) * (w + 1)] for y in range(h))


class MakeNativeReferenceTest(unittest.TestCase):
    def test_white_and_black_blocks_rgb(self) -> None:
        # 4x2 RGB: left 2x2 block white, right 2x2 block black.
        px = [(255, 255, 255)] * 2 + [(0, 0, 0)] * 2
        w, h, grey = convert(make_png(4, 2, 3, px + px))
        self.assertEqual((w, h), (2, 1))
        self.assertEqual(grey, bytes([255, 0]))

    def test_primaries_use_rec601_luma(self) -> None:
        # Each 2x2 block is a single colour; expected = round(0.299 r + 0.587 g + 0.114 b).
        colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        row = [c for c in colours for _ in range(2)]
        w, h, grey = convert(make_png(6, 2, 3, row + row))
        self.assertEqual((w, h), (3, 1))
        self.assertEqual(list(grey), [76, 150, 29])

    def test_rgba_ignores_alpha_and_averages_block(self) -> None:
        # One 2x2 block: two white pixels, two black, alpha deliberately varied.
        px = [(255, 255, 255, 10), (0, 0, 0, 200), (0, 0, 0, 0), (255, 255, 255, 255)]
        w, h, grey = convert(make_png(2, 2, 4, px))
        self.assertEqual((w, h), (1, 1))
        self.assertEqual(grey, bytes([128]))  # 127.5 rounds up

    def test_png_filters_decode_to_the_same_pixels(self) -> None:
        px = [(10 * i % 256, 20 * i % 256, 30 * i % 256) for i in range(8)]
        expected = convert(make_png(4, 2, 3, px, 0))
        for kind in (1, 2):
            self.assertEqual(convert(make_png(4, 2, 3, px, kind)), expected)

    def test_rejects_non_png_and_unsupported_formats(self) -> None:
        with self.assertRaises(SystemExit):
            conv.decode_png(b"not a png at all")
        grey = (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00" * 2)) + chunk(b"IEND", b""))
        with self.assertRaises(SystemExit):
            conv.decode_png(grey)

    def test_command_line_writes_half_size_greyscale(self) -> None:
        px = [(255, 255, 255)] * 4 + [(0, 0, 0)] * 4
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp, "in.png"), Path(tmp, "out.png")
            src.write_bytes(make_png(4, 2, 3, px[:4] + px[4:]))
            subprocess.run([sys.executable, str(Path(conv.__file__)), str(src), str(dst)],
                           check=True, capture_output=True)
            w, h, grey = convert(make_png(4, 2, 3, px[:4] + px[4:]))
            self.assertEqual(dst.read_bytes(), conv.encode_grey_png(w, h, grey))

    def test_committed_reference_is_reduction_of_its_dimensions(self) -> None:
        ref = Path(conv.__file__).parent / "fixtures/superconvolver-native/native-820x560-luma.png"
        data = ref.read_bytes()
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (820, 560))


if __name__ == "__main__":
    unittest.main()
