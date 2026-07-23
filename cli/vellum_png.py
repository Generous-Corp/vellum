#!/usr/bin/env python3
"""Bounded PNG decoding and deterministic contact-sheet composition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
import zlib


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_DIMENSION = 16_384
MAX_PIXELS = 64 * 1024 * 1024
MAX_ENCODED_BYTES = 256 * 1024 * 1024


class PngError(ValueError):
    pass


@dataclass(frozen=True)
class RgbaImage:
    width: int
    height: int
    pixels: bytes

    def __post_init__(self) -> None:
        if (
            isinstance(self.width, bool) or isinstance(self.height, bool)
            or self.width <= 0 or self.height <= 0
            or self.width > MAX_DIMENSION or self.height > MAX_DIMENSION
            or self.width * self.height > MAX_PIXELS
            or len(self.pixels) != self.width * self.height * 4
        ):
            raise PngError("RGBA image dimensions and byte length disagree or exceed limits")


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    distance_left = abs(estimate - left)
    distance_above = abs(estimate - above)
    distance_upper_left = abs(estimate - upper_left)
    if distance_left <= distance_above and distance_left <= distance_upper_left:
        return left
    return above if distance_above <= distance_upper_left else upper_left


def decode_png_bytes(encoded: bytes) -> RgbaImage:
    if len(encoded) > MAX_ENCODED_BYTES:
        raise PngError("PNG exceeds the encoded byte limit")
    if not encoded.startswith(PNG_SIGNATURE):
        raise PngError("PNG signature is missing")
    offset = len(PNG_SIGNATURE)
    header: tuple[int, int, int] | None = None
    compressed = bytearray()
    saw_end = False
    while offset < len(encoded):
        if offset + 12 > len(encoded):
            raise PngError("PNG chunk is truncated")
        length = struct.unpack(">I", encoded[offset:offset + 4])[0]
        chunk_type = encoded[offset + 4:offset + 8]
        if len(chunk_type) != 4 or any(
            not (ord("A") <= value <= ord("Z") or ord("a") <= value <= ord("z"))
            for value in chunk_type
        ):
            raise PngError("PNG chunk type is invalid")
        end = offset + 12 + length
        if end > len(encoded):
            raise PngError("PNG chunk payload is truncated")
        payload = encoded[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", encoded[offset + 8 + length:end])[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
            raise PngError("PNG chunk CRC is invalid")
        if header is None and chunk_type != b"IHDR":
            raise PngError("IHDR must be the first PNG chunk")
        if chunk_type == b"IHDR":
            if header is not None or length != 13:
                raise PngError("PNG has a duplicate or malformed IHDR")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if (
                width <= 0 or height <= 0 or width > MAX_DIMENSION or height > MAX_DIMENSION
                or width * height > MAX_PIXELS
            ):
                raise PngError("PNG dimensions exceed limits")
            if bit_depth != 8 or color_type not in {2, 6}:
                raise PngError("Montages accept only 8-bit RGB or RGBA PNGs")
            if compression != 0 or filtering != 0 or interlace != 0:
                raise PngError("PNG compression, filter, or interlace mode is unsupported")
            header = (width, height, 4 if color_type == 6 else 3)
        elif chunk_type == b"IDAT":
            if saw_end:
                raise PngError("IDAT appears after IEND")
            compressed.extend(payload)
            if len(compressed) > MAX_ENCODED_BYTES:
                raise PngError("PNG compressed stream exceeds limits")
        elif chunk_type == b"IEND":
            if length != 0 or saw_end:
                raise PngError("PNG has a malformed or duplicate IEND")
            saw_end = True
            offset = end
            break
        elif not chunk_type[0] & 0x20:
            raise PngError(f"Unsupported critical PNG chunk {chunk_type!r}")
        offset = end
    if header is None or not compressed or not saw_end or offset != len(encoded):
        raise PngError("PNG is incomplete or has trailing bytes")

    width, height, channels = header
    stride = width * channels
    expected = (stride + 1) * height
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(bytes(compressed), expected + 1)
        if len(raw) <= expected:
            raw += inflater.flush(expected + 1 - len(raw))
    except (ValueError, zlib.error) as error:
        raise PngError(f"PNG compressed stream is invalid: {error}") from error
    if (
        len(raw) != expected or not inflater.eof or inflater.unconsumed_tail
        or inflater.unused_data
    ):
        raise PngError("PNG decompressed size or stream boundary is invalid")

    reconstructed = bytearray(height * stride)
    previous = bytearray(stride)
    source_offset = 0
    for row_index in range(height):
        filter_type = raw[source_offset]
        source_offset += 1
        source = raw[source_offset:source_offset + stride]
        source_offset += stride
        row = bytearray(stride)
        for index, value in enumerate(source):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                predicted = 0
            elif filter_type == 1:
                predicted = left
            elif filter_type == 2:
                predicted = above
            elif filter_type == 3:
                predicted = (left + above) // 2
            elif filter_type == 4:
                predicted = _paeth(left, above, upper_left)
            else:
                raise PngError(f"Unsupported PNG scanline filter {filter_type}")
            row[index] = (value + predicted) & 0xFF
        reconstructed[row_index * stride:(row_index + 1) * stride] = row
        previous = row

    if channels == 4:
        return RgbaImage(width, height, bytes(reconstructed))
    rgba = bytearray(width * height * 4)
    for source_index in range(0, len(reconstructed), 3):
        destination = source_index // 3 * 4
        rgba[destination:destination + 3] = reconstructed[source_index:source_index + 3]
        rgba[destination + 3] = 255
    return RgbaImage(width, height, bytes(rgba))


def read_png(path: Path) -> RgbaImage:
    try:
        return decode_png_bytes(path.read_bytes())
    except OSError as error:
        raise PngError(f"Cannot read PNG {path}: {error}") from error


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload)) + kind + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def encode_png(image: RgbaImage) -> bytes:
    scanlines = bytearray()
    stride = image.width * 4
    for row in range(image.height):
        scanlines.append(0)
        start = row * stride
        scanlines.extend(image.pixels[start:start + stride])
    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0)
    return PNG_SIGNATURE + _chunk(b"IHDR", header) + _chunk(
        b"IDAT", zlib.compress(bytes(scanlines), level=9)
    ) + _chunk(b"IEND", b"")


def write_png(path: Path, image: RgbaImage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(image))


def montage(
    images: list[RgbaImage], *, columns: int | None = None, gap: int = 16,
    background: tuple[int, int, int, int] = (24, 26, 32, 255),
) -> RgbaImage:
    if not images:
        raise PngError("A montage requires at least one image")
    if columns is None:
        columns = 1
        while columns * columns < len(images):
            columns += 1
    if isinstance(columns, bool) or columns <= 0 or columns > len(images):
        raise PngError("Montage columns must be between one and the image count")
    if isinstance(gap, bool) or gap < 0 or gap > 1024:
        raise PngError("Montage gap must be between 0 and 1024")
    if len(background) != 4 or any(value < 0 or value > 255 for value in background):
        raise PngError("Montage background must be four byte values")
    cell_width = max(image.width for image in images)
    cell_height = max(image.height for image in images)
    rows = (len(images) + columns - 1) // columns
    width = columns * cell_width + (columns + 1) * gap
    height = rows * cell_height + (rows + 1) * gap
    if width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
        raise PngError("Montage dimensions exceed limits")
    pixels = bytearray(bytes(background) * (width * height))
    for index, image in enumerate(images):
        column = index % columns
        row = index // columns
        origin_x = gap + column * (cell_width + gap) + (cell_width - image.width) // 2
        origin_y = gap + row * (cell_height + gap) + (cell_height - image.height) // 2
        source_stride = image.width * 4
        for image_row in range(image.height):
            source_start = image_row * source_stride
            destination = ((origin_y + image_row) * width + origin_x) * 4
            pixels[destination:destination + source_stride] = image.pixels[
                source_start:source_start + source_stride
            ]
    return RgbaImage(width, height, bytes(pixels))
