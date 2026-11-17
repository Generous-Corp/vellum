#pragma once

#include "include/core/SkColor.h"
#include "modules/skparagraph/include/Paragraph.h"

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

// Paragraph shaping over Vellum's packaged fonts, free of scene types: a
// request names its runs, styles and width, and the result is a laid-out
// SkParagraph. Every paragraph is built and laid out under one lock around
// the shared packaged-font collection.
namespace vellum::graphics::detail {

/// Where each line of a wrapped paragraph sits within its width, on a
/// left-to-right base direction.
enum class ParagraphAlign : std::uint8_t {
    start,
    center,
    end,
    /// Stretches every line but the last to the width.
    justify,
};

struct ParagraphRunStyle final {
    /// Empty selects Inter.
    std::string font_family;
    /// Clamped to [100, 900].
    int font_weight = 400;
    /// At least one.
    float font_size = 14.0F;
    float letter_spacing = 0.0F;
    SkColor4f color{0.0F, 0.0F, 0.0F, 1.0F};
    bool underline = false;
    bool strikethrough = false;
};

struct ParagraphRun final {
    std::string text;
    ParagraphRunStyle style;
};

/// How a paragraph breaks into lines.
struct ParagraphWrap final {
    /// The width lines break at; a negative width is zero and a non-finite
    /// one does not wrap.
    float width = 0.0F;
    ParagraphAlign align = ParagraphAlign::start;
    /// Each line's height as a multiple of its largest font size, the extra
    /// space split above and below the glyphs; zero keeps the fonts' spacing.
    float line_height = 0.0F;
    /// The most lines laid out; zero lays out every line.
    std::uint32_t max_lines = 0;
    /// Ends the last line with U+2026 when `max_lines` cuts the text.
    bool ellipsis = false;
};

/// How a character no packaged face covers is drawn.
enum class MissingGlyphs : std::uint8_t {
    /// Replaced with `?`.
    question_mark,
    /// Drawn as the face's missing-glyph (.notdef) box.
    notdef_box,
};

struct ParagraphRequest final {
    /// The packaged font directory; empty resolves the installed one.
    std::string font_directory;
    std::vector<ParagraphRun> runs;
    /// Absent lays out one unwrapped, left-aligned line.
    std::optional<ParagraphWrap> wrap;
    MissingGlyphs missing_glyphs = MissingGlyphs::question_mark;
};

struct ShapedParagraph final {
    std::shared_ptr<skia::textlayout::Paragraph> paragraph;
    /// The longest line of a wrapped paragraph, or the unwrapped line's width.
    float width = 0.0F;
    float ascent = 0.0F;
    /// The paragraph's height below the first baseline.
    float descent = 0.0F;
    float baseline = 0.0F;
    std::uint32_t lines = 1U;
    /// The code points the shaper found no face for, ascending.
    std::vector<std::uint32_t> unresolved;
};

/// Shapes and lays out `request`. Each run tries its own family, then Noto
/// Sans Arabic, Noto Sans JP and Inter, character by character; host fonts are
/// never consulted. Invalid UTF-8 is replaced with `?`.
[[nodiscard]] bool shape_paragraph(const ParagraphRequest& request, ShapedParagraph& shaped,
                                   std::string* error = nullptr);

/// One run of text whose coverage `find_missing_glyphs` checks.
struct GlyphQuery final {
    std::string_view font_family;
    std::string_view text;
};

/// For each query, the code points of its text that `shape_paragraph` cannot
/// draw under `policy`, in text order: with `notdef_box` those no face of the
/// run's families covers, leaving out default-ignorable ones the shaper draws
/// as nothing; with `question_mark` those no packaged face covers. Invalid
/// sequences are not reported. Each text's result is remembered, so a repeat
/// is not scanned again. Empty when the fonts cannot be loaded.
[[nodiscard]] std::vector<std::vector<std::uint32_t>> find_missing_glyphs(
    std::string_view font_directory, const std::vector<GlyphQuery>& queries,
    MissingGlyphs policy);

}  // namespace vellum::graphics::detail
