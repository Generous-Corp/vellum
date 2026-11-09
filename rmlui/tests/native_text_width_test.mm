// Text width from RmlUi's default font engine, as this module builds it (with
// rmlui-fractional-glyph-advances.patch), against CoreText's fractional,
// kerned layout of the same font file at the same pixel size. Arial and
// Verdana keep their kerning in the legacy 'kern' table both read, so the two
// must agree to within a pixel on every string; summing whole-pixel advances
// instead came out up to 5 % wider (about 10 px on the pangram at 10 px).
#include <vellum/rmlui/native.hpp>

#include "test_support.hpp"

#import <CoreText/CoreText.h>
#import <Foundation/Foundation.h>

#include <RmlUi/Core.h>

#include <cmath>
#include <cstdio>
#include <filesystem>
#include <map>
#include <string>
#include <vector>

using namespace vellum::rmlui;

namespace {

constexpr int kSkip = 77;

class NullRenderer final : public Rml::RenderInterface {
  public:
    Rml::CompiledGeometryHandle CompileGeometry(Rml::Span<const Rml::Vertex>, Rml::Span<const int>) override {
        return 1;
    }
    void RenderGeometry(Rml::CompiledGeometryHandle, Rml::Vector2f, Rml::TextureHandle) override {}
    void ReleaseGeometry(Rml::CompiledGeometryHandle) override {}
    Rml::TextureHandle LoadTexture(Rml::Vector2i&, const Rml::String&) override {
        return 0;
    }
    Rml::TextureHandle GenerateTexture(Rml::Span<const Rml::byte>, Rml::Vector2i) override {
        return 1;
    }
    void ReleaseTexture(Rml::TextureHandle) override {}
    void EnableScissorRegion(bool) override {}
    void SetScissorRegion(Rml::Rectanglei) override {}
};

class NoAssets final : public AssetProvider {
  public:
    Status load(std::string_view, Asset&) override {
        return Status::missing_asset;
    }
};

double coretext_width(CTFontRef font, const std::string& text) {
    CFStringRef string = CFStringCreateWithCString(nullptr, text.c_str(), kCFStringEncodingUTF8);
    const void* keys[] = {kCTFontAttributeName};
    const void* values[] = {font};
    CFDictionaryRef attributes = CFDictionaryCreate(nullptr, keys, values, 1, &kCFTypeDictionaryKeyCallBacks,
                                                    &kCFTypeDictionaryValueCallBacks);
    CFAttributedStringRef attributed = CFAttributedStringCreate(nullptr, string, attributes);
    CTLineRef line = CTLineCreateWithAttributedString(attributed);
    const double width = CTLineGetTypographicBounds(line, nullptr, nullptr, nullptr);
    CFRelease(line);
    CFRelease(attributed);
    CFRelease(attributes);
    CFRelease(string);
    return width;
}

} // namespace

int main() {
    @autoreleasepool {
        const std::map<std::string, std::string> fonts = {
            {"Arial", "/System/Library/Fonts/Supplemental/Arial.ttf"},
            {"Verdana", "/System/Library/Fonts/Supplemental/Verdana.ttf"},
        };
        for (const auto& [family, path] : fonts) {
            if (!std::filesystem::exists(path)) {
                std::printf("SKIP: %s is not installed at %s\n", family.c_str(), path.c_str());
                return kSkip;
            }
        }
        if (!NativeContext::fonts_enabled()) {
            std::printf("SKIP: built without VELLUM_RMLUI_FONTS\n");
            return kSkip;
        }
        const std::vector<std::string> strings = {
            "Cutoff", "Resonance", "Filter Envelope", "LFO 2 Destination", "Patch Browser", "Donate",
            "AVATAR Wave Tables", "Volume", "1234567890", "The quick brown fox jumps over the lazy dog"};
        const int sizes[] = {10, 11, 12, 13, 14, 16, 20, 24, 28};

        NoAssets assets;
        NullRenderer renderer;
        NativeContext native(assets, renderer);
        VELLUM_RMLUI_CHECK(native.open(64, 64) == Status::ok);
        int failures = 0;
        int compared = 0;
        double worst = 0.0;
        {
            NativeContext::Scope scope(native);
            std::vector<std::string> bytes;
            bytes.reserve(fonts.size());
            for (const auto& [family, path] : fonts) {
                bytes.push_back(test::read_binary(path));
                const auto& data = bytes.back();
                VELLUM_RMLUI_CHECK(Rml::LoadFontFace(
                    Rml::Span<const Rml::byte>(reinterpret_cast<const Rml::byte*>(data.data()), data.size()),
                    family, Rml::Style::FontStyle::Normal, Rml::Style::FontWeight::Normal));
            }
            Rml::FontEngineInterface* engine = Rml::GetFontEngineInterface();
            const Rml::String language;
            const Rml::TextShapingContext shaping{language};
            for (const auto& [family, path] : fonts) {
                CGDataProviderRef provider = CGDataProviderCreateWithFilename(path.c_str());
                CGFontRef cg_font = CGFontCreateWithDataProvider(provider);
                for (const int size : sizes) {
                    const Rml::FontFaceHandle handle = engine->GetFontFaceHandle(
                        family, Rml::Style::FontStyle::Normal, Rml::Style::FontWeight::Normal, size);
                    VELLUM_RMLUI_CHECK(handle != 0);
                    CTFontRef ct_font = CTFontCreateWithGraphicsFont(cg_font, size, nullptr, nullptr);
                    for (const auto& text : strings) {
                        const int rmlui = engine->GetStringWidth(handle, text, shaping);
                        const double reference = coretext_width(ct_font, text);
                        const double delta = rmlui - reference;
                        worst = std::max(worst, std::abs(delta));
                        ++compared;
                        if (std::abs(delta) > 1.0) {
                            ++failures;
                            std::printf("%-8s %2d px %-45s rmlui %4d coretext %7.2f (%+.2f)\n", family.c_str(), size,
                                        text.c_str(), rmlui, reference, delta);
                        }
                    }
                    CFRelease(ct_font);
                }
                CGFontRelease(cg_font);
                CGDataProviderRelease(provider);
            }
        }
        native.close();
        std::printf("%d widths compared, %d more than 1 px from CoreText, worst %.2f px\n", compared, failures, worst);
        // The control: the comparison saw every string at every size.
        VELLUM_RMLUI_CHECK(compared == static_cast<int>(fonts.size() * strings.size() * std::size(sizes)));
        return failures == 0 ? 0 : 1;
    }
}
