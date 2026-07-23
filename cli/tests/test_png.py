from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from cli.vellum_png import PngError, RgbaImage, decode_png_bytes, encode_png, montage, read_png, write_png


class PngTests(unittest.TestCase):
    def test_round_trip_and_montage_are_deterministic(self) -> None:
        red = RgbaImage(2, 1, bytes([255, 0, 0, 255] * 2))
        blue = RgbaImage(1, 2, bytes([0, 0, 255, 255] * 2))
        self.assertEqual(decode_png_bytes(encode_png(red)), red)
        composed = montage([red, blue], columns=2, gap=1, background=(1, 2, 3, 255))
        self.assertEqual((composed.width, composed.height), (7, 4))
        self.assertEqual(decode_png_bytes(encode_png(composed)), composed)
        self.assertEqual(encode_png(composed), encode_png(composed))

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested/montage.png"
            write_png(path, composed)
            self.assertEqual(read_png(path), composed)

    def test_decoder_rejects_corruption_and_unbounded_shapes(self) -> None:
        image = RgbaImage(1, 1, b"\x00\x01\x02\xff")
        encoded = bytearray(encode_png(image))
        encoded[29] ^= 0x01
        with self.assertRaisesRegex(PngError, "CRC"):
            decode_png_bytes(bytes(encoded))
        with self.assertRaises(PngError):
            decode_png_bytes(b"not a png")
        with self.assertRaises(PngError):
            RgbaImage(2, 2, b"too short")
        with self.assertRaises(PngError):
            montage([image], columns=0)

    def test_decoder_rejects_overlong_stream_with_stable_error(self) -> None:
        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
        # A 1x1 RGBA scanline is exactly five bytes including the filter byte.
        encoded = (
            b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xffextra"))
            + chunk(b"IEND", b"")
        )
        with self.assertRaisesRegex(PngError, "size or stream boundary"):
            decode_png_bytes(encoded)


if __name__ == "__main__":
    unittest.main()
