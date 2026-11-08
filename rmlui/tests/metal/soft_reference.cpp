// Renders the Metal visual-test documents with the soft renderer and writes
// each frame as premultiplied RGBA8: the reference metal_visual_test compares
// the Metal renderer with.
#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/soft_renderer.hpp>

#include "visual_common.hpp"

#include <cstdio>
#include <filesystem>

using namespace vellum::rmlui;
using namespace vellum::rmlui::metal_test;

int main(int argc, char** argv) {
    if (argc != 4) {
        std::fprintf(stderr, "usage: %s <rmlui-source> <fixtures> <output-dir>\n", argv[0]);
        return 2;
    }
    if (!Backend::fonts_enabled()) {
        std::fprintf(stderr, "the visual references need VELLUM_RMLUI_FONTS=ON\n");
        return 2;
    }
    DirectoryAssets assets(argv[1], argv[2]);
    const std::filesystem::path out_dir(argv[3]);
    std::filesystem::create_directories(out_dir);
    for (const char* document : kDocuments) {
        SoftRenderer renderer(0x00000000u);
        HeadlessRecorder lua;
        Backend backend(assets, renderer, lua);
        if (backend.open(kWidth, kHeight) != Status::ok)
            return 1;
        for (const auto& font : kFonts) {
            FontFaceOptions options;
            options.style = font.italic ? FontStyle::italic : FontStyle::normal;
            options.weight = font.weight;
            if (backend.load_font_face(font.path, "LatoLatin", options) != Status::ok) {
                std::fprintf(stderr, "font failed: %s\n", font.path);
                return 1;
            }
        }
        if (backend.load_document(document) != Status::ok) {
            std::fprintf(stderr, "document failed: %s\n", document);
            return 1;
        }
        backend.render();
        backend.render(); // a second pass, after layout settles
        if (!write_rgba(out_dir / reference_name(document), renderer.pixels()))
            return 1;
        backend.close();
    }
    return 0;
}
