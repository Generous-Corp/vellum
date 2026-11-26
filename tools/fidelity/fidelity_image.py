"""The small image surface the fidelity tools use, over Vellum's PNG codec.

The tools need RGB or RGBA pixel tuples, per-channel histograms and a size, so
they run in a sterile checkout with no Pillow. `resize_lanczos` and
`resize_bicubic` reproduce Pillow's resampling (separable two-pass convolution
with 22-bit fixed-point weights, Pillow's pass order, alpha premultiplied for
RGBA), so results match the tools these were ported from; `resize` is
nearest-neighbour.
"""

from __future__ import annotations

import math
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

    def resize_lanczos(self, size: tuple[int, int]) -> "Image":
        """Resize with Pillow's LANCZOS filter, matching its 8-bit output."""
        return self._resample(size, _lanczos, 3.0)

    def resize_bicubic(self, size: tuple[int, int]) -> "Image":
        """Resize with Pillow's BICUBIC filter, its default for resize()."""
        return self._resample(size, _bicubic, 2.0)

    def _resample(self, size: tuple[int, int], kernel, support: float) -> "Image":
        width, height = size
        if width <= 0 or height <= 0:
            raise ValueError("resize target must be positive")
        if size == self.size:
            return Image(width, height, list(self._pixels), self.mode)
        channels = MODES[self.mode]
        pixels = self._pixels
        if self.mode == "RGBA":
            pixels = [_premultiply(pixel) for pixel in pixels]
        planes = [[pixel[c] for pixel in pixels] for c in range(channels)]
        source_width, source_height = self.size
        # Pillow runs the vertical pass first when the height shrinks by more
        # than twice what the width does; the pass order changes the rounding.
        shrink = source_height - height
        horizontal_first = not (shrink > 0 and shrink > (source_width - width) * 2)

        def horizontal(planes, rows):
            bounds, weights = _coefficients(source_width, width, kernel, support)
            return [_horizontal(plane, source_width, rows, width, bounds, weights)
                    for plane in planes]

        def vertical(planes, columns):
            bounds, weights = _coefficients(source_height, height, kernel, support)
            return [_vertical(plane, columns, height, bounds, weights) for plane in planes]

        if horizontal_first:
            if width != source_width:
                planes = horizontal(planes, source_height)
            if height != source_height:
                planes = vertical(planes, width)
        else:
            planes = vertical(planes, source_width)
            if width != source_width:
                planes = horizontal(planes, height)
        resized = list(zip(*planes))
        if self.mode == "RGBA":
            resized = [_unpremultiply(pixel) for pixel in resized]
        return Image(width, height, resized, self.mode)

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


PRECISION_BITS = 32 - 8 - 2
_ROUND = 1 << (PRECISION_BITS - 1)


def _sinc(x: float) -> float:
    if x == 0.0:
        return 1.0
    x *= math.pi
    return math.sin(x) / x


def _lanczos(x: float) -> float:
    return _sinc(x) * _sinc(x / 3) if -3.0 <= x < 3.0 else 0.0


def _bicubic(x: float) -> float:
    a = -0.5
    x = abs(x)
    if x < 1.0:
        return ((a + 2.0) * x - (a + 3.0)) * x * x + 1
    if x < 2.0:
        return (((x - 5) * x + 8) * x - 4) * a
    return 0.0


def _coefficients(in_size: int, out_size: int, kernel, kernel_support: float
                  ) -> tuple[list[int], list[list[int]]]:
    """Per output index: the first input index and its fixed-point weights."""
    scale = in_size / out_size
    filterscale = max(scale, 1.0)
    support = kernel_support * filterscale
    inverse = 1.0 / filterscale
    bounds: list[int] = []
    weights: list[list[int]] = []
    for index in range(out_size):
        center = (index + 0.5) * scale
        low = max(int(center - support + 0.5), 0)
        high = min(int(center + support + 0.5), in_size)
        raw = [kernel((x + low - center + 0.5) * inverse) for x in range(high - low)]
        total = sum(raw)
        if total != 0.0:
            raw = [w / total for w in raw]
        fixed = [int(w * (1 << PRECISION_BITS) + (-0.5 if w < 0 else 0.5)) for w in raw]
        bounds.append(low)
        weights.append(fixed)
    return bounds, weights


def _clip8(value: int) -> int:
    value >>= PRECISION_BITS
    return 0 if value < 0 else 255 if value > 255 else value


def _horizontal(plane, width, height, out_width, bounds, weights):
    out = []
    for y in range(height):
        row = plane[y * width:(y + 1) * width]
        for low, kernel in zip(bounds, weights):
            total = _ROUND
            for offset, weight in enumerate(kernel):
                total += row[low + offset] * weight
            out.append(_clip8(total))
    return out


def _vertical(plane, width, out_height, bounds, weights):
    out = [0] * (width * out_height)
    for y, (low, kernel) in enumerate(zip(bounds, weights)):
        base = y * width
        for x in range(width):
            total = _ROUND
            for offset, weight in enumerate(kernel):
                total += plane[(low + offset) * width + x] * weight
            out[base + x] = _clip8(total)
    return out


def _muldiv255(value: int, alpha: int) -> int:
    tmp = value * alpha + 128
    return ((tmp >> 8) + tmp) >> 8


def _premultiply(pixel: tuple[int, ...]) -> tuple[int, ...]:
    alpha = pixel[3]
    return (_muldiv255(pixel[0], alpha), _muldiv255(pixel[1], alpha),
            _muldiv255(pixel[2], alpha), alpha)


def _unpremultiply(pixel: tuple[int, ...]) -> tuple[int, ...]:
    alpha = pixel[3]
    if alpha in (0, 255):
        return tuple(pixel)
    return tuple(min(255, (255 * value) // alpha) for value in pixel[:3]) + (alpha,)
