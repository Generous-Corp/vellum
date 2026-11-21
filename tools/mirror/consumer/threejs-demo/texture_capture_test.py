#!/usr/bin/env python3
"""Check that the glTF capture contains both colors from its 2x2 texture."""

import struct
import sys
import zlib


def decode_png(path):
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("capture is not a PNG")
    pos = 8
    idat = bytearray()
    width = height = depth = color_type = interlace = None
    while pos < len(data):
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + size]
        pos += size + 12
        if kind == b"IHDR":
            width, height, depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
        elif kind == b"IDAT":
            idat.extend(chunk)
        elif kind == b"IEND":
            break
    if (depth, interlace) != (8, 0) or color_type not in (2, 6):
        raise ValueError("unsupported capture pixel format")
    channels = 3 if color_type == 2 else 4
    stride = width * channels
    raw = zlib.decompress(idat)
    if len(raw) != height * (stride + 1):
        raise ValueError("truncated capture scanlines")
    rows = []
    previous = bytearray(stride)
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        source = raw[cursor : cursor + stride]
        cursor += stride
        row = bytearray(stride)
        for i, value in enumerate(source):
            left = row[i - channels] if i >= channels else 0
            above = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                estimate = left + above - upper_left
                distances = (
                    abs(estimate - left),
                    abs(estimate - above),
                    abs(estimate - upper_left),
                )
                predictor = (left, above, upper_left)[distances.index(min(distances))]
            else:
                raise ValueError(f"unsupported PNG filter {filter_type}")
            row[i] = (value + predictor) & 0xFF
        rows.append(row)
        previous = row
    return width, height, channels, rows


def near(pixel, expected, tolerance=42):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel[:3], expected))


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: texture_capture_test.py CAPTURE.png")
    _, _, channels, rows = decode_png(sys.argv[1])
    pink = (244, 63, 94)
    green = (52, 211, 153)
    pink_count = green_count = 0
    for row in rows:
        for offset in range(0, len(row), channels):
            pixel = row[offset : offset + channels]
            pink_count += near(pixel, pink)
            green_count += near(pixel, green)
    if pink_count < 2 or green_count < 2:
        raise SystemExit(
            f"textured glTF capture missing checker colors: pink={pink_count} green={green_count}"
        )
    print(f"textured glTF capture contains checker colors: pink={pink_count} green={green_count}")


if __name__ == "__main__":
    main()
