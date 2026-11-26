"""The small image surface the fidelity tools use, over Vellum's PNG codec.

The tools need RGB or RGBA pixel tuples, per-channel histograms and a size, so
they run in a sterile checkout with no Pillow. Resizing is nearest-neighbour,
which keeps results deterministic and exact for integer scale factors.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))
from vellum_png import RgbaImage, read_png  # noqa: E402

MODES = {"RGB": 3, "RGBA": 4}


class Image:
    """Pixels in one mode: `size`, `getdata()` and `histogram()`."""

    def __init__(self, width: int, height: int, pixels: list[tuple[int, ...]], mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"unsupported mode {mode!r}")
        if len(pixels) != width * height:
            raise ValueError("pixel count does not match the size")
        self.size = (width, height)
        self.mode = mode
        self._pixels = pixels

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    def getdata(self) -> list[tuple[int, ...]]:
        return self._pixels

    def histogram(self) -> list[int]:
        """256 bins per channel, channels concatenated in order."""
        channels = MODES[self.mode]
        bins = [0] * (256 * channels)
        for pixel in self._pixels:
            for channel in range(channels):
                bins[channel * 256 + pixel[channel]] += 1
        return bins

    def resize(self, size: tuple[int, int]) -> "Image":
        width, height = size
        if width <= 0 or height <= 0:
            raise ValueError("resize target must be positive")
        source_width, source_height = self.size
        pixels = [
            self._pixels[(y * source_height // height) * source_width + (x * source_width // width)]
            for y in range(height) for x in range(width)
        ]
        return Image(width, height, pixels, self.mode)

    def to_rgba(self) -> RgbaImage:
        data = bytearray()
        for pixel in self._pixels:
            data.extend(pixel if len(pixel) == 4 else (*pixel, 255))
        return RgbaImage(self.width, self.height, bytes(data))


def from_rgba(image: RgbaImage, mode: str = "RGB") -> Image:
    channels = MODES[mode]
    raw = image.pixels
    pixels = [tuple(raw[offset:offset + channels]) for offset in range(0, len(raw), 4)]
    return Image(image.width, image.height, pixels, mode)


def open_image(path: Path, mode: str = "RGB") -> Image:
    return from_rgba(read_png(Path(path)), mode)
