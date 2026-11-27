"""A 5x7 bitmap font for montage labels, so labels need no font files.

Each glyph is seven rows of five bits, most significant bit on the left.
Lowercase letters draw as uppercase; a character without a glyph draws as
a hollow box so a missing glyph is visible rather than silently dropped.
"""

from __future__ import annotations

GLYPHS: dict[str, tuple[int, ...]] = {
    " ": (0, 0, 0, 0, 0, 0, 0),
    "A": (14, 17, 17, 31, 17, 17, 17),
    "B": (30, 17, 17, 30, 17, 17, 30),
    "C": (14, 17, 16, 16, 16, 17, 14),
    "D": (30, 17, 17, 17, 17, 17, 30),
    "E": (31, 16, 16, 30, 16, 16, 31),
    "F": (31, 16, 16, 30, 16, 16, 16),
    "G": (14, 17, 16, 23, 17, 17, 15),
    "H": (17, 17, 17, 31, 17, 17, 17),
    "I": (14, 4, 4, 4, 4, 4, 14),
    "J": (7, 2, 2, 2, 2, 18, 12),
    "K": (17, 18, 20, 24, 20, 18, 17),
    "L": (16, 16, 16, 16, 16, 16, 31),
    "M": (17, 27, 21, 21, 17, 17, 17),
    "N": (17, 17, 25, 21, 19, 17, 17),
    "O": (14, 17, 17, 17, 17, 17, 14),
    "P": (30, 17, 17, 30, 16, 16, 16),
    "Q": (14, 17, 17, 17, 21, 18, 13),
    "R": (30, 17, 17, 30, 20, 18, 17),
    "S": (15, 16, 16, 14, 1, 1, 30),
    "T": (31, 4, 4, 4, 4, 4, 4),
    "U": (17, 17, 17, 17, 17, 17, 14),
    "V": (17, 17, 17, 17, 17, 10, 4),
    "W": (17, 17, 17, 21, 21, 21, 10),
    "X": (17, 17, 10, 4, 10, 17, 17),
    "Y": (17, 17, 10, 4, 4, 4, 4),
    "Z": (31, 1, 2, 4, 8, 16, 31),
    "0": (14, 17, 19, 21, 25, 17, 14),
    "1": (4, 12, 4, 4, 4, 4, 14),
    "2": (14, 17, 1, 2, 4, 8, 31),
    "3": (31, 2, 4, 2, 1, 17, 14),
    "4": (2, 6, 10, 18, 31, 2, 2),
    "5": (31, 16, 30, 1, 1, 17, 14),
    "6": (6, 8, 16, 30, 17, 17, 14),
    "7": (31, 1, 2, 4, 8, 8, 8),
    "8": (14, 17, 17, 14, 17, 17, 14),
    "9": (14, 17, 17, 15, 1, 2, 12),
    ".": (0, 0, 0, 0, 0, 12, 12),
    ",": (0, 0, 0, 0, 12, 4, 8),
    ":": (0, 12, 12, 0, 12, 12, 0),
    "-": (0, 0, 0, 31, 0, 0, 0),
    "_": (0, 0, 0, 0, 0, 0, 31),
    "/": (0, 1, 2, 4, 8, 16, 0),
    "(": (2, 4, 8, 8, 8, 4, 2),
    ")": (8, 4, 2, 2, 2, 4, 8),
    "%": (24, 25, 2, 4, 8, 19, 3),
    "#": (10, 10, 31, 10, 31, 10, 10),
    "'": (4, 4, 8, 0, 0, 0, 0),
    "+": (0, 4, 4, 31, 4, 4, 0),
    "=": (0, 0, 31, 0, 31, 0, 0),
}
MISSING = (31, 17, 17, 17, 17, 17, 31)
GLYPH_WIDTH, GLYPH_HEIGHT, ADVANCE = 5, 7, 6


def glyph(character: str) -> tuple[int, ...]:
    return GLYPHS.get(character.upper(), MISSING)


def text_width(text: str, scale: int) -> int:
    return max(0, len(text) * ADVANCE - 1) * scale


def draw_text(pixels: bytearray, width: int, height: int, x: int, y: int,
              text: str, color: tuple[int, int, int], scale: int) -> None:
    """Draw `text` into an RGBA byte buffer, clipped to its bounds."""
    for index, character in enumerate(text):
        rows = glyph(character)
        origin = x + index * ADVANCE * scale
        for row, bits in enumerate(rows):
            for column in range(GLYPH_WIDTH):
                if not bits & (1 << (GLYPH_WIDTH - 1 - column)):
                    continue
                for dy in range(scale):
                    py = y + row * scale + dy
                    if not 0 <= py < height:
                        continue
                    for dx in range(scale):
                        px = origin + column * scale + dx
                        if 0 <= px < width:
                            offset = (py * width + px) * 4
                            pixels[offset:offset + 4] = bytes((*color, 255))
