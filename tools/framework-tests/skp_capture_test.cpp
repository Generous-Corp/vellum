// SkpFrameCapture: records a frame's draw ops through the Canvas interface and
// serializes a `.skp` artifact. The capture is backend-independent and needs
// no GPU, so these cases run everywhere. Skia's own symbols are private to the
// provider library, so a blob is judged by its documented file signature, its
// recorded op count, and (for embedded images) the PNG payload it carries,
// never by deserializing it. The image proc that PNG-encodes GPU-texture-backed
// images is not reachable from here: Skia falls back to its own encoder for a
// raster image, so a payload check cannot tell the proc from its absence.

#include "framework_test.hpp"

#include <vellum/canvas/canvas.hpp>
#include <vellum/render/headless_surface.hpp>
#include <vellum/render/skp_capture.hpp>

#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <system_error>
#include <vector>

namespace {

namespace fs = std::filesystem;
using vellum::render::SkpFrameCapture;

constexpr char kSkpSignature[] = "skiapict";
constexpr unsigned char kPngSignature[] = {0x89, 'P', 'N', 'G'};

bool has_skp_signature(const std::string& blob) {
    return blob.size() >= 8 && std::memcmp(blob.data(), kSkpSignature, 8) == 0;
}

bool contains_png(const std::string& blob) {
    return blob.find(std::string(reinterpret_cast<const char*>(kPngSignature), 4)) !=
           std::string::npos;
}

std::string read_file(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

void paint_solid(vellum::canvas::Canvas& c, uint8_t r, uint8_t g, uint8_t b, float size) {
    c.set_fill_color(vellum::canvas::Color::rgba8(r, g, b));
    c.fill_rect(0.0f, 0.0f, size, size);
}

std::vector<uint8_t> solid_png(uint32_t size, uint8_t r, uint8_t g, uint8_t b) {
    vellum::render::HeadlessSurface::Rgba src;
    src.width = size;
    src.height = size;
    src.pixels.resize(static_cast<size_t>(size) * size * 4u);
    for (size_t i = 0; i < static_cast<size_t>(size) * size; ++i) {
        src.pixels[i * 4 + 0] = r;
        src.pixels[i * 4 + 1] = g;
        src.pixels[i * 4 + 2] = b;
        src.pixels[i * 4 + 3] = 255;
    }
    return vellum::render::HeadlessSurface::encode_png(src);
}

FRAMEWORK_TEST(vector_ops_serialize_to_a_skp_blob_and_consume_the_capture) {
    CHECK(vellum::render::skp_capture_supported());
    SkpFrameCapture capture(64, 48);
    CHECK(capture.available());
    auto* c = capture.canvas();
    CHECK(c != nullptr);
    if (!c)
        return;
    c->set_fill_color(vellum::canvas::Color::rgba8(255, 0, 0));
    c->fill_rect(4.0f, 4.0f, 40.0f, 24.0f);
    c->set_fill_color(vellum::canvas::Color::rgba8(0, 0, 255));
    c->fill_rect(20.0f, 20.0f, 24.0f, 16.0f);

    std::string blob;
    const auto result = capture.finish_to_memory(blob);
    CHECK(result.ok);
    CHECK(result.bytes_written == blob.size());
    CHECK(result.op_count > 0);
    CHECK(has_skp_signature(blob));

    // The capture is consumed: no canvas, and a second finish fails.
    CHECK(capture.canvas() == nullptr);
    CHECK(!capture.available());
    std::string second;
    CHECK(!capture.finish_to_memory(second).ok);
}

FRAMEWORK_TEST(more_drawing_records_more_ops) {
    auto op_count = [](int rects) {
        SkpFrameCapture capture(32, 32);
        auto* c = capture.canvas();
        if (!c)
            return std::size_t{0};
        for (int i = 0; i < rects; ++i)
            paint_solid(*c, 10, 20, 30, 8.0f + static_cast<float>(i));
        std::string blob;
        return capture.finish_to_memory(blob).op_count;
    };
    const auto few = op_count(1);
    const auto many = op_count(6);
    CHECK(few > 0);
    CHECK(many > few);
}

FRAMEWORK_TEST(a_capture_writes_a_loadable_skp_file) {
    const std::string path = (fs::temp_directory_path() / "vellum-skp-capture-test.skp").string();
    std::error_code ec;
    fs::remove(path, ec);

    const auto result = vellum::render::capture_skp_to_file(
        32, 32, path, [](vellum::canvas::Canvas& c) { paint_solid(c, 0, 255, 0, 32.0f); });
    CHECK(result.ok);
    CHECK(result.path == path);
    CHECK(result.bytes_written > 0);
    CHECK(fs::exists(path));
    CHECK(fs::file_size(path) == result.bytes_written);
    CHECK(has_skp_signature(read_file(path)));
    fs::remove(path, ec);
}

// An image drawn into the capture must reach the blob with its pixels as a PNG
// payload. The vector-only capture is the control: it carries no PNG, so the
// check cannot be satisfied by the container format alone.
FRAMEWORK_TEST(an_image_drawn_into_the_capture_reaches_the_blob_as_a_png) {
    const auto png = solid_png(8, 0, 255, 0);
    CHECK(!png.empty());

    SkpFrameCapture with_image(8, 8);
    auto* c = with_image.canvas();
    CHECK(c != nullptr);
    if (!c)
        return;
    CHECK(c->draw_image_from_data(png.data(), png.size(), 0.0f, 0.0f, 8.0f, 8.0f));
    std::string image_blob;
    const auto image_result = with_image.finish_to_memory(image_blob);
    CHECK(image_result.ok);
    CHECK(image_result.op_count > 0);
    CHECK(contains_png(image_blob));

    SkpFrameCapture vector_only(8, 8);
    auto* v = vector_only.canvas();
    CHECK(v != nullptr);
    if (!v)
        return;
    paint_solid(*v, 0, 255, 0, 8.0f);
    std::string vector_blob;
    CHECK(vector_only.finish_to_memory(vector_blob).ok);
    CHECK(!contains_png(vector_blob));
}

FRAMEWORK_TEST(a_failed_write_leaves_no_file_behind) {
    const std::string bad_dir = (fs::temp_directory_path() / "vellum-skp-no-such-dir-xyz").string();
    std::error_code ec;
    fs::remove_all(bad_dir, ec);
    const std::string dest = bad_dir + "/frame.skp";

    const auto result = vellum::render::capture_skp_to_file(
        16, 16, dest, [](vellum::canvas::Canvas& c) { paint_solid(c, 255, 0, 0, 16.0f); });
    CHECK(!result.ok);
    CHECK(!result.reason.empty());
    CHECK(!fs::exists(dest));
    CHECK(!fs::exists(dest + ".tmp"));
}

FRAMEWORK_TEST(a_failed_write_leaves_a_prior_valid_capture_intact) {
    const std::string path = (fs::temp_directory_path() / "vellum-skp-atomic-prior.skp").string();
    std::error_code ec;
    fs::remove(path, ec);

    const auto first = vellum::render::capture_skp_to_file(
        16, 16, path, [](vellum::canvas::Canvas& c) { paint_solid(c, 0, 255, 0, 16.0f); });
    CHECK(first.ok);
    const auto original_size = fs::exists(path) ? fs::file_size(path) : 0u;
    const std::string original_bytes = read_file(path);
    CHECK(original_size > 0);

    // A non-empty directory squats on the sibling temp path, so the write
    // cannot open it and survives the unlink attempt.
    const std::string tmp_as_dir = path + ".tmp";
    fs::remove_all(tmp_as_dir, ec);
    fs::create_directory(tmp_as_dir);
    { std::ofstream guard(tmp_as_dir + "/keep"); guard << "x"; }

    const auto second = vellum::render::capture_skp_to_file(
        16, 16, path, [](vellum::canvas::Canvas& c) { paint_solid(c, 0, 0, 255, 16.0f); });
    CHECK(!second.ok);
    CHECK(!second.reason.empty());

    // The earlier capture is the same file with the same bytes.
    CHECK(fs::exists(path));
    CHECK(fs::file_size(path) == original_size);
    CHECK(read_file(path) == original_bytes);

    fs::remove_all(tmp_as_dir, ec);
    fs::remove(path, ec);
}

FRAMEWORK_TEST(invalid_input_degrades_without_writing) {
    SkpFrameCapture bad(0, 32);
    CHECK(!bad.available());
    CHECK(bad.canvas() == nullptr);

    std::string blob;
    const auto mem = bad.finish_to_memory(blob);
    CHECK(!mem.ok);
    CHECK(!mem.reason.empty());
    CHECK(blob.empty());

    const std::string never = (fs::temp_directory_path() / "vellum-should-not-be-written.skp").string();
    const auto file = vellum::render::capture_skp_to_file(-1, -1, never, [](vellum::canvas::Canvas&) {});
    CHECK(!file.ok);
    CHECK(!file.reason.empty());
    CHECK(!fs::exists(never));

    SkpFrameCapture ok(16, 16);
    CHECK(ok.available());
    const auto empty_path = ok.finish_to_file("");
    CHECK(!empty_path.ok);
    CHECK(!empty_path.reason.empty());
}

} // namespace

int main() {
    return framework_test::run_all();
}
