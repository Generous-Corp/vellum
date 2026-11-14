#pragma once

// Shared by the Metal visual test and the soft-renderer reference that it is
// compared with. The two run in separate processes: the soft renderer draws
// through VellumRmlUi::Core and the Metal renderer through
// VellumRmlUi::Native, and each carries its own copy of RmlUi.

#include <vellum/rmlui/assets.hpp>
#include <vellum/rmlui/image_compare.hpp>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <string_view>
#include <vector>

namespace vellum::rmlui::metal_test {

inline constexpr std::uint32_t kWidth = 512;
inline constexpr std::uint32_t kHeight = 512;

// The fonts the RmlUi visual-test stylesheet names, from RmlUi's own sources.
// Both renderers register them with the same explicit family, style and
// weight, so neither depends on what the font files report.
struct Font {
    const char* path;
    bool italic;
    std::uint16_t weight;
};
inline constexpr Font kFonts[] = {
    {"rmlui/Samples/assets/LatoLatin-Regular.ttf", false, 400},
    {"rmlui/Samples/assets/LatoLatin-Bold.ttf", false, 700},
    {"rmlui/Samples/assets/LatoLatin-Italic.ttf", true, 400},
    {"rmlui/Samples/assets/LatoLatin-BoldItalic.ttf", true, 700},
};

// Documents this renderer version draws completely: RmlUi visual tests that
// link the test stylesheet relatively (some name it from RmlUi's own test
// shell root, which an asset provider cannot resolve) and use no clip mask,
// layer, filter or shader (the Metal test fails if one
// does), plus this module's fixtures: translucent fills and text, which the
// blend control needs, and a loaded image, transforms and a scissored child.
inline constexpr const char* kDocuments[] = {
    "rmlui/Tests/Data/VisualTests/border_radius.rml",
    "rmlui/Tests/Data/VisualTests/box_sizing.rml",
    "rmlui/Tests/Data/VisualTests/css1_clear.rml",
    "rmlui/Tests/Data/VisualTests/flex_01.rml",
    "rmlui/Tests/Data/VisualTests/float_placement.rml",
    "rmlui/Tests/Data/VisualTests/float_basic.rml",
    "rmlui/Tests/Data/VisualTests/inline_block.rml",
    "rmlui/Tests/Data/VisualTests/inline_formatting_01.rml",
    "rmlui/Tests/Data/VisualTests/letter_spacing.rml",
    "rmlui/Tests/Data/VisualTests/position_01_normal_flow.rml",
    "rmlui/Tests/Data/VisualTests/position_06_absolute_positioning.rml",
    "rmlui/Tests/Data/VisualTests/position_02_relative_positioning.rml",
    "rmlui/Tests/Data/VisualTests/inline_formatting_02.rml",
    "fixtures/translucent.rml",
    "fixtures/geometry.rml",
    "fixtures/atlas_filter.rml",
};

inline bool atlas_filter_document(std::string_view document) {
    return document == "fixtures/atlas_filter.rml";
}

// The documents whose output depends on translucent blending.
inline bool translucent(std::string_view document) {
    return document == "fixtures/translucent.rml";
}

// Reads files under two roots: "rmlui/..." from the pinned RmlUi source and
// "fixtures/..." from this module's test fixtures. Paths are normalised, and
// anything that escapes its root is refused.
class DirectoryAssets final : public AssetProvider {
  public:
    DirectoryAssets(std::filesystem::path rmlui_source, std::filesystem::path fixtures)
        : rmlui_(std::move(rmlui_source)), fixtures_(std::move(fixtures)) {}

    Status load(std::string_view path, Asset& out) override {
        const std::filesystem::path relative = std::filesystem::path(path).lexically_normal();
        if (relative.is_absolute() || relative.empty() || *relative.begin() == "..")
            return Status::rejected;
        const auto head = *relative.begin();
        std::filesystem::path root;
        if (head == "rmlui")
            root = rmlui_;
        else if (head == "fixtures")
            root = fixtures_;
        else
            return Status::missing_asset;
        const auto full = root / relative.lexically_relative(head);
        std::ifstream stream(full, std::ios::binary);
        if (!stream)
            return Status::missing_asset;
        out.path = relative.generic_string();
        out.bytes.assign(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
        return Status::ok;
    }

  private:
    std::filesystem::path rmlui_;
    std::filesystem::path fixtures_;
};

inline std::string reference_name(std::string_view document) {
    std::string name(document);
    for (auto& c : name)
        if (c == '/' || c == '.')
            c = '_';
    return name + ".rgba";
}

// Premultiplied RGBA8, kWidth x kHeight, top row first.
inline bool write_rgba(const std::filesystem::path& file, const std::vector<std::uint8_t>& pixels) {
    std::ofstream stream(file, std::ios::binary | std::ios::trunc);
    stream.write(reinterpret_cast<const char*>(pixels.data()),
                 static_cast<std::streamsize>(pixels.size()));
    return static_cast<bool>(stream);
}

inline bool read_rgba(const std::filesystem::path& file, std::vector<std::uint8_t>& pixels) {
    std::ifstream stream(file, std::ios::binary);
    if (!stream)
        return false;
    pixels.assign(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
    return pixels.size() == static_cast<std::size_t>(kWidth) * kHeight * 4u;
}

} // namespace vellum::rmlui::metal_test
