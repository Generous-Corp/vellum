#include "text_paragraph.hpp"

#include "utf8_text.hpp"

#include "include/core/SkFontMgr.h"
#include "include/core/SkFontStyle.h"
#include "include/core/SkPaint.h"
#include "include/core/SkString.h"
#include "include/core/SkTypeface.h"
#include "include/ports/SkFontMgr_mac_ct.h"
#include "modules/skparagraph/include/FontCollection.h"
#include "modules/skparagraph/include/ParagraphBuilder.h"
#include "modules/skparagraph/include/ParagraphStyle.h"
#include "modules/skparagraph/include/TextStyle.h"
#include "modules/skparagraph/include/TypefaceFontProvider.h"
#include "modules/skunicode/include/SkUnicode_icu.h"

#include <dlfcn.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <filesystem>
#include <mutex>
#include <unordered_map>
#include <utility>

namespace vellum::graphics::detail {
namespace {

void set_error(std::string* destination, std::string message) {
    if (destination != nullptr) *destination = std::move(message);
}

std::filesystem::path font_directory(std::string_view requested) {
    if (!requested.empty()) return std::filesystem::path(requested);
    Dl_info info{};
    if (dladdr(reinterpret_cast<const void*>(&font_directory), &info) != 0 &&
        info.dli_fname != nullptr) {
        const auto library_directory = std::filesystem::path(info.dli_fname).parent_path();
        const auto bundled = library_directory.parent_path() /
            "Resources" / "vellum" / "fonts";
        if (std::filesystem::is_directory(bundled)) return bundled;
        const auto installed = library_directory /
            VELLUM_DATADIR_FROM_LIBDIR / "vellum" / "fonts";
        if (std::filesystem::is_directory(installed)) return installed;
        const auto build_tree = library_directory /
            VELLUM_BUILD_DATADIR_FROM_PROVIDER_DIR / "vellum" / "fonts";
        if (std::filesystem::is_directory(build_tree)) return build_tree;
    }
    return {};
}

SkFontStyle font_style(const ParagraphRunStyle& style) {
    return {std::clamp(style.font_weight, 100, 900),
            SkFontStyle::kNormal_Width, SkFontStyle::kUpright_Slant};
}

struct PackagedFontSet {
    sk_sp<skia::textlayout::FontCollection> collection;
    std::vector<sk_sp<SkTypeface>> typefaces;
    /// The family name of each of `typefaces`.
    std::vector<SkString> families;
    /// The code points each run text was found to miss, by policy, family and
    /// text, so an unchanged text is not scanned again every frame.
    std::unordered_map<std::string, std::vector<std::uint32_t>> missing;
    std::size_t missing_bytes = 0;
};

bool is_nonrendering_code_point(std::uint32_t code_point) {
    return code_point <= 0x1FU || (code_point >= 0x7FU && code_point <= 0x9FU) ||
        (code_point >= 0x200BU && code_point <= 0x200FU) || code_point == 0x2060U ||
        (code_point >= 0x202AU && code_point <= 0x202EU) ||
        (code_point >= 0x2066U && code_point <= 0x2069U) ||
        (code_point >= 0xFE00U && code_point <= 0xFE0FU) ||
        (code_point >= 0xE0100U && code_point <= 0xE01EFU);
}

bool has_packaged_glyph(
    std::uint32_t code_point,
    const std::vector<sk_sp<SkTypeface>>& typefaces) {
    if (is_nonrendering_code_point(code_point)) return true;
    return std::any_of(typefaces.begin(), typefaces.end(), [code_point](const auto& face) {
        return face->unicharToGlyph(static_cast<SkUnichar>(code_point)) != 0U;
    });
}

std::mutex& text_mutex() {
    static std::mutex mutex;
    return mutex;
}

// The segmenter every paragraph is shaped with. The caller holds `text_mutex`.
const sk_sp<SkUnicode>& text_unicode() {
    static const sk_sp<SkUnicode> unicode = SkUnicodes::ICU::Make();
    return unicode;
}

// The packaged faces in `directory`, loaded once per directory. The caller
// holds `text_mutex`.
PackagedFontSet* packaged_fonts(const std::filesystem::path& directory,
                                std::string* error) {
    static const sk_sp<SkFontMgr> manager = SkFontMgr_New_CoreText(nullptr);
    static std::unordered_map<std::string, PackagedFontSet> font_cache;
    if (!manager || directory.empty() || !std::filesystem::is_directory(directory)) {
        set_error(error, "Vellum packaged font directory is unavailable: " + directory.string());
        return nullptr;
    }
    const auto cache_key = directory.lexically_normal().string();
    auto cached = font_cache.find(cache_key);
    if (cached == font_cache.end()) {
        PackagedFontSet loaded;
        auto provider = sk_make_sp<skia::textlayout::TypefaceFontProvider>();
        constexpr std::array<std::string_view, 7> packaged_fonts{
            "Inter-Regular.ttf", "Jost-Regular.ttf", "Jost-Medium.ttf",
            "Jost-SemiBold.ttf", "Jost-Bold.ttf", "NotoSansJP[wght].ttf",
            "NotoSansArabic[wdth,wght].ttf",
        };
        for (const auto filename : packaged_fonts) {
            const auto path = directory / filename;
            auto typeface = manager->makeFromFile(path.c_str());
            if (!typeface) {
                set_error(error, "Vellum packaged font is unavailable: " + path.string());
                return nullptr;
            }
            SkString family;
            typeface->getFamilyName(&family);
            loaded.families.push_back(std::move(family));
            loaded.typefaces.push_back(typeface);
            provider->registerTypeface(std::move(typeface));
        }
        if (!has_packaged_glyph(static_cast<std::uint32_t>('?'), loaded.typefaces)) {
            set_error(error, "Vellum packaged fonts have no unsupported-text placeholder");
            return nullptr;
        }
        loaded.collection = sk_make_sp<skia::textlayout::FontCollection>();
        loaded.collection->setAssetFontManager(provider);
        loaded.collection->setDefaultFontManager(provider, std::vector<SkString>{
            SkString("Inter"), SkString("Noto Sans JP"), SkString("Noto Sans Arabic")});
        loaded.collection->disableFontFallback();
        cached = font_cache.emplace(cache_key, std::move(loaded)).first;
    }
    return &cached->second;
}

// The families a run is shaped with, in the order the shaper tries them for
// each character: its own, then the packaged fallbacks.
std::vector<SkString> run_families(std::string_view font_family) {
    return {
        font_family.empty() ? SkString("Inter") : SkString(font_family.data(), font_family.size()),
        SkString("Noto Sans Arabic"),
        SkString("Noto Sans JP"),
        SkString("Inter"),
    };
}

// Whether a face of one of `families` has a glyph for `code_point`.
bool families_cover(std::uint32_t code_point, const std::vector<SkString>& families,
                    const PackagedFontSet& fonts) {
    if (is_nonrendering_code_point(code_point)) return true;
    for (std::size_t index = 0; index < fonts.typefaces.size(); ++index) {
        const bool listed = std::any_of(families.begin(), families.end(),
            [&](const SkString& family) { return family.equals(fonts.families[index]); });
        if (listed &&
            fonts.typefaces[index]->unicharToGlyph(static_cast<SkUnichar>(code_point)) != 0U) {
            return true;
        }
    }
    return false;
}

// The code points of `query` that `shape_paragraph` cannot draw under
// `policy`, remembered per text in `fonts`. The caller holds `text_mutex`.
void collect_missing_glyphs(const GlyphQuery& query, MissingGlyphs policy,
                            PackagedFontSet& fonts, std::vector<std::uint32_t>& missing) {
    const auto text = query.text;
    if (std::all_of(text.begin(), text.end(),
                    [](char byte) { return static_cast<unsigned char>(byte) < 0x80U; })) {
        return;
    }
    const bool box = policy == MissingGlyphs::notdef_box;
    const auto family = query.font_family;
    std::string key = (box ? "b" : "q") + std::to_string(family.size()) + ":";
    key += family;
    key += text;
    if (const auto found = fonts.missing.find(key); found != fonts.missing.end()) {
        missing.insert(missing.end(), found->second.begin(), found->second.end());
        return;
    }
    const auto families = run_families(family);
    const auto& unicode = text_unicode();
    std::vector<std::uint32_t> found;
    for (std::size_t offset = 0U; offset < text.size();) {
        const auto decoded = decode_utf8(text, offset);
        offset = decoded.end;
        if (!decoded.valid) continue;
        const bool covered = box
            ? families_cover(decoded.code_point, families, fonts) ||
                  (unicode && unicode->isDefaultIgnorable(static_cast<SkUnichar>(decoded.code_point)))
            : has_packaged_glyph(decoded.code_point, fonts.typefaces);
        if (!covered) found.push_back(decoded.code_point);
    }
    constexpr std::size_t kMissingBudget = 8U * 1024U * 1024U;
    const std::size_t bytes = key.size() + found.size() * sizeof(std::uint32_t) + 64U;
    if (fonts.missing_bytes + bytes > kMissingBudget) {
        fonts.missing.clear();
        fonts.missing_bytes = 0;
    }
    missing.insert(missing.end(), found.begin(), found.end());
    fonts.missing.emplace(std::move(key), std::move(found));
    fonts.missing_bytes += bytes;
}

skia::textlayout::TextAlign skia_text_align(ParagraphAlign align) {
    switch (align) {
    case ParagraphAlign::start: return skia::textlayout::TextAlign::kStart;
    case ParagraphAlign::center: return skia::textlayout::TextAlign::kCenter;
    case ParagraphAlign::end: return skia::textlayout::TextAlign::kEnd;
    case ParagraphAlign::justify: return skia::textlayout::TextAlign::kJustify;
    }
    return skia::textlayout::TextAlign::kStart;
}

}  // namespace

bool shape_paragraph(const ParagraphRequest& request, ShapedParagraph& shaped,
                     std::string* error) {
    const std::scoped_lock lock(text_mutex());
    const auto& unicode = text_unicode();
    if (!unicode) {
        set_error(error, "Vellum could not create the ICU text segmenter");
        return false;
    }
    const auto* fonts = packaged_fonts(font_directory(request.font_directory), error);
    if (fonts == nullptr) return false;

    const auto& wrap = request.wrap;
    skia::textlayout::ParagraphStyle paragraph_style;
    if (wrap) {
        paragraph_style.setTextAlign(skia_text_align(wrap->align));
        if (wrap->max_lines > 0U) paragraph_style.setMaxLines(wrap->max_lines);
        if (wrap->ellipsis) paragraph_style.setEllipsis(SkString("\xE2\x80\xA6"));
    } else {
        paragraph_style.setMaxLines(1U);
        paragraph_style.setTextAlign(skia::textlayout::TextAlign::kLeft);
    }
    auto builder = skia::textlayout::ParagraphBuilder::make(
        paragraph_style, fonts->collection, unicode);
    if (!builder) {
        set_error(error, "Vellum could not create the attributed-text paragraph builder");
        return false;
    }

    const bool box = request.missing_glyphs == MissingGlyphs::notdef_box;
    for (const auto& requested : request.runs) {
        if (requested.text.empty()) continue;
        skia::textlayout::TextStyle style;
        style.setFontFamilies(run_families(requested.style.font_family));
        style.setFontStyle(font_style(requested.style));
        style.setFontSize(std::max(1.0F, requested.style.font_size));
        style.setLetterSpacing(requested.style.letter_spacing);
        style.setFontEdging(SkFont::Edging::kAntiAlias);
        if (wrap && wrap->line_height > 0.0F) {
            style.setHeight(wrap->line_height);
            style.setHeightOverride(true);
            style.setHalfLeading(true);
        }
        SkPaint foreground;
        foreground.setAntiAlias(true);
        foreground.setColor4f(requested.style.color);
        style.setForegroundPaint(foreground);
        int decoration = skia::textlayout::kNoDecoration;
        if (requested.style.underline) decoration |= skia::textlayout::kUnderline;
        if (requested.style.strikethrough) decoration |= skia::textlayout::kLineThrough;
        style.setDecoration(static_cast<skia::textlayout::TextDecoration>(decoration));
        style.setDecorationColor(foreground.getColor());
        builder->pushStyle(style);
        const auto text = box
            ? replace_unsupported_text(requested.text, [](std::uint32_t) { return true; })
            : replace_unsupported_text(requested.text, [fonts](std::uint32_t code_point) {
                  return has_packaged_glyph(code_point, fonts->typefaces);
              });
        builder->addText(text.data(), text.size());
        builder->pop();
    }

    auto paragraph = builder->Build();
    if (!paragraph) {
        set_error(error, "Vellum could not build the attributed-text paragraph");
        return false;
    }
    if (wrap) {
        paragraph->layout(std::isfinite(wrap->width) ? std::max(0.0F, wrap->width)
                                                     : 1000000000.0F);
    } else {
        paragraph->layout(1000000000.0F);
    }
    shaped.width = wrap ? paragraph->getLongestLine() : paragraph->getMaxIntrinsicWidth();
    shaped.ascent = paragraph->getAlphabeticBaseline();
    shaped.descent =
        std::max(0.0F, paragraph->getHeight() - paragraph->getAlphabeticBaseline());
    shaped.baseline = paragraph->getAlphabeticBaseline();
    shaped.lines = wrap ? static_cast<std::uint32_t>(paragraph->lineNumber()) : 1U;
    const auto unresolved = paragraph->unresolvedCodepoints();
    shaped.unresolved.assign(unresolved.begin(), unresolved.end());
    std::sort(shaped.unresolved.begin(), shaped.unresolved.end());
    shaped.paragraph = std::shared_ptr<skia::textlayout::Paragraph>(std::move(paragraph));
    return true;
}

std::vector<std::vector<std::uint32_t>> find_missing_glyphs(
    std::string_view requested_directory, const std::vector<GlyphQuery>& queries,
    MissingGlyphs policy) {
    std::vector<std::vector<std::uint32_t>> output;
    const std::scoped_lock lock(text_mutex());
    auto* fonts = packaged_fonts(font_directory(requested_directory), nullptr);
    if (fonts == nullptr) return output;
    output.resize(queries.size());
    for (std::size_t index = 0; index < queries.size(); ++index) {
        collect_missing_glyphs(queries[index], policy, *fonts, output[index]);
    }
    return output;
}

}  // namespace vellum::graphics::detail
